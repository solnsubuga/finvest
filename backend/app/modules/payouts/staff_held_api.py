"""Staff routes for payouts held because they are large.

`GET /staff/payouts/held` is part of the review queue (`review_queue.view`). Approving or
declining one needs `payout_accounts.change`, the payout reviewers' permission, a second step in
the last few minutes, a reason and an `Idempotency-Key`. The client asked for the payout, so the
member of staff who decides it is the second person.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import payouts, reviews
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
# Deciding needs a passkey or app code from the last few minutes.
Reviewer = Annotated[StaffSession, Depends(require_confirmed("payout_accounts.change"))]


class HeldPayoutOut(Contract):
    """A payout waiting for a member of staff, because it is above the review threshold."""

    id: PushId
    client_id: PushId
    destination_id: PushId
    kind: Literal["mobile_money", "bank"]
    amount: MoneyView
    business_date: date
    created_at: datetime


class ReviewIn(Contract):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class ReviewedPayoutOut(Contract):
    """The decision, and where the payout is now. An approved payout is `queued` to be sent, and
    may have moved on by the time a repeated request is answered; a declined one is `cancelled`
    with its money back."""

    id: PushId
    decision: Literal["approved", "declined"]
    status: Literal["queued", "sent", "uncertain", "paid", "failed", "cancelled"]


def _held(payout: PayoutView) -> HeldPayoutOut:
    return HeldPayoutOut(
        id=payout.id,
        client_id=payout.client_id,
        destination_id=payout.destination_id,
        kind=payout.kind.value,
        amount=MoneyView(minor=payout.amount.minor, currency=payout.amount.currency.value),
        business_date=payout.business_date,
        created_at=payout.created_at,
    )


def _reviewed(payout: PayoutView, decision: Literal["approved", "declined"]) -> ReviewedPayoutOut:
    status = payout.status.value
    assert status != "held", "a decided payout is never held"
    return ReviewedPayoutOut(id=payout.id, decision=decision, status=status)  # type: ignore[arg-type]


def _refused(error: reviews.ReviewRefused) -> ApplicationError:
    return ApplicationError("conflict", fields=[FieldProblem(field_path=["body"], code=error.code)])


@staff_router.get("/payouts/held")
async def held(staff: Viewer, database: Db) -> list[HeldPayoutOut]:
    """Payouts held for review, oldest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await payouts.held_for_review(session, limit=100)
    return [_held(payout) for payout in found]


@staff_router.post("/payouts/{payout_id}/approve")
async def approve_held(
    payout_id: PushId,
    body: ReviewIn,
    staff: Reviewer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ReviewedPayoutOut:
    """Release a held payout to be sent. 409 `payout_not_held` once it is no longer held."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            moved = await reviews.approve(
                session,
                staff.actor,
                payout_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except reviews.ReviewRefused as error:
        raise _refused(error) from None
    return _reviewed(moved, "approved")


@staff_router.post("/payouts/{payout_id}/decline")
async def decline_held(
    payout_id: PushId,
    body: ReviewIn,
    staff: Reviewer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ReviewedPayoutOut:
    """Cancel a held payout and give its money back. 409 `payout_not_held` once it is no
    longer held."""
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
