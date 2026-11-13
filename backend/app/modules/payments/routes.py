"""Routes for paying for an investment, and for staff checking bank transfers.

The client never says how much to pay: the amount is the investment's, read in the same
transaction. Mobile money is collected from the number on the client's own account, from their
session, never from the request. Every route that can move money takes an `Idempotency-Key`.

A client tells us about a bank transfer through a route that uploads the receipt itself. It
arrives with the shared file storage, so until then a bank transfer cannot be started over HTTP.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import CurrentClient, require_permission
from app.modules.identity.service import StaffSession
from app.modules.investments import holdings
from app.modules.payments import bank_transfers as bank
from app.modules.payments import service
from app.modules.payments.domain.bank_transfers import NotMatchedReason
from app.modules.payments.domain.deposits import (
    DepositStatus,
    Method,
    Network,
    Purpose,
    mask_msisdn,
)
from app.modules.payments.domain.provider import CollectionsProvider
from app.modules.payments.service import Deposit, DepositRequest
from app.modules.reconciliation.statements import StatementLineView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}
# Matching a transfer to the bank statement is reconciliation work. Admin, operations and finance.
BankStaff = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class MoneyView(Contract):
    minor: int
    currency: Literal["UGX", "USD"]


class ReceiptView(Contract):
    sent_on: date
    bank_name: str
    transaction_number: str
    content_type: str
    byte_size: int


class PaymentView(Contract):
    id: PushId
    investment_id: PushId
    method: Literal["mtn", "airtel", "bank_transfer"]
    status: Literal[
        "created", "sending", "awaiting_approval", "uncertain", "in_review", "confirmed", "failed"
    ]
    amount: MoneyView
    # Why it failed, for a failed payment: a provider refusal or a bank transfer's reason.
    failure: str | None
    # Shown as the client may see it again, never in full.
    payer: str | None
    receipt: ReceiptView | None
    created_at: datetime
    confirmed_at: datetime | None


class MobileMoneyPayment(Contract):
    network: Literal["mtn", "airtel"]


def _view(deposit: Deposit, receipt: bank.BankReceipt | None = None) -> PaymentView:
    final_failure = deposit.code if deposit.status is DepositStatus.FAILED else None
    return PaymentView(
        id=deposit.id,
        investment_id=deposit.purpose_id,
        method=deposit.method.value,
        status=deposit.status.value,
        amount=MoneyView(minor=deposit.amount.minor, currency=deposit.amount.currency.value),
        failure=final_failure,
        payer=mask_msisdn(deposit.payer_msisdn) if deposit.payer_msisdn else None,
        receipt=None
        if receipt is None
        else ReceiptView(
            sent_on=receipt.sent_on,
            bank_name=receipt.bank_name,
            transaction_number=receipt.transaction_number,
            content_type=receipt.content_type,
            byte_size=receipt.byte_size,
        ),
        created_at=deposit.created_at,
        confirmed_at=deposit.settled_at if deposit.status is DepositStatus.CONFIRMED else None,
    )


async def _payment_view(session: AsyncSession, deposit: Deposit) -> PaymentView:
    if deposit.method is Method.BANK_TRANSFER:
        transfer = await bank.get_bank_transfer(session, deposit.id)
        return _view(transfer.deposit, transfer.receipt)
    return _view(deposit)


def create_client_router(collections: CollectionsProvider | None) -> APIRouter:
    router = APIRouter(prefix="/client", tags=["client payments"], responses=_REFUSALS)

    @router.post("/investments/{investment_id}/payments/mobile-money", status_code=201)
    async def pay_by_mobile_money(
        investment_id: PushId,
        body: MobileMoneyPayment,
        client: CurrentClient,
        key: IdempotencyKey,
        database: Db,
        clock: Time,
    ) -> PaymentView:
        """Ask for a mobile money prompt on the client's own number, for exactly the amount owed.

        409 if the investment already has a payment under way or paid: fetch it from
        `GET /client/investments/{investment_id}/payment`.
        """
        if collections is None:
            raise ApplicationError("unavailable")
        async with database.actor_transaction(client.actor) as session:
            payable = await holdings.payable_investment(
                session, client_id=client.client_id, investment_id=investment_id, clock=clock
            )
            deposit = await service.request_deposit(
                session,
                client.actor,
                DepositRequest(
                    purpose=Purpose.INVESTMENT,
                    purpose_id=payable.investment_id,
                    network=Network(body.network),
                    amount=payable.amount,
                    phone=client.phone,
                ),
                idempotency_key=key,
                account_phone=client.phone,
                provider=collections.name,
                clock=clock,
            )
            return _view(deposit)

    @router.get("/investments/{investment_id}/payment")
    async def current_payment(
        investment_id: PushId, client: CurrentClient, database: Db
    ) -> PaymentView:
        """The investment's payment that has not failed: under way, being checked or paid."""
        async with database.actor_transaction(client.actor) as session:
            deposit = await service.live_deposit_for(
                session, purpose=Purpose.INVESTMENT, purpose_id=investment_id
            )
            if deposit is None:
                raise ApplicationError("not_found")
            return await _payment_view(session, deposit)

    @router.get("/payments/{payment_id}")
    async def get_payment(payment_id: PushId, client: CurrentClient, database: Db) -> PaymentView:
        async with database.actor_transaction(client.actor) as session:
            return await _payment_view(session, await service.get_deposit(session, payment_id))

    return router


# Staff --------------------------------------------------------------------------------------


class StatementLineOut(Contract):
    id: PushId
    account_reference: str
    value_date: date
    amount: MoneyView
    reference: str
    # The bank's narrative. It can name the sender, so it is shown and never logged.
    narrative: str


class ReviewOut(Contract):
    outcome: Literal["matched", "not_matched"]
    reason: str | None
    statement_line_id: PushId | None
    reviewed_by: PushId
    reviewed_at: datetime
    approved_by: PushId | None
    approved_at: datetime | None


class BankTransferOut(Contract):
    payment: PaymentView
    client_id: PushId
    review: ReviewOut | None
    # The line the first person chose, shown to the second beside the receipt so the approval
    # is a real check of amount, date and narrative. None until the transfer is matched.
    matched_line: StatementLineOut | None
    # Lines that could be this transfer. Empty once it has been reviewed.
    candidates: list[StatementLineOut]


class BankTransferPage(Contract):
    items: list[PaymentView]
    next_before: PushId | None


class MatchIn(Contract):
    outcome: Literal["matched"]
    statement_line_id: PushId


class NotMatchedIn(Contract):
    outcome: Literal["not_matched"]
    reason: Literal["amount_differs", "not_on_statement", "unreadable", "name_differs", "other"]


ReviewIn = Annotated[MatchIn | NotMatchedIn, Field(discriminator="outcome")]


def _line(line: StatementLineView) -> StatementLineOut:
    return StatementLineOut(
        id=line.id,
        account_reference=line.account_reference,
        value_date=line.value_date,
        amount=MoneyView(minor=line.amount.minor, currency=line.amount.currency.value),
        reference=line.reference,
        narrative=line.counterparty_hint,
    )


def _review(view: bank.ReviewView) -> ReviewOut:
    return ReviewOut(
        outcome="matched" if view.outcome == "matched" else "not_matched",
        reason=view.reason.value if view.reason else None,
        statement_line_id=view.statement_line_id,
        reviewed_by=view.reviewed_by,
        reviewed_at=view.reviewed_at,
        approved_by=view.approved_by,
        approved_at=view.approved_at,
    )


staff_router = APIRouter(prefix="/staff", tags=["staff bank transfers"], responses=_REFUSALS)
PAGE = 50


@staff_router.get("/payments/bank-transfers")
async def bank_transfers_in_review(
    staff: BankStaff,
    database: Db,
    before: Annotated[PushId | None, Query()] = None,
) -> BankTransferPage:
    """Bank transfers waiting for a check or a second person, newest first."""
    async with database.actor_transaction(staff.actor) as session:
        deposits = await service.list_deposits(
            session, status=DepositStatus.IN_REVIEW, limit=PAGE, before_id=before
        )
        items = [await _payment_view(session, deposit) for deposit in deposits]
    return BankTransferPage(
        items=items, next_before=deposits[-1].id if len(deposits) == PAGE else None
    )


@staff_router.get("/payments/bank-transfers/{payment_id}")
async def bank_transfer(payment_id: PushId, staff: BankStaff, database: Db) -> BankTransferOut:
    async with database.actor_transaction(staff.actor) as session:
        transfer = await bank.get_bank_transfer(session, payment_id)
        review = await bank.get_review(session, staff.actor, payment_id)
        line = await bank.matched_line(session, staff.actor, payment_id) if review else None
        candidates: tuple[StatementLineView, ...] = ()
        if review is None and transfer.deposit.status is DepositStatus.IN_REVIEW:
            candidates = await bank.candidate_lines(session, staff.actor, payment_id)
    return BankTransferOut(
        payment=_view(transfer.deposit, transfer.receipt),
        client_id=transfer.deposit.client_id,
        review=_review(review) if review else None,
        matched_line=_line(line) if line else None,
        candidates=[_line(line) for line in candidates],
    )


@staff_router.post("/payments/bank-transfers/{payment_id}/review")
async def review_bank_transfer(
    payment_id: PushId,
    body: ReviewIn,
    staff: BankStaff,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
) -> BankTransferOut:
    """Match the transfer to a statement line, or say why it cannot be matched.

    A match waits for a second member of staff. A refusal fails the payment with no money moved.
    """
    outcome: bank.Review = (
        bank.Matched(body.statement_line_id)
        if isinstance(body, MatchIn)
        else bank.NotMatched(NotMatchedReason(body.reason))
    )
    async with database.actor_transaction(staff.actor) as session:
        await bank.review_bank_transfer(
            session, staff.actor, payment_id, outcome, idempotency_key=key, clock=clock
        )
    return await bank_transfer(payment_id, staff, database)


@staff_router.post("/payments/bank-transfers/{payment_id}/approve")
async def approve_bank_match(
    payment_id: PushId, staff: BankStaff, key: IdempotencyKey, database: Db, clock: Time
) -> BankTransferOut:
    """Approve another staff member's match. The payment is confirmed moments later."""
    async with database.actor_transaction(staff.actor) as session:
        await bank.approve_bank_match(
            session, staff.actor, payment_id, idempotency_key=key, clock=clock
        )
    return await bank_transfer(payment_id, staff, database)
