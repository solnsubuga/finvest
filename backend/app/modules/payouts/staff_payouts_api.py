"""Staff routes for payouts that need a person to look at them.

`GET /staff/payouts/stranded` lists sent or uncertain payouts whose status reads ended with no
answer, and the `resolutions` routes let two staff members settle one from the ioTec statement:
one proposes, a different one approves. Seeing them needs `reconciliation.view`, and every step
of a settlement needs `reconciliation.change`, as the day close does.

`GET /staff/payouts/held-up` lists queued mobile money payouts the provider has taken nothing of
since their first try `HELD_UP_AFTER` or more ago. The sweep keeps sending them and never gives
up: a client's money is in flight. This is where a person sees that it is not moving, so they can
find out why from the provider. Seeing it needs `money_movement.view`.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import payouts, resolutions
from app.modules.payouts.api import MoneyView
from app.modules.payouts.payouts import PayoutView
from app.modules.payouts.resolutions import Outcome, ResolutionView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

staff_router = APIRouter(prefix="/staff", tags=["staff payouts"], responses=_REFUSALS)
routers = [staff_router]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
Viewer = Annotated[StaffSession, Depends(require_permission("money_movement.view"))]
Watcher = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
Settler = Annotated[StaffSession, Depends(require_permission("reconciliation.change"))]
# Approving needs a passkey or app code from the last few minutes.
Approver = Annotated[StaffSession, Depends(require_confirmed("reconciliation.change"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class HeldUpPayoutOut(Contract):
    """A queued payout first tried two hours or more ago, and taken by nothing since."""

    id: PushId
    client_id: PushId
    kind: Literal["mobile_money"]
    amount: MoneyView
    created_at: datetime
    first_tried_at: datetime
    last_tried_at: datetime
    # Sends tried so far. Each one ended with the provider saying it took nothing.
    tries: int


@staff_router.get("/payouts/held-up")
async def held_up(staff: Viewer, database: Db, clock: Time) -> list[HeldUpPayoutOut]:
    """Payouts held up at the provider, longest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await payouts.held_up(session, clock.now(), limit=100)
    return [
        HeldUpPayoutOut(
            id=item.payout.id,
            client_id=item.payout.client_id,
            kind="mobile_money",
            amount=MoneyView(
                minor=item.payout.amount.minor, currency=item.payout.amount.currency.value
            ),
            created_at=item.payout.created_at,
            first_tried_at=item.first_tried_at,
            last_tried_at=item.last_tried_at,
            tries=item.tries,
        )
        for item in found
    ]


class ResolutionOut(Contract):
    id: PushId
    payout_id: PushId
    outcome: Literal["paid", "failed"]
    statement_line_id: PushId | None
    statement_id: PushId | None
    reason: str
    status: Literal["proposed", "approved", "declined", "withdrawn"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None


class StrandedPayoutOut(Contract):
    """A payout the provider was read for until the window closed, with no answer."""

    id: PushId
    client_id: PushId
    status: Literal["sent", "uncertain"]
    amount: MoneyView
    first_sent_at: datetime
    # The open proposal, if a first person has made one. It waits for a different person.
    resolution: ResolutionOut | None


class ProposeIn(Contract):
    """`paid` names the ioTec statement line that carries the payout's ID; `failed` names an
    ioTec statement for a day at least three days after the send."""

    outcome: Literal["paid", "failed"]
    statement_line_id: PushId | None = None
    statement_id: PushId | None = None
    reason: Reason


class DecideIn(Contract):
    reason: Reason


class SettledPayoutOut(Contract):
    id: PushId
    status: Literal["paid", "failed"]


def _resolution(view: ResolutionView) -> ResolutionOut:
    return ResolutionOut(
        id=view.id,
        payout_id=view.payout_id,
        outcome=view.outcome.value,
        statement_line_id=view.statement_line_id,
        statement_id=view.statement_id,
        reason=view.reason,
        status=view.status.value,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
    )


def _amount(payout: PayoutView) -> MoneyView:
    return MoneyView(minor=payout.amount.minor, currency=payout.amount.currency.value)


@staff_router.get("/payouts/stranded")
async def stranded(staff: Watcher, database: Db, clock: Time) -> list[StrandedPayoutOut]:
    """Payouts whose reads ended with no answer, longest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await resolutions.stranded(session, clock.now())
    return [
        StrandedPayoutOut(
            id=item.payout.id,
            client_id=item.payout.client_id,
            status=item.payout.status.value,  # type: ignore[arg-type]
            amount=_amount(item.payout),
            first_sent_at=item.first_sent_at,
            resolution=None if item.resolution is None else _resolution(item.resolution),
        )
        for item in found
    ]


@staff_router.post("/payouts/{payout_id}/resolutions", status_code=201)
async def propose_resolution(
    payout_id: PushId,
    body: ProposeIn,
    staff: Settler,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ResolutionOut:
    """The first person proposes how the payout ended. A different person must approve it."""
    async with database.actor_transaction(staff.actor) as session:
        view = await resolutions.propose(
            session,
            staff.actor,
            payout_id,
            outcome=Outcome(body.outcome),
            statement_line_id=body.statement_line_id,
            statement_id=body.statement_id,
            reason=body.reason,
            idempotency_key=idempotency_key,
            clock=clock,
        )
    return _resolution(view)


@staff_router.post("/payout-resolutions/{resolution_id}/approve")
async def approve_resolution(
    resolution_id: PushId,
    body: DecideIn,
    staff: Approver,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> SettledPayoutOut:
    """A different person approves. The payout moves to the outcome and its entry is posted."""
    async with database.actor_transaction(staff.actor) as session:
        moved = await resolutions.approve(
            session,
            staff.actor,
            resolution_id,
            reason=body.reason,
            idempotency_key=idempotency_key,
            clock=clock,
        )
    return SettledPayoutOut(id=moved.id, status=moved.status.value)  # type: ignore[arg-type]


@staff_router.post("/payout-resolutions/{resolution_id}/decline")
async def decline_resolution(
    resolution_id: PushId, body: DecideIn, staff: Settler, database: Db, clock: Time
) -> ResolutionOut:
    """A different person declines. The payout stays stranded, ready for a new proposal."""
    async with database.actor_transaction(staff.actor) as session:
        view = await resolutions.decline(
            session, staff.actor, resolution_id, reason=body.reason, clock=clock
        )
    return _resolution(view)


@staff_router.post("/payout-resolutions/{resolution_id}/withdraw")
async def withdraw_resolution(
    resolution_id: PushId, body: DecideIn, staff: Settler, database: Db, clock: Time
) -> ResolutionOut:
    """The proposer takes their proposal back."""
    async with database.actor_transaction(staff.actor) as session:
        view = await resolutions.withdraw(
            session, staff.actor, resolution_id, reason=body.reason, clock=clock
        )
    return _resolution(view)
