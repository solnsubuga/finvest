"""Two staff members settle a payout the provider never answered for, from the ioTec statement.

A sent or uncertain payout is read from the provider for `READ_WINDOW`. When the reads end with
no answer, the statement is the only evidence left:

* One member of staff proposes `paid`, naming the ioTec statement line that carries the payout's
  ID, going out, for its amount; or `failed`, naming an ioTec statement for a day at least
  `READ_WINDOW` after the send, when no ioTec line carries the payout's ID.
* A different member of staff approves it. In the same transaction the payout moves to that
  outcome and its ledger entry is posted: settled from the wallet if paid, parked in the
  client's "payout pending" account if failed. The client is told, as when the provider answers.
* They may decline it with a reason, and the proposer may withdraw it, so it can be proposed
  again.

Migration 0037 holds every rule here in the database too, including the evidence.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.payouts import destinations, jobs, payouts
from app.modules.payouts.domain.payouts import PayoutEvent, needs_status_read
from app.modules.payouts.domain.sending import READ_WINDOW
from app.modules.payouts.payouts import PayoutView
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import Direction, StatementSource
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorCode, FieldProblem
from app.shared.push_ids import push_id


class Outcome(StrEnum):
    PAID = "paid"
    FAILED = "failed"


class ResolutionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


PROPOSE_OPERATION = "payouts.propose_resolution"
APPROVE_OPERATION = "payouts.approve_resolution"

_EVENTS = {
    Outcome.PAID: PayoutEvent.STAFF_RESOLVED_PAID,
    Outcome.FAILED: PayoutEvent.STAFF_RESOLVED_FAILED,
}


@dataclass(frozen=True, slots=True)
class ResolutionView:
    id: str
    payout_id: str
    outcome: Outcome
    # The evidence: a line for paid, a statement for failed.
    statement_line_id: str | None
    statement_id: str | None
    reason: str
    status: ResolutionStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None


@dataclass(frozen=True, slots=True)
class StrandedPayout:
    """A payout the provider was read for until the window closed, with no answer."""

    payout: PayoutView
    first_sent_at: datetime
    # The open proposal for it, if a first person has made one.
    resolution: ResolutionView | None


class ResolutionRefused(ApplicationError):
    def __init__(self, code: str, *, status: ErrorCode = "conflict", field: str = "body") -> None:
        super().__init__(status, fields=[FieldProblem(field_path=["body", field], code=code)])
        self.refusal = code


_COLUMNS = (
    "id, payout_id, outcome, statement_line_id, statement_id, reason, status, proposed_by, "
    "proposed_at, decided_by, decided_at, decision_reason"
)
_INSERT = text(
    "INSERT INTO payout_resolutions (id, payout_id, outcome, statement_line_id, statement_id, "
    "reason, status, proposed_by, proposed_at) VALUES (:id, :payout_id, :outcome, :line, "
    ":statement, :reason, 'proposed', :proposed_by, :now)"
)
_GET = text(f"SELECT {_COLUMNS} FROM payout_resolutions WHERE id = :id")  # noqa: S608
_LOCK = text(f"SELECT {_COLUMNS} FROM payout_resolutions WHERE id = :id FOR UPDATE")  # noqa: S608
_OPEN_FOR = text(
    f"SELECT {_COLUMNS} FROM payout_resolutions "  # noqa: S608 - constant column list
    "WHERE payout_id = :payout_id AND status = 'proposed'"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM payout_resolutions "  # noqa: S608 - constant column list
    "WHERE status = 'proposed' ORDER BY proposed_at, id"
)
_DECIDE = text(
    "UPDATE payout_resolutions SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)
_FIRST_SENT = text(
    "SELECT min(started_at) FROM payout_send_attempts "
    "WHERE payout_id = :payout_id AND (outcome IS NULL OR outcome <> 'deferred')"
)
_STRANDED = text(
    "SELECT p.id, min(a.started_at) AS first_sent_at FROM payouts p "
    "JOIN payout_send_attempts a ON a.payout_id = p.id "
    "AND (a.outcome IS NULL OR a.outcome <> 'deferred') "
    "WHERE p.status IN ('sent', 'uncertain') GROUP BY p.id "
    "HAVING min(a.started_at) <= :window_closed ORDER BY min(a.started_at), p.id LIMIT :limit"
)


async def propose(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    *,
    outcome: Outcome,
    statement_line_id: str | None = None,
    statement_id: str | None = None,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> ResolutionView:
    """The first person's word on how a stranded payout ended, with the statement as evidence.

    The same key with the same request returns the first proposal, not a second one.
    """
    _staff_only(staff)
    if not reason.strip():
        raise ResolutionRefused("reason_required", status="validation_failed", field="reason")
    now = clock.now()

    async def command() -> Receipt:
        view = await _propose(
            session, staff, payout_id, outcome, statement_line_id, statement_id, reason, clock
        )
        return Receipt(201, {"resolution_id": view.id})

    outcome_of = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={
            "payout_id": payout_id,
            "outcome": outcome.value,
            "statement_line_id": statement_line_id,
            "statement_id": statement_id,
            "reason": reason.strip(),
        },
        now=now,
        command=command,
    )
    return await get_resolution(session, str(outcome_of.receipt.response["resolution_id"]))


async def _propose(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    outcome: Outcome,
    statement_line_id: str | None,
    statement_id: str | None,
    reason: str,
    clock: Clock,
) -> ResolutionView:
    now = clock.now()
    payout = await payouts.get_payout(session, payout_id)
    first_sent = await _stranded_since(session, payout, now)
    await _check_evidence(session, payout, first_sent, outcome, statement_line_id, statement_id)
    if (await session.execute(_OPEN_FOR, {"payout_id": payout_id})).first() is not None:
        raise ResolutionRefused("resolution_already_proposed")
    resolution_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": resolution_id,
            "payout_id": payout_id,
            "outcome": outcome.value,
            "line": statement_line_id if outcome is Outcome.PAID else None,
            "statement": statement_id if outcome is Outcome.FAILED else None,
            "reason": reason.strip(),
            "proposed_by": staff.id,
            "now": now,
        },
    )
    await _audit(session, staff, "payouts.resolution_proposed", payout_id, clock, reason, outcome)
    return await get_resolution(session, resolution_id)


async def approve(
    session: AsyncSession,
    staff: Actor,
    resolution_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> PayoutView:
    """A second person agrees. The payout moves to the outcome and its entry is posted.

    The same key with the same request returns the payout as the first approval left it.
    """
    _staff_only(staff)

    async def command() -> Receipt:
        moved = await _approve(session, staff, resolution_id, reason, clock)
        return Receipt(200, {"payout_id": moved.id})

    done = await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"resolution_id": resolution_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await payouts.get_payout(session, str(done.receipt.response["payout_id"]))


async def _approve(
    session: AsyncSession, staff: Actor, resolution_id: str, reason: str, clock: Clock
) -> PayoutView:
    resolution = await _decidable(session, staff, resolution_id, reason)
    if resolution.proposed_by == staff.id:
        raise ResolutionRefused("proposed_by_you", status="forbidden")
    payout = await payouts.get_payout(session, resolution.payout_id)
    now = clock.now()
    first_sent = await _stranded_since(session, payout, resolution.proposed_at)
    await _check_evidence(
        session,
        payout,
        first_sent,
        resolution.outcome,
        resolution.statement_line_id,
        resolution.statement_id,
    )
    await _decide(session, resolution_id, ResolutionStatus.APPROVED, staff, reason, now)
    moved = await payouts.settle_by_hand(
        session, staff, payout.id, _EVENTS[resolution.outcome], clock
    )
    await _audit(
        session, staff, "payouts.resolution_approved", payout.id, clock, reason, resolution.outcome
    )
    await jobs.tell_client(session, moved, now)
    return moved


async def decline(
    session: AsyncSession, staff: Actor, resolution_id: str, *, reason: str, clock: Clock
) -> ResolutionView:
    """A second person disagrees. The payout stays as it is, ready for a new proposal."""
    resolution = await _decidable(session, staff, resolution_id, reason)
    if resolution.proposed_by == staff.id:
        raise ResolutionRefused("proposed_by_you", status="forbidden")
    await _decide(session, resolution_id, ResolutionStatus.DECLINED, staff, reason, clock.now())
    await _audit(
        session,
        staff,
        "payouts.resolution_declined",
        resolution.payout_id,
        clock,
        reason,
        resolution.outcome,
    )
    return await get_resolution(session, resolution_id)


async def withdraw(
    session: AsyncSession, staff: Actor, resolution_id: str, *, reason: str, clock: Clock
) -> ResolutionView:
    """The proposer takes their proposal back."""
    resolution = await _decidable(session, staff, resolution_id, reason)
    if resolution.proposed_by != staff.id:
        raise ResolutionRefused("not_proposed_by_you", status="forbidden")
    await _decide(session, resolution_id, ResolutionStatus.WITHDRAWN, staff, reason, clock.now())
    await _audit(
        session,
        staff,
        "payouts.resolution_withdrawn",
        resolution.payout_id,
        clock,
        reason,
        resolution.outcome,
    )
    return await get_resolution(session, resolution_id)


async def get_resolution(session: AsyncSession, resolution_id: str) -> ResolutionView:
    row = (await session.execute(_GET, {"id": resolution_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def waiting(session: AsyncSession) -> list[ResolutionView]:
    """Proposals waiting for a second person, oldest first. Empty outside a staff transaction."""
    return [_view(row) for row in (await session.execute(_WAITING)).all()]


async def stranded(
    session: AsyncSession, now: datetime, *, limit: int = 100
) -> list[StrandedPayout]:
    """Payouts whose reads have ended with no answer, longest first, with any open proposal.

    Empty outside a staff transaction.
    """
    rows = (
        await session.execute(
            _STRANDED, {"window_closed": now - READ_WINDOW, "limit": max(1, min(limit, 100))}
        )
    ).all()
    found: list[StrandedPayout] = []
    for row in rows:
        payout = await payouts.get_payout(session, row.id)
        open_row = (await session.execute(_OPEN_FOR, {"payout_id": row.id})).one_or_none()
        found.append(
            StrandedPayout(payout, row.first_sent_at, None if open_row is None else _view(open_row))
        )
    return found


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


async def _stranded_since(session: AsyncSession, payout: PayoutView, at: datetime) -> datetime:
    """When the payout was first sent, if its reads have ended by `at`."""
    if not needs_status_read(payout.status):
        raise ResolutionRefused("payout_not_stranded")
    first_sent = await session.scalar(_FIRST_SENT, {"payout_id": payout.id})
    if first_sent is None or first_sent > at - READ_WINDOW:
        # While the provider is still being read, its answer is the one that counts.
        raise ResolutionRefused("provider_still_being_read")
    first: datetime = first_sent
    return first


async def _check_evidence(
    session: AsyncSession,
    payout: PayoutView,
    first_sent: datetime,
    outcome: Outcome,
    statement_line_id: str | None,
    statement_id: str | None,
) -> None:
    """What the ioTec statement for the wallet the payout left from shows.

    `failed` needs that wallet's statement for every day from the send to the cited one: a
    line on a day nobody imported would otherwise let a paid payout be settled as failed, and
    the client paid twice.
    """
    sent_on = business_date(first_sent)
    wallet = (await destinations.get_destination(session, payout.destination_id)).network or ""
    on_statement = await statements.find_statement_lines(
        session,
        source=StatementSource.IOTEC,
        date_from=date.min,
        date_to=date.max,
        reference=payout.id,
    )
    if outcome is Outcome.PAID:
        if not statement_line_id or statement_id:
            raise ResolutionRefused(
                "statement_line_required", status="validation_failed", field="statement_line_id"
            )
        line = next((each for each in on_statement if each.id == statement_line_id), None)
        if (
            line is None
            or line.account_reference != wallet
            or line.direction is not Direction.OUT
            or line.amount != payout.amount
        ):
            raise ResolutionRefused("statement_line_not_this_payout", field="statement_line_id")
        return
    if not statement_id or statement_line_id:
        raise ResolutionRefused(
            "statement_required", status="validation_failed", field="statement_id"
        )
    if on_statement:
        # The payout's ID is on a statement, so it did not simply fail. A person looks at the line.
        raise ResolutionRefused("payout_on_statement", field="statement_id")
    statement = await statements.get_statement_by_id(session, statement_id)
    if (
        statement is None
        or statement.source is not StatementSource.IOTEC
        or statement.account_reference != wallet
        or statement.closing_balance.currency is not payout.amount.currency
    ):
        raise ResolutionRefused("statement_not_this_wallet", field="statement_id")
    if statement.business_date < sent_on + timedelta(days=READ_WINDOW.days):
        raise ResolutionRefused("statement_too_early", field="statement_id")
    imported = await statements.statement_days(
        session,
        source=StatementSource.IOTEC,
        account_reference=wallet,
        currency=payout.amount.currency,
        date_from=sent_on,
        date_to=statement.business_date,
    )
    if len(imported) != (statement.business_date - sent_on).days + 1:
        raise ResolutionRefused("statement_day_missing", field="statement_id")


async def _decidable(
    session: AsyncSession, staff: Actor, resolution_id: str, reason: str
) -> ResolutionView:
    _staff_only(staff)
    if not reason.strip():
        raise ResolutionRefused("reason_required", status="validation_failed", field="reason")
    row = (await session.execute(_LOCK, {"id": resolution_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    resolution = _view(row)
    if resolution.status is not ResolutionStatus.PROPOSED:
        raise ResolutionRefused("resolution_already_decided")
    return resolution


async def _decide(
    session: AsyncSession,
    resolution_id: str,
    status: ResolutionStatus,
    staff: Actor,
    reason: str,
    now: datetime,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": resolution_id,
            "status": status.value,
            "decided_by": staff.id,
            "now": now,
            "reason": reason.strip(),
        },
    )


async def _audit(
    session: AsyncSession,
    staff: Actor,
    action: str,
    payout_id: str,
    clock: Clock,
    reason: str,
    outcome: Outcome,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval",
            action=action,
            resource_type="payout",
            resource_id=payout_id,
            actor=AuditActor(staff.kind, staff.id),
            reason=reason.strip(),
            details={"outcome": outcome.value},
        ),
        clock,
    )


def _view(row: Row[Any]) -> ResolutionView:
    return ResolutionView(
        id=row.id,
        payout_id=row.payout_id,
        outcome=Outcome(row.outcome),
        statement_line_id=row.statement_line_id,
        statement_id=row.statement_id,
        reason=row.reason,
        status=ResolutionStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
    )
