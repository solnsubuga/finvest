"""Closing a client's account: the client asks, one member of staff prepares, another closes.

Every function works inside the caller's transaction, and the request, its move, the account's
new status and their audit events commit together or not at all. Before each step the client's
standing is read again from the modules that own it, so an investment started or a payment made
after the request still stops the closure. Migration 0232 holds the rules PostgreSQL enforces.

Closing moves no money: it is refused while Finvest owes the client anything.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.autoinvest.plans import live_plan_count
from app.modules.closures.domain.blockers import Blocker, Standing, blockers
from app.modules.goals.service import open_goal_count
from app.modules.identity import client_locks
from app.modules.investments.holdings import running_count
from app.modules.ledger.statements import client_owed_as_of
from app.modules.payments.service import open_deposit_count
from app.modules.payouts.payouts import open_payout_count
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

RESOURCE = "closure_request"
MAX_NOTE = 500
# Every ledger entry, including any dated ahead, counts towards what is owed.
_EVERYTHING = date(9999, 12, 31)
Status = Literal["requested", "prepared", "closed", "declined", "cancelled"]


@dataclass(frozen=True, slots=True)
class ClosureRequest:
    id: str
    client_id: str
    reason: str | None
    requested_at: datetime
    status: Status
    prepared_by: str | None
    prepared_by_name: str | None
    prepared_at: datetime | None
    prepared_note: str | None
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    cancelled_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status in ("requested", "prepared")


_SELECT = (
    "SELECT r.id, r.client_id, r.reason, r.requested_at, r.status, r.prepared_by, "
    "p.name AS prepared_by_name, r.prepared_at, r.prepared_note, r.decided_by, "
    "d.name AS decided_by_name, r.decided_at, r.decision_note, r.cancelled_at "
    "FROM closure_requests r LEFT JOIN identity_staff p ON p.id = r.prepared_by "
    "LEFT JOIN identity_staff d ON d.id = r.decided_by "
)
_BY_ID = text(_SELECT + "WHERE r.id = :id")
_LATEST = text(
    _SELECT + "WHERE r.client_id = :client_id ORDER BY r.requested_at DESC, r.id DESC LIMIT 1"
)
_OPEN = text(_SELECT + "WHERE r.status IN ('requested', 'prepared') ORDER BY r.requested_at, r.id")
_LOCK = text("SELECT status, client_id FROM closure_requests WHERE id = :id FOR UPDATE")


async def standing(session: AsyncSession, client_id: str) -> Standing:
    """The client's position, read from the modules that own each part of it."""
    owed: dict[Currency, int] = {}
    for item in await client_owed_as_of(session, client_id, _EVERYTHING):
        owed[item.owed.currency] = owed.get(item.owed.currency, 0) + item.owed.minor
    return Standing(
        running_investments=await running_count(session, client_id),
        owed=tuple(Money(minor, currency) for currency, minor in sorted(owed.items()) if minor),
        open_deposits=await open_deposit_count(session, client_id),
        open_payouts=await open_payout_count(session, client_id),
        open_goals=await open_goal_count(session, client_id),
        live_plans=await live_plan_count(session, client_id),
    )


async def latest(session: AsyncSession, client_id: str) -> ClosureRequest | None:
    """The client's most recent request, open or ended, or None if they never asked."""
    row = (await session.execute(_LATEST, {"client_id": client_id})).one_or_none()
    return _request(row) if row else None


async def open_requests(session: AsyncSession) -> list[ClosureRequest]:
    """Every request waiting for staff, oldest first."""
    return [_request(row) for row in (await session.execute(_OPEN)).all()]


async def get(session: AsyncSession, request_id: str) -> ClosureRequest:
    row = (await session.execute(_BY_ID, {"id": request_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _request(row)


async def ask(
    session: AsyncSession, client: Actor, reason: str | None, clock: Clock
) -> ClosureRequest:
    """The signed-in client asks to close their account. Refused while anything blocks it."""
    client_id = _person(client, "client")
    await _clear(session, client_id)
    cleaned = _note(reason, "reason", required=False)
    request_id = push_id(clock)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO closure_requests (id, client_id, reason, requested_at) "
                    "VALUES (:id, :client_id, :reason, :now)"
                ),
                {"id": request_id, "client_id": client_id, "reason": cleaned, "now": clock.now()},
            )
    except IntegrityError:
        # A request is already open.
        raise ApplicationError("conflict") from None
    await _audit(session, client, "closure.requested", request_id, client_id, None, clock)
    return await get(session, request_id)


async def cancel(
    session: AsyncSession, client: Actor, request_id: str, clock: Clock
) -> ClosureRequest:
    """The client changes their mind while their request is still open."""
    client_id = _person(client, "client")
    status, owner = await _locked(session, request_id)
    if owner != client_id:
        raise ApplicationError("not_found")
    _expect(status, "requested", "prepared")
    await session.execute(
        text(
            "UPDATE closure_requests SET status = 'cancelled', cancelled_at = :now WHERE id = :id"
        ),
        {"now": clock.now(), "id": request_id},
    )
    await _audit(session, client, "closure.cancelled", request_id, client_id, None, clock)
    return await get(session, request_id)


async def prepare(
    session: AsyncSession, staff: Actor, request_id: str, note: str, clock: Clock
) -> ClosureRequest:
    """Check a request and pass it for a second member of staff to close."""
    staff_id = _person(staff, "staff")
    cleaned = _note(note, "note", required=True)
    status, client_id = await _locked(session, request_id)
    _expect(status, "requested")
    await _account_active(session, client_id)
    await _clear(session, client_id)
    await session.execute(
        text(
            "UPDATE closure_requests SET status = 'prepared', prepared_by = :staff, "
            "prepared_at = :now, prepared_note = :note WHERE id = :id"
        ),
        {"staff": staff_id, "now": clock.now(), "note": cleaned, "id": request_id},
    )
    await _audit(session, staff, "closure.prepared", request_id, client_id, cleaned, clock)
    return await get(session, request_id)


async def approve(
    session: AsyncSession, staff: Actor, request_id: str, note: str, clock: Clock
) -> ClosureRequest:
    """Close the account of someone else's prepared request. Every session the client holds
    ends and they can never sign in again."""
    staff_id = _person(staff, "staff")
    cleaned = _note(note, "note", required=True)
    status, client_id = await _locked(session, request_id)
    _expect(status, "prepared")
    if (await get(session, request_id)).prepared_by == staff_id:
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    await _account_active(session, client_id)
    await _clear(session, client_id)
    now = clock.now()
    await session.execute(
        text(
            "UPDATE closure_requests SET status = 'closed', decided_by = :staff, "
            "decided_at = :now, decision_note = :note, decided_in = pg_current_xact_id() "
            "WHERE id = :id"
        ),
        {"staff": staff_id, "now": now, "note": cleaned, "id": request_id},
    )
    await _audit(session, staff, "closure.approved", request_id, client_id, cleaned, clock)
    await client_locks.close_account(session, request_id, client_id, now)
    return await get(session, request_id)


async def decline(
    session: AsyncSession, staff: Actor, request_id: str, note: str, clock: Clock
) -> ClosureRequest:
    """Turn a request down, saying why. The client sees the note and the account stays open."""
    staff_id = _person(staff, "staff")
    cleaned = _note(note, "note", required=True)
    status, client_id = await _locked(session, request_id)
    _expect(status, "requested", "prepared")
    await session.execute(
        text(
            "UPDATE closure_requests SET status = 'declined', decided_by = :staff, "
            "decided_at = :now, decision_note = :note WHERE id = :id"
        ),
        {"staff": staff_id, "now": clock.now(), "note": cleaned, "id": request_id},
    )
    await _audit(session, staff, "closure.declined", request_id, client_id, cleaned, clock)
    return await get(session, request_id)


# Internals ------------------------------------------------------------------------------------


async def _clear(session: AsyncSession, client_id: str) -> None:
    found = blockers(await standing(session, client_id))
    if found:
        raise ApplicationError(
            "conflict",
            fields=[FieldProblem(field_path=["blockers"], code=item.value) for item in found],
        )


async def _account_active(session: AsyncSession, client_id: str) -> None:
    account = await client_locks.client_account(session, client_id)
    if account is None or account.status != "active":
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["client"], code="not_active")]
        )


async def _locked(session: AsyncSession, request_id: str) -> tuple[str, str]:
    row = (await session.execute(_LOCK, {"id": request_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return str(row.status), str(row.client_id)


def _expect(status: str, *allowed: str) -> None:
    if status not in allowed:
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["status"], code=f"already_{status}")]
        )


def _person(actor: Actor, kind: Literal["client", "staff"]) -> str:
    if actor.kind != kind or actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


def _note(value: str | None, field: str, *, required: bool) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned and not required:
        return None
    if not cleaned or len(cleaned) > MAX_NOTE:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["body", field], code="required")]
        )
    return cleaned


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    request_id: str,
    client_id: str,
    reason: str | None,
    clock: Clock,
) -> None:
    assert actor.id is not None
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action in ("closure.approved", "closure.declined") else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=request_id,
            actor=AuditActor.client(actor.id)
            if actor.kind == "client"
            else AuditActor.staff(actor.id),
            reason=reason,
            details={"client_id": client_id},
        ),
        clock,
    )


def _request(row: Row[Any]) -> ClosureRequest:
    return ClosureRequest(
        id=row.id,
        client_id=row.client_id,
        reason=row.reason,
        requested_at=row.requested_at,
        status=row.status,
        prepared_by=row.prepared_by,
        prepared_by_name=row.prepared_by_name,
        prepared_at=row.prepared_at,
        prepared_note=row.prepared_note,
        decided_by=row.decided_by,
        decided_by_name=row.decided_by_name,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        cancelled_at=row.cancelled_at,
    )


__all__ = ["Blocker", "ClosureRequest", "Standing"]
