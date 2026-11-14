"""Staff routes for payouts held because they are large.

`GET /staff/payouts/held` is part of the review queue (`review_queue.view`), with any open
proposal to release each one. Releasing takes two people with `payout_accounts.change`: one
proposes with `POST /staff/payouts/{id}/release`, a different one approves with
`POST /staff/payout-releases/{id}/approve` after a second step in the last few minutes, and the
proposer may withdraw. Declining is one person's step, also behind the second step. Every step
needs a reason; proposing, approving and declining need an `Idempotency-Key`.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import reviews
from app.modules.payouts.api import MoneyView
from app.modules.payouts.payouts import PayoutView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem

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
Viewer = Annotated[StaffSession, Depends(require_permission("review_queue.view"))]
Proposer = Annotated[StaffSession, Depends(require_permission("payout_accounts.change"))]
# Approving and declining need a passkey or app code from the last few minutes.
Reviewer = Annotated[StaffSession, Depends(require_confirmed("payout_accounts.change"))]


class ReleaseOut(Contract):
    """A proposal to send a held payout. It waits for a different person to approve it."""

    id: PushId
    payout_id: PushId
    reason: str
    status: Literal["proposed", "approved", "declined", "withdrawn"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None


class HeldPayoutOut(Contract):
    """A payout waiting for a member of staff, because it is above the review threshold."""

    id: PushId
    client_id: PushId
    destination_id: PushId
    kind: Literal["mobile_money", "bank"]
    amount: MoneyView
    business_date: date
    created_at: datetime
    # The open proposal to release it, if a first person has made one.
    release: ReleaseOut | None


class ReviewIn(Contract):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class ReviewedPayoutOut(Contract):
    """The decision, and where the payout is now. An approved payout is `queued` to be sent, and
    may have moved on by the time a repeated request is answered; a declined one is `cancelled`
    with its money back."""

    id: PushId
    decision: Literal["approved", "declined"]
    status: Literal["queued", "sent", "uncertain", "paid", "failed", "cancelled"]


def _release(view: reviews.ReleaseView) -> ReleaseOut:
    return ReleaseOut(
        id=view.id,
        payout_id=view.payout_id,
        reason=view.reason,
        status=view.status.value,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
    )


def _held(item: reviews.HeldPayout) -> HeldPayoutOut:
    payout = item.payout
    return HeldPayoutOut(
        id=payout.id,
        client_id=payout.client_id,
        destination_id=payout.destination_id,
        kind=payout.kind.value,
        amount=MoneyView(minor=payout.amount.minor, currency=payout.amount.currency.value),
        business_date=payout.business_date,
        created_at=payout.created_at,
        release=None if item.release is None else _release(item.release),
    )


def _reviewed(payout: PayoutView, decision: Literal["approved", "declined"]) -> ReviewedPayoutOut:
    status = payout.status.value
    assert status != "held", "a decided payout is never held"
    return ReviewedPayoutOut(id=payout.id, decision=decision, status=status)  # type: ignore[arg-type]


def _refused(error: reviews.ReviewRefused) -> ApplicationError:
    return ApplicationError(
        error.status, fields=[FieldProblem(field_path=["body"], code=error.code)]
    )


@staff_router.get("/payouts/held")
async def held(staff: Viewer, database: Db) -> list[HeldPayoutOut]:
    """Payouts held for review, oldest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await reviews.held(session, limit=100)
    return [_held(item) for item in found]


@staff_router.post("/payouts/{payout_id}/release", status_code=201)
async def propose_release(
    payout_id: PushId,
    body: ReviewIn,
    staff: Proposer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ReleaseOut:
    """The first person proposes sending a held payout. A different person must approve it.
    409 `payout_not_held` or `release_already_proposed`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await reviews.propose_release(
                session,
                staff.actor,
                payout_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except reviews.ReviewRefused as error:
        raise _refused(error) from None
    return _release(view)


@staff_router.post("/payout-releases/{release_id}/approve")
async def approve_release(
    release_id: PushId,
    body: ReviewIn,
    staff: Reviewer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ReviewedPayoutOut:
    """A different person approves, and the payout is queued to be sent. 403 `proposed_by_you`;
    409 `release_already_decided` or `payout_not_held`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            moved = await reviews.approve_release(
                session,
                staff.actor,
                release_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except reviews.ReviewRefused as error:
        raise _refused(error) from None
    return _reviewed(moved, "approved")


@staff_router.post("/payout-releases/{release_id}/withdraw")
async def withdraw_release(
    release_id: PushId, body: ReviewIn, staff: Proposer, database: Db, clock: Time
) -> ReleaseOut:
    """The proposer takes their proposal back. 403 `not_proposed_by_you`; 409
    `release_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await reviews.withdraw_release(
                session, staff.actor, release_id, reason=body.reason, clock=clock
            )
    except reviews.ReviewRefused as error:
        raise _refused(error) from None
    return _release(view)


@staff_router.post("/payouts/{payout_id}/decline")
async def decline_held(
    payout_id: PushId,
    body: ReviewIn,
    staff: Reviewer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ReviewedPayoutOut:
    """Cancel a held payout and give its money back, closing any open proposal. One person's
    step. 409 `payout_not_held` once it is no longer held."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            moved = await reviews.decline(
                session,
                staff.actor,
                payout_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except reviews.ReviewRefused as error:
        raise _refused(error) from None
    return _reviewed(moved, "declined")
