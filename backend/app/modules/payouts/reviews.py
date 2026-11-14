"""Staff review of a payout held because it is large.

A payout above the review threshold in force starts held, with its money already reserved.

* Releasing it to be sent takes two people, from the owner's standing rule: one member of staff
  proposes the release with a reason, and a different one approves it, which queues the payout
  in the same transaction. The proposer may withdraw the proposal.
* Declining it is one person's step, as refusing a bank transfer is: the money goes back to where
  it came from and the client is told (`payout_declined`). It closes any open proposal.

Every step needs a reason. Proposing, approving and declining take an idempotency key: a retried
request returns what the first one did. Migration 0038 holds the two-person rule in the database.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.notifications import contract as notifications
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.payouts import IllegalTransition, PayoutEvent, PayoutStatus
from app.modules.payouts.domain.sending import destination_label
from app.modules.payouts.payouts import PayoutView
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id

PROPOSE_OPERATION = "payouts.propose_release"
APPROVE_OPERATION = "payouts.approve_release"
DECLINE_OPERATION = "payouts.decline_held"


class ReleaseStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class ReleaseView:
    id: str
    payout_id: str
    reason: str
    status: ReleaseStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None


@dataclass(frozen=True, slots=True)
class HeldPayout:
    payout: PayoutView
    # The open proposal to release it, if a first person has made one.
    release: ReleaseView | None


class ReviewRefused(Exception):
    """The step was not recorded. `code` is safe to show to staff."""

    def __init__(self, code: str, status: Literal["conflict", "forbidden"] = "conflict") -> None:
        super().__init__(code)
        self.code = code
        self.status = status


_COLUMNS = (
    "id, payout_id, reason, status, proposed_by, proposed_at, decided_by, decided_at, "
    "decision_reason"
)
_INSERT = text(
    "INSERT INTO payout_releases (id, payout_id, reason, status, proposed_by, proposed_at) "
    "VALUES (:id, :payout_id, :reason, 'proposed', :proposed_by, :now)"
)
_GET = text(f"SELECT {_COLUMNS} FROM payout_releases WHERE id = :id")  # noqa: S608
_LOCK = text(f"SELECT {_COLUMNS} FROM payout_releases WHERE id = :id FOR UPDATE")  # noqa: S608
_OPEN_FOR = text(
    f"SELECT {_COLUMNS} FROM payout_releases "  # noqa: S608 - constant column list
    "WHERE payout_id = :payout_id AND status = 'proposed' FOR UPDATE"
)
_DECIDE = text(
    "UPDATE payout_releases SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)


async def held(session: AsyncSession, *, limit: int = 100) -> list[HeldPayout]:
    """Held payouts, oldest first, each with its open proposal. Empty outside a staff
    transaction."""
    found: list[HeldPayout] = []
    for payout in await payouts.held_for_review(session, limit=limit):
        row = (await session.execute(_OPEN_FOR, {"payout_id": payout.id})).one_or_none()
        found.append(HeldPayout(payout, None if row is None else _view(row)))
    return found


async def propose_release(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> ReleaseView:
    """The first person proposes sending a held payout. A different person must approve it."""
    _check(staff, reason)

    async def command() -> Receipt:
        payout = await payouts.get_payout(session, payout_id)
        if payout.status is not PayoutStatus.HELD:
            raise ReviewRefused("payout_not_held")
        if (await session.execute(_OPEN_FOR, {"payout_id": payout_id})).first() is not None:
            raise ReviewRefused("release_already_proposed")
        release_id = push_id()
        await session.execute(
            _INSERT,
            {
                "id": release_id,
                "payout_id": payout_id,
                "reason": reason.strip(),
                "proposed_by": staff.id,
                "now": clock.now(),
            },
        )
        await _audit(session, staff, "payouts.release_proposed", payout_id, reason, clock)
        return Receipt(201, {"release_id": release_id})

    done = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={"payout_id": payout_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await get_release(session, str(done.receipt.response["release_id"]))


async def approve_release(
    session: AsyncSession,
    staff: Actor,
    release_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> PayoutView:
    """A second person agrees, and the payout is queued to be sent."""
    _check(staff, reason)

    async def command() -> Receipt:
        release = await _open(session, release_id)
        if release.proposed_by == staff.id:
            raise ReviewRefused("proposed_by_you", "forbidden")
        await _decide(session, release, ReleaseStatus.APPROVED, staff, reason, clock)
        moved = await _move(
            session, staff, release.payout_id, PayoutEvent.REVIEW_APPROVED, reason, clock
        )
        await _audit(session, staff, "payouts.release_approved", moved.id, reason, clock)
        return Receipt(200, {"payout_id": moved.id})

    done = await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"release_id": release_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await payouts.get_payout(session, str(done.receipt.response["payout_id"]))


async def withdraw_release(
    session: AsyncSession, staff: Actor, release_id: str, *, reason: str, clock: Clock
) -> ReleaseView:
    """The proposer takes their proposal back. The payout stays held."""
    _check(staff, reason)
    release = await _open(session, release_id)
    if release.proposed_by != staff.id:
        raise ReviewRefused("not_proposed_by_you", "forbidden")
    await _decide(session, release, ReleaseStatus.WITHDRAWN, staff, reason, clock)
    await _audit(session, staff, "payouts.release_withdrawn", release.payout_id, reason, clock)
    return await get_release(session, release_id)


async def decline(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> PayoutView:
    """Cancel a held payout. Its money goes back to the investment or balance it came from."""
    _check(staff, reason)

    async def command() -> Receipt:
        row = (await session.execute(_OPEN_FOR, {"payout_id": payout_id})).one_or_none()
        moved = await _move(session, staff, payout_id, PayoutEvent.REVIEW_DECLINED, reason, clock)
        if row is not None:
            # Whoever declines closes the proposal too: as its proposer, they withdraw it.
            release = _view(row)
            closed = (
                ReleaseStatus.WITHDRAWN
                if release.proposed_by == staff.id
                else ReleaseStatus.DECLINED
            )
            await _decide(session, release, closed, staff, reason, clock)
        await _tell_declined(session, moved, clock)
        return Receipt(200, {"payout_id": moved.id})

    done = await run_once(
        session,
        actor=staff,
        operation=DECLINE_OPERATION,
        key=idempotency_key,
        request={"payout_id": payout_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await payouts.get_payout(session, str(done.receipt.response["payout_id"]))


async def get_release(session: AsyncSession, release_id: str) -> ReleaseView:
    row = (await session.execute(_GET, {"id": release_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


def _check(staff: Actor, reason: str) -> None:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    if not reason.strip():
        raise ApplicationError("validation_failed")


async def _open(session: AsyncSession, release_id: str) -> ReleaseView:
    row = (await session.execute(_LOCK, {"id": release_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    release = _view(row)
    if release.status is not ReleaseStatus.PROPOSED:
        raise ReviewRefused("release_already_decided")
    return release


async def _move(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    event: PayoutEvent,
    reason: str,
    clock: Clock,
) -> PayoutView:
    payout = await payouts.get_payout(session, payout_id)
    if payout.status is not PayoutStatus.HELD:
        raise ReviewRefused("payout_not_held")
    try:
        return await payouts.apply_event(
            session, staff, payout_id, event, clock, reason=reason.strip()
        )
    except IllegalTransition:
        # Cancelled by the client or decided by someone else since it was read.
        raise ReviewRefused("payout_not_held") from None


async def _decide(
    session: AsyncSession,
    release: ReleaseView,
    status: ReleaseStatus,
    staff: Actor,
    reason: str,
    clock: Clock,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": release.id,
            "status": status.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
        },
    )


async def _audit(
    session: AsyncSession, staff: Actor, action: str, payout_id: str, reason: str, clock: Clock
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
        ),
        clock,
    )


async def _tell_declined(session: AsyncSession, payout: PayoutView, clock: Clock) -> None:
    """In the same transaction as the decline, so the client is told once and only if it holds."""
    destination = await destinations.get_destination(session, payout.destination_id)
    await notifications.notify(
        session,
        event_name="payout_declined",
        client_id=payout.client_id,
        idempotency_key=payout.id,
        variables={
            "amount": payout.amount,
            # Never the number itself.
            "destination": "your bank account"
            if destination.kind is DestinationKind.BANK
            else destination_label(destination.network or ""),
        },
        link=notifications.Link("payout", payout.id),
        now=clock.now(),
    )


def _view(row: Row[Any]) -> ReleaseView:
    return ReleaseView(
        id=row.id,
        payout_id=row.payout_id,
        reason=row.reason,
        status=ReleaseStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
    )
