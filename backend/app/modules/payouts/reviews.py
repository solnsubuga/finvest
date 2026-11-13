"""Staff review of a payout held because it is large.

A payout above the review threshold in force starts held, with its money already reserved. One
member of staff approves it, and it is queued and sent, or declines it, and the money goes back
to where it came from. The client asked for it, so the staff member is the second person. Both
need a reason and an idempotency key: a retried request returns what the first one did.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.payouts import payouts
from app.modules.payouts.domain.payouts import IllegalTransition, PayoutEvent, PayoutStatus
from app.modules.payouts.payouts import PayoutView
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

APPROVE_OPERATION = "payouts.approve_held"
DECLINE_OPERATION = "payouts.decline_held"


class ReviewRefused(Exception):
    """The review was not recorded. `code` is safe to show to staff."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def approve(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> PayoutView:
    """Release a held payout to be sent."""
    return await _review(
        session, staff, payout_id, PayoutEvent.REVIEW_APPROVED, reason, idempotency_key, clock
    )


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
    return await _review(
        session, staff, payout_id, PayoutEvent.REVIEW_DECLINED, reason, idempotency_key, clock
    )


async def _review(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    event: PayoutEvent,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> PayoutView:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    if not reason.strip():
        raise ApplicationError("validation_failed")

    async def command() -> Receipt:
        payout = await payouts.get_payout(session, payout_id)
        if payout.status is not PayoutStatus.HELD:
            raise ReviewRefused("payout_not_held")
        try:
            moved = await payouts.apply_event(
                session, staff, payout_id, event, clock, reason=reason.strip()
            )
        except IllegalTransition:
            # Cancelled by the client or decided by someone else since it was read.
            raise ReviewRefused("payout_not_held") from None
        return Receipt(200, {"payout_id": moved.id})

    done = await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION if event is PayoutEvent.REVIEW_APPROVED else DECLINE_OPERATION,
        key=idempotency_key,
        request={"payout_id": payout_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await payouts.get_payout(session, str(done.receipt.response["payout_id"]))
