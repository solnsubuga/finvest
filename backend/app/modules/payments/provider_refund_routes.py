"""Staff routes for paying a card payment back to the card. See `provider_refunds.py`.

Seeing refunds needs `reconciliation.view`, like the payments they come from. Proposing,
approving, sending back, withdrawing and saying what the provider shows need
`deposit_reassignments.change`, the permission for putting a payment right, and approving,
which moves money, also needs the second step from the last few minutes. Every `POST` needs an
`Idempotency-Key`.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payments import provider_refunds as service
from app.modules.payments.domain.provider_refunds import RefundStatus, Settlement
from app.modules.payments.domain.reassignments import MAX_TEXT_LENGTH
from app.modules.payments.routes import MoneyView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope
from app.shared.money import Money

PERMISSION = "deposit_reassignments.change"
_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

Viewers = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
Deciders = Annotated[StaffSession, Depends(require_permission(PERMISSION))]
# Approving takes money out of a client's investment and sends it to a card, so it asks for a
# passkey or app code from the last few minutes. The console answers `confirm_it_is_you`.
Approvers = Annotated[StaffSession, Depends(require_confirmed(PERMISSION))]

Status = Literal[
    "proposed",
    "approved",
    "sent_back",
    "withdrawn",
    "sending",
    "requested",
    "uncertain",
    "done",
    "failed",
]
Found = Literal["requested", "paid", "not_paid"]
Refusal = Literal[
    "not_confirmed",
    "not_for_an_investment",
    "not_refundable",
    "already_taken_back",
    "not_a_payment",
    "no_confirmation_code",
    "money_moved_on",
]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class CardRefundOut(Contract):
    id: PushId
    status: Status
    # The confirmed card payment paid back, and its client and investment.
    deposit_id: PushId
    client_id: PushId
    investment_id: PushId
    # pesapal, or card_sandbox outside production.
    provider: str
    # The investment's amount, the card charge the client paid on top, and what goes back:
    # the amount, and the charge too when the refund is Finvest's mistake.
    amount: MoneyView
    charge: MoneyView
    finvest_erred: bool
    total: MoneyView
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    # Why it was sent back.
    decision_note: str | None
    # When the money was held and the provider asked.
    sent_at: datetime | None
    # What a member of staff found at the provider, and where they saw it.
    found: Found | None
    found_by: PushId | None
    found_at: datetime | None
    found_note: str | None
    done_at: datetime | None
    failure_code: str | None


class CardRefundDetailOut(Contract):
    refund: CardRefundOut
    # The card that paid, as the client sees it. Never the card number.
    card_brand: str | None
    card_last_four: str | None
    # What waits for the investment now. Paying back needs at least the payment's amount.
    waiting: MoneyView
    # For an open proposal, why approving would be refused right now. None if it would go
    # through, and for a decided one.
    refusal: Refusal | None
    # The person reading may approve or send it back: they hold the permission and did not
    # propose it. The proposer may only withdraw it.
    can_decide: bool
    # The person reading may say what the provider shows: it waits on the provider, they hold
    # the permission and did not propose it.
    can_record_finding: bool
    yours: bool


class CardRefundProposeIn(Contract):
    deposit_id: PushId
    reason: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]
    # The refund is Finvest's own mistake, so the card charge goes back too.
    finvest_erred: bool = False


class CardRefundSendBackIn(Contract):
    note: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


class CardRefundFindingIn(Contract):
    found: Found
    # Where it was seen: the provider's dashboard or statement, and the date.
    note: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


def _money(amount: Money) -> MoneyView:
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def _out(refund: service.Refund) -> CardRefundOut:
    return CardRefundOut(
        id=refund.id,
        status=refund.status.value,
        deposit_id=refund.deposit_id,
        client_id=refund.client_id,
        investment_id=refund.investment_id,
        provider=refund.provider,
        amount=_money(refund.amount),
        charge=_money(refund.charge),
        finvest_erred=refund.finvest_erred,
        total=_money(refund.total),
        reason=refund.reason,
        proposed_by=refund.proposed_by,
        proposed_at=refund.proposed_at,
        decided_by=refund.decided_by,
        decided_at=refund.decided_at,
        decision_note=refund.decision_note,
        sent_at=refund.sent_at,
        found=refund.settlement.value if refund.settlement else None,
        found_by=refund.settled_by,
        found_at=refund.settled_at,
        found_note=refund.settlement_note,
        done_at=refund.done_at,
        failure_code=refund.failure_code,
    )


def _detail(detail: service.RefundDetail, staff: StaffSession) -> CardRefundDetailOut:
    refund = detail.refund
    yours = refund.proposed_by == staff.staff_id
    allowed = PERMISSION in staff.permissions and not yours
    waiting_on_provider = refund.status in (RefundStatus.REQUESTED, RefundStatus.UNCERTAIN) and (
        refund.settlement is None
        or (refund.status is RefundStatus.REQUESTED and refund.settlement is Settlement.REQUESTED)
    )
    return CardRefundDetailOut(
        refund=_out(refund),
        card_brand=detail.card_brand,
        card_last_four=detail.card_last_four,
        waiting=_money(detail.waiting),
        refusal=detail.refusal.value if detail.refusal else None,
        can_decide=refund.status.is_open and allowed,
        can_record_finding=waiting_on_provider and allowed,
        yours=yours,
    )


router = APIRouter(prefix="/staff", tags=["staff card refunds"], responses=_REFUSALS)


@router.get("/payments/card-refunds")
async def list_card_refunds(
    staff: Viewers,
    database: Db,
    status: Annotated[Status | None, Query()] = None,
    deposit_id: Annotated[PushId | None, Query()] = None,
) -> list[CardRefundOut]:
    """Card refunds, newest first, at most 200."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.list_refunds(
            session,
            staff.actor,
            status=RefundStatus(status) if status else None,
            deposit_id=deposit_id,
            limit=200,
        )
    return [_out(refund) for refund in found]


@router.get("/payments/card-refunds/{refund_id}")
async def get_card_refund(
    refund_id: PushId, staff: Viewers, database: Db, clock: Time
) -> CardRefundDetailOut:
    """One card refund, the card, the money waiting now and whether approving would go through."""
    return await _read(database, staff, refund_id, clock)


async def _read(
    database: Database, staff: StaffSession, refund_id: str, clock: Clock
) -> CardRefundDetailOut:
    async with database.actor_transaction(staff.actor) as session:
        detail = await service.get_refund(session, staff.actor, refund_id, clock)
    return _detail(detail, staff)


@router.post("/payments/card-refunds", status_code=201)
async def propose_card_refund(
    body: CardRefundProposeIn, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> CardRefundDetailOut:
    """Propose paying a card payment back to the card. Nothing moves until a different member
    of staff approves.

    The whole amount must still wait for the investment (`422` `money_moved_on` if not: unwind
    a started investment first). `409` if the payment already has a live refund.
    """
    async with database.actor_transaction(staff.actor) as session:
        proposed = await service.propose_refund(
            session,
            staff.actor,
            deposit_id=body.deposit_id,
            reason=body.reason,
            finvest_erred=body.finvest_erred,
            idempotency_key=key,
            clock=clock,
        )
    return await _read(database, staff, proposed.id, clock)


@router.post("/payments/card-refunds/{refund_id}/approve")
async def approve_card_refund(
    refund_id: PushId, staff: Approvers, key: IdempotencyKey, database: Db, clock: Time
) -> CardRefundDetailOut:
    """Approve someone else's proposal. The worker holds the money and asks the provider once,
    moments later. The proposer gets `403`. `422` with a code if the money moved on since it
    was proposed: the proposal stays open."""
    async with database.actor_transaction(staff.actor) as session:
        await service.approve_refund(
            session, staff.actor, refund_id, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, refund_id, clock)


@router.post("/payments/card-refunds/{refund_id}/send-back")
async def send_back_card_refund(
    refund_id: PushId,
    body: CardRefundSendBackIn,
    staff: Deciders,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
) -> CardRefundDetailOut:
    """Decline someone else's proposal, with a note. Nothing moves. The proposer gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.send_back_refund(
            session, staff.actor, refund_id, body.note, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, refund_id, clock)


@router.post("/payments/card-refunds/{refund_id}/withdraw")
async def withdraw_card_refund(
    refund_id: PushId, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> CardRefundDetailOut:
    """Take back your own proposal before anyone decides it. Anyone else gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.withdraw_refund(
            session, staff.actor, refund_id, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, refund_id, clock)


@router.post("/payments/card-refunds/{refund_id}/finding")
async def record_card_refund_finding(
    refund_id: PushId,
    body: CardRefundFindingIn,
    staff: Approvers,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
) -> CardRefundDetailOut:
    """Say what the provider shows for a refund waiting on it: the request is there, it was
    paid, or it was not. The worker carries it out moments later. The proposer gets `403`;
    `409` if the refund is not waiting on the provider."""
    async with database.actor_transaction(staff.actor) as session:
        await service.record_finding(
            session,
            staff.actor,
            refund_id,
            Settlement(body.found),
            body.note,
            idempotency_key=key,
            clock=clock,
        )
    return await _read(database, staff, refund_id, clock)


routers: list[APIRouter] = [router]
