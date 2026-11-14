"""Routes for paying for an investment, and for staff checking bank transfers.

The client never says how much to pay: the amount is the investment's, read in the same
transaction. Mobile money is collected from the number on the client's own account, from their
session, never from the request. Every route that can move money takes an `Idempotency-Key`.

A bank transfer arrives as a form with the receipt file. The file is stored first, outside any
transaction, then the transfer is saved pointing at it, so a saved transfer always has its file.
"""

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.storage import FileStorage, InvalidFile, NewFile, StorageError, sha256_hex
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import ClientSession, StaffSession
from app.modules.investments import holdings
from app.modules.kyc import service as kyc
from app.modules.payments import bank_transfers as bank
from app.modules.payments import service
from app.modules.payments.domain.bank_transfers import (
    MAX_RECEIPT_BYTES,
    RECEIPT_CONTENT_TYPES,
    InvalidReceipt,
    NotMatchedReason,
    ReceiptDetails,
    ReceiptFile,
    check_receipt_bytes,
)
from app.modules.payments.domain.deposits import (
    DepositStatus,
    Method,
    Network,
    Purpose,
    mask_msisdn,
)
from app.modules.payments.domain.provider import CollectionsProvider
from app.modules.payments.service import Deposit, DepositRequest
from app.modules.payments.settings import BankAccountDetails
from app.modules.reconciliation.statements import StatementLineView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency

logger = structlog.get_logger()

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
}
# Matching a transfer to the bank statement is reconciliation work. Admin, operations and finance.
BankStaff = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
# Matching, refusing or approving confirms money, so it is a permission of its own.
BankDeciders = Annotated[StaffSession, Depends(require_permission("bank_transfers.change"))]
# Deciding needs a passkey or app code from the last few minutes.
BankApprovers = Annotated[StaffSession, Depends(require_confirmed("bank_transfers.change"))]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _storage(request: Request) -> FileStorage:
    storage: FileStorage = request.app.state.storage
    return storage


Db = Annotated[Database, Depends(_database)]
Files = Annotated[FileStorage, Depends(_storage)]
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


class BankTransferDetails(Contract):
    """Where to send a bank transfer for one investment, and what to write on it."""

    bank_name: str
    account_name: str
    account_number: str
    branch: str | None
    # The client's Finvest account number. The client writes it on the transfer so staff can
    # find the line on the statement.
    reference: str
    amount: MoneyView


class MobileMoneyPayment(Contract):
    network: Literal["mtn", "airtel"]


async def _payable_for_transfer(
    session: AsyncSession,
    client: ClientSession,
    investment_id: str,
    amount_minor: int,
    clock: Clock,
) -> holdings.PayableInvestment:
    payable = await holdings.payable_investment(
        session, client_id=client.client_id, investment_id=investment_id, clock=clock
    )
    if amount_minor != payable.amount.minor:
        raise ApplicationError(
            "validation_failed",
            fields=[
                FieldProblem(
                    field_path=["body", "amount_minor"], code="amount_differs_from_investment"
                )
            ],
        )
    return payable


async def _discard_receipt(storage: FileStorage, key: str, deposit_id: str) -> None:
    """Delete a stored receipt that no transfer records. Best effort: if the store cannot be
    reached, the file stays under a key nothing points to, and the log says which deposit."""
    try:
        await storage.delete(key)
    except StorageError:
        logger.warning("receipt_orphaned", deposit_id=deposit_id)


def _receipt_problem(code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", "receipt"], code=code)]
    )


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


class PaymentPage(Contract):
    items: list[PaymentView]
    next_before: PushId | None


# Payments per page of a client's Activity list.
CLIENT_PAGE = 50


async def _payment_view(session: AsyncSession, deposit: Deposit) -> PaymentView:
    if deposit.method is Method.BANK_TRANSFER:
        # A transfer moved here from another client's payment has no receipt of its own.
        return _view(deposit, await bank.find_receipt(session, deposit.id))
    return _view(deposit)


def create_client_router(
    collections: CollectionsProvider | None,
    bank_accounts: Mapping[Currency, BankAccountDetails] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/client", tags=["client payments"], responses=_REFUSALS)
    accounts = dict(bank_accounts or {})

    @router.get("/investments/{investment_id}/bank-transfer-details")
    async def bank_transfer_details(
        investment_id: PushId, client: CurrentClient, database: Db, clock: Time
    ) -> BankTransferDetails:
        """Where to send a bank transfer for this investment, for exactly the amount owed.

        The same refusals as paying: 404 if it is not the client's, 409 if it can no longer be
        paid or already has a payment under way or paid (fetch that from
        `GET /client/investments/{investment_id}/payment`). 503 if no account is set for the
        investment's currency, so the app offers mobile money instead.
        """
        async with database.actor_transaction(client.actor) as session:
            payable = await holdings.payable_investment(
                session, client_id=client.client_id, investment_id=investment_id, clock=clock
            )
            live = await service.live_deposit_for(
                session, purpose=Purpose.INVESTMENT, purpose_id=payable.investment_id
            )
            if live is not None:
                raise ApplicationError("conflict")
            holder = await kyc.account_holder(session, client.client_id)
        account = accounts.get(payable.amount.currency)
        if account is None or holder is None:
            raise ApplicationError("unavailable")
        return BankTransferDetails(
            bank_name=account.bank_name,
            account_name=account.account_name,
            account_number=account.account_number,
            branch=account.branch,
            reference=holder.account_number,
            amount=MoneyView(minor=payable.amount.minor, currency=payable.amount.currency.value),
        )

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

    @router.post("/investments/{investment_id}/payments/bank-transfer", status_code=201)
    async def pay_by_bank_transfer(
        investment_id: PushId,
        receipt: Annotated[UploadFile, File(description="PDF, JPEG or PNG, at most 10 MiB")],
        amount_minor: Annotated[int, Form()],
        sent_on: Annotated[date, Form()],
        bank_name: Annotated[str, Form(min_length=1, max_length=120)],
        transaction_number: Annotated[str, Form(min_length=1, max_length=100)],
        client: CurrentClient,
        key: IdempotencyKey,
        database: Db,
        clock: Time,
        storage: Files,
    ) -> PaymentView:
        """Tell us about a bank transfer the client made, with its receipt, for staff to check.

        A `multipart/form-data` body. The amount sent must be exactly the investment's: a
        transfer for any other amount would be refused by staff hours later, so it is refused
        here first.
        """
        data = await receipt.read(MAX_RECEIPT_BYTES + 1)
        content_type = receipt.content_type or ""

        def request(
            payable: holdings.PayableInvestment, file: ReceiptFile
        ) -> bank.BankTransferRequest:
            return bank.BankTransferRequest(
                purpose=Purpose.INVESTMENT,
                purpose_id=payable.investment_id,
                file=file,
                details=ReceiptDetails(payable.amount, sent_on, bank_name, transaction_number),
            )

        # Everything that can refuse the transfer runs before the file is stored, so a refused
        # one, an investment already being paid or a replayed key never leaves a file behind.
        sent = ReceiptFile(content_type, len(data), sha256_hex(data))
        if content_type in RECEIPT_CONTENT_TYPES and 0 < len(data) <= MAX_RECEIPT_BYTES:
            # The type is the client's claim; the other refusals come from the preflight.
            try:
                check_receipt_bytes(content_type, data)
            except InvalidReceipt as error:
                raise _receipt_problem(error.code) from None
        async with database.actor_transaction(client.actor) as session:
            payable = await _payable_for_transfer(
                session, client, investment_id, amount_minor, clock
            )
            earlier = await bank.preflight_bank_receipt(
                session, client.actor, request(payable, sent), idempotency_key=key, clock=clock
            )
            if earlier is not None:
                return _view(earlier.deposit, earlier.receipt)
        deposit_id = bank.new_deposit_id()
        stored_key = bank.receipt_key(deposit_id)
        try:
            stored = await storage.put(NewFile(stored_key, data, content_type))
        except InvalidFile:
            raise _receipt_problem("unsupported_content_type") from None
        except StorageError:
            # Nothing was saved, so the client can send the same request again.
            raise ApplicationError("unavailable") from None
        try:
            async with database.actor_transaction(client.actor) as session:
                payable = await _payable_for_transfer(
                    session, client, investment_id, amount_minor, clock
                )
                transfer = await bank.submit_bank_receipt(
                    session,
                    client.actor,
                    request(
                        payable, ReceiptFile(stored.content_type, stored.byte_size, stored.sha256)
                    ),
                    idempotency_key=key,
                    clock=clock,
                    deposit_id=deposit_id,
                )
        except Exception:
            await _discard_receipt(storage, stored_key, deposit_id)
            raise
        if transfer.deposit.id != deposit_id:
            # The same request, sent twice at once, was saved by the other one.
            await _discard_receipt(storage, stored_key, deposit_id)
        return _view(transfer.deposit, transfer.receipt)

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

    @router.get("/payments")
    async def list_payments(
        client: CurrentClient,
        database: Db,
        before: Annotated[PushId | None, Query()] = None,
    ) -> PaymentPage:
        """The client's own payments, newest first, for the Activity list.

        Each is summarised without its receipt; `GET /client/payments/{payment_id}` has the
        detail. `next_before` is the ID to pass as `before` for the next page.
        """
        async with database.actor_transaction(client.actor) as session:
            deposits = await service.list_deposits(session, limit=CLIENT_PAGE + 1, before_id=before)
        page = deposits[:CLIENT_PAGE]
        return PaymentPage(
            items=[_view(deposit) for deposit in page],
            next_before=page[-1].id if len(deposits) > CLIENT_PAGE else None,
        )

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


class ReceiptLinkOut(Contract):
    # Opens the receipt inline for a few minutes. Anyone holding it can open the file until it
    # expires, so it is never logged or kept.
    url: str
    expires_at: datetime


class BankTransferOut(Contract):
    payment: PaymentView
    client_id: PushId
    # None if storage cannot make a link right now; the rest of the transfer still shows.
    receipt_link: ReceiptLinkOut | None
    review: ReviewOut | None
    # The line the first person chose, shown to the second beside the receipt so the approval
    # is a real check of amount, date and narrative. None until the transfer is matched.
    matched_line: StatementLineOut | None
    # Lines that could be this transfer. Empty once it has been reviewed.
    candidates: list[StatementLineOut]


class BankTransferInQueue(PaymentView):
    # The step the transfer waits for: `check` (nobody has reviewed it), `second_person` (one
    # person matched it and a different one must approve), `posting` (approved or refused, and
    # the worker is settling it).
    awaiting: Literal["check", "second_person", "posting"]


class BankTransferPage(Contract):
    items: list[BankTransferInQueue]
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
        items = [
            BankTransferInQueue(
                **vars(await _payment_view(session, deposit)),
                awaiting=_awaiting(await bank.get_review(session, staff.actor, deposit.id)),
            )
            for deposit in deposits
        ]
    return BankTransferPage(
        items=items, next_before=deposits[-1].id if len(deposits) == PAGE else None
    )


def _awaiting(review: bank.ReviewView | None) -> Literal["check", "second_person", "posting"]:
    if review is None:
        return "check"
    if review.outcome == "matched" and review.approved_by is None:
        return "second_person"
    return "posting"


RECEIPT_LINK_LIFETIME = timedelta(minutes=5)


def _receipt_link(
    storage: FileStorage, receipt: bank.BankReceipt, clock: Clock
) -> ReceiptLinkOut | None:
    try:
        link = storage.read_link(
            receipt.storage_key,
            content_type=receipt.content_type,
            lifetime=RECEIPT_LINK_LIFETIME,
            now=clock.now(),
        )
    except StorageError:
        return None
    return ReceiptLinkOut(url=link.url, expires_at=link.expires_at)


async def _detail(
    session: AsyncSession, staff: StaffSession, payment_id: str, storage: FileStorage, clock: Clock
) -> BankTransferOut:
    transfer = await bank.get_bank_transfer(session, payment_id)
    review = await bank.get_review(session, staff.actor, payment_id)
    line = await bank.matched_line(session, staff.actor, payment_id) if review else None
    candidates: tuple[StatementLineView, ...] = ()
    if review is None and transfer.deposit.status is DepositStatus.IN_REVIEW:
        candidates = await bank.candidate_lines(session, staff.actor, payment_id)
    return BankTransferOut(
        payment=_view(transfer.deposit, transfer.receipt),
        client_id=transfer.deposit.client_id,
        receipt_link=_receipt_link(storage, transfer.receipt, clock),
        review=_review(review) if review else None,
        matched_line=_line(line) if line else None,
        candidates=[_line(line) for line in candidates],
    )


@staff_router.get("/payments/bank-transfers/{payment_id}")
async def bank_transfer(
    payment_id: PushId, staff: BankStaff, database: Db, clock: Time, storage: Files
) -> BankTransferOut:
    """One transfer, its review and the statement lines that could be it.

    The bank's narrative can name the sender, so opening a transfer is written to the audit trail.
    """
    async with database.actor_transaction(staff.actor) as session:
        detail = await _detail(session, staff, payment_id, storage, clock)
        await bank.record_opened(session, staff.actor, payment_id, clock)
    return detail


@staff_router.post("/payments/bank-transfers/{payment_id}/review")
async def review_bank_transfer(
    payment_id: PushId,
    body: ReviewIn,
    staff: BankDeciders,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
    storage: Files,
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
    async with database.actor_transaction(staff.actor) as session:
        return await _detail(session, staff, payment_id, storage, clock)


@staff_router.post("/payments/bank-transfers/{payment_id}/approve")
async def approve_bank_match(
    payment_id: PushId,
    staff: BankApprovers,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
    storage: Files,
) -> BankTransferOut:
    """Approve another staff member's match. The payment is confirmed moments later."""
    async with database.actor_transaction(staff.actor) as session:
        await bank.approve_bank_match(
            session, staff.actor, payment_id, idempotency_key=key, clock=clock
        )
    async with database.actor_transaction(staff.actor) as session:
        return await _detail(session, staff, payment_id, storage, clock)
