"""Closing the month on the Finance screen: one person proposes it with the books as they
stand, a different person approves it.

The close reads the ledger through `app.modules.ledger.statements` and `closing`, and moves
nothing. Approving reads the books again and refuses if they differ from the ones proposed, so
the month signed is the month shown. Every step is on the audit trail.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, cast

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.finance.domain.books import month_end, month_label
from app.modules.finance.domain.month_close import (
    CannotClose,
    SignedLine,
    check_can_close,
    digest,
    ordered,
)
from app.modules.identity.service import StaffSession
from app.modules.ledger import closing, statements
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorCode, FieldProblem
from app.shared.money import Currency
from app.shared.push_ids import push_id

CloseStatus = Literal["proposed", "approved", "declined", "withdrawn"]
PROPOSE_OPERATION = "finance.propose_month_close"
APPROVE_OPERATION = "finance.approve_month_close"
RECENT = 12


class CloseRefused(ApplicationError):
    def __init__(self, code: str, *, status: ErrorCode = "conflict", field: str = "body") -> None:
        path: list[str | int] = ["body"] if field == "body" else ["body", field]
        super().__init__(status, fields=[FieldProblem(field_path=path, code=code)])
        self.refusal = code


@dataclass(frozen=True, slots=True)
class MonthCloseView:
    id: str
    month: date
    books: tuple[SignedLine, ...]
    books_sha256: str
    reason: str
    status: CloseStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None


_COLUMNS = (
    "id, month, books, books_sha256, reason, status, proposed_by, proposed_at, decided_by, "
    "decided_at, decision_reason"
)
_GET = text(f"SELECT {_COLUMNS} FROM finance_month_closes WHERE id = :id")  # noqa: S608
_GET_FOR_UPDATE = text(
    f"SELECT {_COLUMNS} FROM finance_month_closes WHERE id = :id FOR UPDATE"  # noqa: S608
)
_RECENT = text(
    f"SELECT {_COLUMNS} FROM finance_month_closes "  # noqa: S608
    "ORDER BY month DESC, proposed_at DESC, id LIMIT :limit"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM finance_month_closes WHERE status = 'proposed' "  # noqa: S608
    "ORDER BY proposed_at, id"
)
_LAST_CLOSED = text("SELECT max(month) FROM finance_month_closes WHERE status = 'approved'")
_INSERT = text(
    "INSERT INTO finance_month_closes (id, month, books, books_sha256, reason, status, "
    "proposed_by, proposed_at) VALUES (:id, :month, CAST(:books AS jsonb), :sha, :reason, "
    "'proposed', :by, :now)"
)
_DECIDE = text(
    "UPDATE finance_month_closes SET status = :status, decided_by = :by, decided_at = :now, "
    "decision_reason = :reason WHERE id = :id AND status = 'proposed'"
)
# One close of this kind at a time, so two people proposing at once do not both pass the
# order check.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended('finvest.finance.month_close', 0))")


def _view(row: Row[tuple[object, ...]]) -> MonthCloseView:
    data = row._mapping
    raw = data["books"]
    lines = json.loads(raw) if isinstance(raw, str) else raw
    return MonthCloseView(
        id=cast(str, data["id"]),
        month=cast(date, data["month"]),
        books=tuple(
            SignedLine(
                currency=str(line["currency"]),
                code=str(line["code"]),
                kind=str(line["kind"]),
                opening=int(line["opening"]),
                debits=int(line["debits"]),
                credits=int(line["credits"]),
                closing=int(line["closing"]),
            )
            for line in cast(list[dict[str, Any]], lines)
        ),
        books_sha256=cast(str, data["books_sha256"]),
        reason=cast(str, data["reason"]),
        status=cast(CloseStatus, data["status"]),
        proposed_by=cast(str, data["proposed_by"]),
        proposed_at=cast(datetime, data["proposed_at"]),
        decided_by=cast(str | None, data["decided_by"]),
        decided_at=cast(datetime | None, data["decided_at"]),
        decision_reason=cast(str | None, data["decision_reason"]),
    )


async def get_close(session: AsyncSession, close_id: str) -> MonthCloseView:
    row = (await session.execute(_GET, {"id": close_id})).first()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def recent(session: AsyncSession, limit: int = RECENT) -> list[MonthCloseView]:
    """The latest proposals and closes, newest month first."""
    return [_view(row) for row in (await session.execute(_RECENT, {"limit": limit})).all()]


async def waiting(session: AsyncSession) -> list[MonthCloseView]:
    """Closes waiting for a second person, for the approvals inbox."""
    return [_view(row) for row in (await session.execute(_WAITING)).all()]


async def last_closed_month(session: AsyncSession) -> date | None:
    found: date | None = (await session.execute(_LAST_CLOSED)).scalar()
    return found


def next_month(after: date | None, today: date) -> date:
    """The month the Finance screen offers to close: the one after the last closed, or last
    month when none is closed yet."""
    if after is None:
        last = today.replace(day=1)
        return date(last.year - (last.month == 1), (last.month - 2) % 12 + 1, 1)
    return date(after.year + after.month // 12, after.month % 12 + 1, 1)


async def books_for(session: AsyncSession, month: date) -> tuple[SignedLine, ...]:
    """Every account code's month in every currency, as the ledger has it."""
    lines: list[SignedLine] = []
    for currency in Currency:
        for total in await statements.totals_by_code(
            session, currency, date_from=month, date_to=month_end(month)
        ):
            lines.append(
                SignedLine(
                    currency=currency.value,
                    code=total.code,
                    kind=total.kind,
                    opening=total.opening.minor,
                    debits=total.debits.minor,
                    credits=total.credits.minor,
                    closing=total.closing.minor,
                )
            )
    return ordered(lines)


@dataclass(frozen=True, slots=True)
class Readiness:
    """Whether the month can be closed now, and if not, why."""

    month: date
    closed_through: date | None
    refusal: str | None


async def readiness(session: AsyncSession, month: date, today: date) -> Readiness:
    closed = await closing.closed_through(session)
    try:
        check_can_close(
            month,
            today=today,
            closed_through=closed,
            last_closed_month=await last_closed_month(session),
            lines=await books_for(session, month)
            if closed is not None and closed >= month_end(month)
            else (),
        )
    except CannotClose as refused:
        return Readiness(month, closed, refused.code)
    return Readiness(month, closed, None)


async def _check(session: AsyncSession, month: date, today: date) -> tuple[SignedLine, ...]:
    closed = await closing.closed_through(session)
    lines = await books_for(session, month) if closed is not None else ()
    try:
        check_can_close(
            month,
            today=today,
            closed_through=closed,
            last_closed_month=await last_closed_month(session),
            lines=lines,
        )
    except CannotClose as refused:
        raise CloseRefused(refused.code) from None
    return lines


def _clean(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned:
        raise CloseRefused("reason_required", status="validation_failed", field="reason")
    return cleaned


async def propose(
    session: AsyncSession,
    staff: StaffSession,
    month: date,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> MonthCloseView:
    """The first person proposes closing the month, with its books as they stand."""
    cleaned = _clean(reason)
    now = clock.now()

    async def command() -> Receipt:
        await session.execute(_LOCK)
        if await waiting(session):
            raise CloseRefused("close_already_proposed")
        lines = await _check(session, month, business_date(now))
        close_id = push_id()
        await session.execute(
            _INSERT,
            {
                "id": close_id,
                "month": month,
                "books": json.dumps([line.as_json() for line in lines]),
                "sha": digest(month, lines),
                "reason": cleaned,
                "by": staff.staff_id,
                "now": now,
            },
        )
        await _audit(
            session, staff, "finance.month_close_proposed", close_id, month, clock, cleaned
        )
        return Receipt(201, {"close_id": close_id})

    outcome = await run_once(
        session,
        actor=staff.actor,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={"month": month_label(month), "reason": cleaned},
        now=now,
        command=command,
    )
    return await get_close(session, str(outcome.receipt.response["close_id"]))


async def _proposed(session: AsyncSession, close_id: str) -> MonthCloseView:
    row = (await session.execute(_GET_FOR_UPDATE, {"id": close_id})).first()
    if row is None:
        raise ApplicationError("not_found")
    found = _view(row)
    if found.status != "proposed":
        raise CloseRefused("not_waiting")
    return found


async def approve(
    session: AsyncSession,
    staff: StaffSession,
    close_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> MonthCloseView:
    """A different person approves, which closes the month. The books are read again and must
    be the ones proposed."""
    cleaned = _clean(reason)
    now = clock.now()

    async def command() -> Receipt:
        await session.execute(_LOCK)
        found = await _proposed(session, close_id)
        if found.proposed_by == staff.staff_id:
            raise CloseRefused("proposed_by_you", status="forbidden")
        lines = await _check(session, found.month, business_date(now))
        if digest(found.month, lines) != found.books_sha256:
            raise CloseRefused("books_changed")
        await session.execute(
            _DECIDE,
            {
                "id": close_id,
                "status": "approved",
                "by": staff.staff_id,
                "now": now,
                "reason": cleaned,
            },
        )
        await _audit(session, staff, "finance.month_closed", close_id, found.month, clock, cleaned)
        return Receipt(200, {"close_id": close_id})

    await run_once(
        session,
        actor=staff.actor,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"close_id": close_id, "reason": cleaned},
        now=now,
        command=command,
    )
    return await get_close(session, close_id)


async def decline(
    session: AsyncSession, staff: StaffSession, close_id: str, *, reason: str, clock: Clock
) -> MonthCloseView:
    """A different person sends it back. The month stays open for a new proposal."""
    cleaned = _clean(reason)
    found = await _proposed(session, close_id)
    if found.proposed_by == staff.staff_id:
        raise CloseRefused("proposed_by_you", status="forbidden")
    await _decide(session, staff, found, "declined", cleaned, clock)
    return await get_close(session, close_id)


async def withdraw(
    session: AsyncSession, staff: StaffSession, close_id: str, *, reason: str, clock: Clock
) -> MonthCloseView:
    """The proposer takes it back."""
    cleaned = _clean(reason)
    found = await _proposed(session, close_id)
    if found.proposed_by != staff.staff_id:
        raise CloseRefused("not_proposed_by_you", status="forbidden")
    await _decide(session, staff, found, "withdrawn", cleaned, clock)
    return await get_close(session, close_id)


async def _decide(
    session: AsyncSession,
    staff: StaffSession,
    found: MonthCloseView,
    status: CloseStatus,
    reason: str,
    clock: Clock,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": found.id,
            "status": status,
            "by": staff.staff_id,
            "now": clock.now(),
            "reason": reason,
        },
    )
    await _audit(
        session, staff, f"finance.month_close_{status}", found.id, found.month, clock, reason
    )


async def _audit(
    session: AsyncSession,
    staff: StaffSession,
    action: str,
    close_id: str,
    month: date,
    clock: Clock,
    reason: str,
) -> None:
    decision = action.endswith(("closed", "declined"))
    await audit.record(
        session,
        AuditEvent(
            category="approval" if decision else "change",
            action=action,
            resource_type="finance_month_close",
            resource_id=close_id,
            actor=AuditActor.staff(staff.staff_id),
            reason=reason,
            details={"month": month_label(month)},
        ),
        clock,
    )
