"""Deposits by bank transfer: the client's receipt, the staff check and the posting.

The rules are in `domain/bank_transfers.py`. Three steps, each in its own transaction:

1. `submit_bank_receipt`, in the client's transaction, saves the deposit as `in_review` with
   the receipt's details. The file goes to private storage under the receipt's storage key;
   the database keeps only its type, size and digest.
2. `review_bank_transfer`, in a staff transaction, records what the staff member found: the
   bank statement line that is this transfer, or why there is none. It moves no money and
   changes no deposit. A refusal queues the job that settles it. A match waits for a second
   member of staff, who approves it with `approve_bank_match`, and that queues the job.
3. The job, in the worker, settles the deposit exactly as the review says. A match posts the
   ledger entry, debiting the bank account the statement is for. The database refuses any
   other outcome, and refuses to confirm a match nobody else approved.

A bank transfer is in the same table as mobile money deposits, so an investment still takes one
live deposit, whichever way it is paid.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    PermanentJobError,
    enqueue,
)
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.payments import service
from app.modules.payments.domain.bank_transfers import (
    InvalidReceipt,
    LineFacts,
    NotMatchedReason,
    ReceiptDetails,
    ReceiptFile,
    check_receipt_details,
    check_receipt_file,
    match_problem,
    match_window,
)
from app.modules.payments.domain.deposits import DepositStatus, Method, Move, Purpose
from app.modules.payments.service import Deposit
from app.modules.reconciliation.domain.pots import CASH_POTS
from app.modules.reconciliation.domain.statements import Direction, StatementSource
from app.modules.reconciliation.statements import StatementLineView, find_statement_lines
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency
from app.shared.push_ids import push_id, validate_push_id

BANK_PROVIDER = "bank"
BANK_ACCOUNT_CODE = "bank.trust"
SUBMIT_OPERATION = "payments.submit_bank_receipt"
REVIEW_OPERATION = "payments.review_bank_transfer"
SETTLE_KIND = "payments.settle_bank_transfer"

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class BankTransferRequest:
    purpose: Purpose
    purpose_id: str
    file: ReceiptFile
    details: ReceiptDetails


@dataclass(frozen=True, slots=True)
class BankReceipt:
    deposit_id: str
    storage_key: str
    content_type: str
    byte_size: int
    sha256: str
    sent_on: date
    bank_name: str
    transaction_number: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BankTransfer:
    deposit: Deposit
    receipt: BankReceipt


@dataclass(frozen=True, slots=True)
class Matched:
    """The transfer is this line of an imported bank statement."""

    statement_line_id: str


@dataclass(frozen=True, slots=True)
class NotMatched:
    reason: NotMatchedReason


Review = Matched | NotMatched


@dataclass(frozen=True, slots=True)
class ReviewView:
    """What a staff member found, and who approved it if it was a match."""

    outcome: str  # "matched" or "not_matched"
    reason: NotMatchedReason | None
    statement_line_id: str | None
    account_reference: str | None
    reviewed_by: str
    reviewed_at: datetime
    approved_by: str | None
    approved_at: datetime | None


_INSERT_RECEIPT = text(
    "INSERT INTO payment_bank_receipts (deposit_id, client_id, storage_key, content_type, "
    "byte_size, sha256, sent_on, bank_name, transaction_number, created_at) VALUES "
    "(:deposit_id, :client_id, :storage_key, :content_type, :byte_size, :sha256, :sent_on, "
    ":bank_name, :transaction_number, :now)"
)
_SELECT_RECEIPT = text(
    "SELECT deposit_id, storage_key, content_type, byte_size, sha256, sent_on, bank_name, "
    "transaction_number, created_at FROM payment_bank_receipts WHERE deposit_id = :id"
)
_INSERT_REVIEW = text(
    "INSERT INTO payment_bank_reviews (deposit_id, outcome, reason, statement_line_id, "
    "account_reference, reviewed_by, reviewed_at) VALUES (:deposit_id, :outcome, :reason, "
    ":statement_line_id, :account_reference, :reviewed_by, :now)"
)
_SELECT_REVIEW = text(
    "SELECT outcome, reason, account_reference FROM payment_bank_reviews WHERE deposit_id = :id"
)
_SELECT_REVIEW_VIEW = text(
    "SELECT review.outcome, review.reason, review.statement_line_id, review.account_reference, "
    "review.reviewed_by, review.reviewed_at, approval.approved_by, approval.approved_at "
    "FROM payment_bank_reviews AS review LEFT JOIN payment_bank_approvals AS approval "
    "ON approval.deposit_id = review.deposit_id WHERE review.deposit_id = :id"
)
_INSERT_APPROVAL = text(
    "INSERT INTO payment_bank_approvals (deposit_id, approved_by, approved_at) "
    "VALUES (:deposit_id, :approved_by, :now)"
)
_SELECT_APPROVED = text("SELECT 1 FROM payment_bank_approvals WHERE deposit_id = :id")
_USED_LINES = text(
    "SELECT statement_line_id FROM payment_bank_reviews "
    "WHERE statement_line_id = ANY(CAST(:ids AS text[]))"
)
_REVIEW_KEY = "pk_payment_bank_reviews"
_APPROVAL_KEY = "pk_payment_bank_approvals"
APPROVE_OPERATION = "payments.approve_bank_match"
_LINE_KEY = "uq_payment_bank_reviews_statement_line_id"
# PostgreSQL's SQLSTATE for a refused row-level security policy, among other privilege errors.
_INSUFFICIENT_PRIVILEGE = "42501"


class _WouldSave(Exception):
    """Raised inside a preflight's savepoint once a new transfer would be saved."""


async def preflight_bank_receipt(
    session: AsyncSession,
    actor: Actor,
    request: BankTransferRequest,
    *,
    idempotency_key: str,
    clock: Clock,
) -> BankTransfer | None:
    """Run `submit_bank_receipt` without keeping anything, before the receipt file is stored.

    Raises whatever it would raise: a refused detail, `DepositExists` for an investment already
    being paid, or a key reused with other details. Returns the earlier transfer if this key
    already saved one, so the file need not be stored again, and None if a new transfer would
    be saved. Nothing is written either way.
    """
    try:
        async with session.begin_nested():
            return await submit_bank_receipt(
                session,
                actor,
                request,
                idempotency_key=idempotency_key,
                clock=clock,
                _file_stored=False,
            )
    except _WouldSave:
        return None


def receipt_key(deposit_id: str) -> str:
    """Where a deposit's receipt file is stored. One receipt per deposit, written once."""
    return f"payments/receipts/{validate_push_id(deposit_id)}"


def new_deposit_id() -> str:
    """An ID for a bank transfer not yet saved, so its receipt can be stored under
    `receipt_key` before the transaction that records it opens."""
    return push_id()


async def submit_bank_receipt(
    session: AsyncSession,
    actor: Actor,
    request: BankTransferRequest,
    *,
    idempotency_key: str,
    clock: Clock,
    deposit_id: str | None = None,
    _file_stored: bool = True,
) -> BankTransfer:
    """Save a client's bank transfer for one investment or goal, waiting for staff to check it.

    `session` is the client's own transaction. The same key returns the same transfer, and the
    same key with different details is refused. While the investment has a live deposit, by
    either method, this raises `DepositExists`. The caller stores the receipt file under
    `receipt_key(deposit_id)` first, with `deposit_id` from `new_deposit_id`. A replay returns
    the first transfer, and the file stored for the retry is left under a key nothing records.
    """
    if actor.kind != "client" or actor.id is None:
        raise ApplicationError("forbidden")
    now = clock.now()
    try:
        validate_push_id(request.purpose_id)
    except ValueError:
        raise _invalid("purpose_id", "invalid_id") from None
    try:
        file = check_receipt_file(request.file)
        details = check_receipt_details(request.details, today=business_date(now))
    except InvalidReceipt as error:
        raise _invalid(error.field, error.code) from None
    client_id = actor.id
    if deposit_id is not None:
        try:
            validate_push_id(deposit_id)
        except ValueError:
            raise _invalid("deposit_id", "invalid_id") from None
    chosen_id = deposit_id or push_id()

    async def command() -> Receipt:
        deposit_id = chosen_id
        await service.insert_deposit(
            session,
            {
                "id": deposit_id,
                "client_id": client_id,
                "purpose_type": request.purpose.value,
                "purpose_id": request.purpose_id,
                "method": Method.BANK_TRANSFER.value,
                "provider": BANK_PROVIDER,
                "currency": details.amount.currency.value,
                "amount_minor": details.amount.minor,
                "payer_msisdn": None,
                "status": DepositStatus.IN_REVIEW.value,
                "now": now,
            },
        )
        await session.execute(
            _INSERT_RECEIPT,
            {
                "deposit_id": deposit_id,
                "client_id": client_id,
                "storage_key": receipt_key(deposit_id),
                "content_type": file.content_type,
                "byte_size": file.byte_size,
                "sha256": file.sha256,
                "sent_on": details.sent_on,
                "bank_name": details.bank_name,
                "transaction_number": details.transaction_number,
                "now": now,
            },
        )
        await service.record_event(
            session, deposit_id, None, DepositStatus.IN_REVIEW, None, "request", now
        )
        if not _file_stored:
            # A preflight: everything that could refuse this transfer has run.
            raise _WouldSave
        return Receipt(201, {"deposit_id": deposit_id})

    outcome = await run_once(
        session,
        actor=actor,
        operation=SUBMIT_OPERATION,
        key=idempotency_key,
        request={
            "purpose": request.purpose.value,
            "purpose_id": request.purpose_id,
            "currency": details.amount.currency.value,
            "amount_minor": details.amount.minor,
            "content_type": file.content_type,
            "byte_size": file.byte_size,
            "sha256": file.sha256,
            "sent_on": details.sent_on.isoformat(),
            "bank_name": details.bank_name,
            "transaction_number": details.transaction_number,
        },
        now=now,
        command=command,
    )
    saved_id = str(outcome.receipt.response["deposit_id"])
    if not outcome.replayed:
        logger.info("bank_transfer_submitted", deposit_id=saved_id)
    return await get_bank_transfer(session, saved_id)


async def get_bank_transfer(session: AsyncSession, deposit_id: str) -> BankTransfer:
    """The transfer and its receipt, if this transaction may see them."""
    deposit = await service.find_deposit(session, deposit_id)
    if deposit is None or deposit.method is not Method.BANK_TRANSFER:
        raise ApplicationError("not_found")
    row = (await session.execute(_SELECT_RECEIPT, {"id": deposit_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return BankTransfer(deposit, _receipt(row))


async def candidate_lines(
    session: AsyncSession, staff: Actor, deposit_id: str
) -> tuple[StatementLineView, ...]:
    """Bank statement lines that could be this transfer, for the staff member to choose from.

    Money in, for exactly the amount owed, within the days the transfer can appear on, and not
    already used for another deposit.
    """
    transfer = await _in_review(session, staff, deposit_id)
    found = await _lines_for(session, transfer)
    used = set((await session.scalars(_USED_LINES, {"ids": [line.id for line in found]})).all())
    return tuple(line for line in found if line.id not in used and _problem(transfer, line) is None)


async def review_bank_transfer(
    session: AsyncSession,
    staff: Actor,
    deposit_id: str,
    review: Review,
    *,
    idempotency_key: str,
    clock: Clock,
) -> None:
    """Record a staff member's check of a bank transfer.

    `session` is the staff member's transaction. A match must name a line of an imported bank
    statement that came in, for exactly the amount owed, within the transfer's window, and that
    no other deposit used. It holds the line and waits for a second person to approve it. A
    refusal moves no money, so it needs one person and queues the job that fails the deposit.
    A transfer is reviewed once: a second review is refused, and a client whose transfer was not
    matched uploads a new receipt as a new deposit.
    """
    now = clock.now()

    async def command() -> Receipt:
        transfer = await _in_review(session, staff, deposit_id)
        values: dict[str, object] = {
            "deposit_id": deposit_id,
            "reviewed_by": staff.id,
            "now": now,
            "outcome": "not_matched",
            "reason": None,
            "statement_line_id": None,
            "account_reference": None,
        }
        if isinstance(review, Matched):
            line = await _chosen_line(session, transfer, review.statement_line_id)
            values |= {
                "outcome": "matched",
                "statement_line_id": line.id,
                "account_reference": line.account_reference,
            }
        else:
            values["reason"] = NotMatchedReason(review.reason).value
        try:
            async with session.begin_nested():
                await session.execute(_INSERT_REVIEW, values)
        except IntegrityError as error:
            message = str(error.orig)
            if _REVIEW_KEY in message:
                raise ApplicationError("conflict") from None
            if _LINE_KEY in message:
                raise _invalid("statement_line_id", "line_already_used") from None
            raise
        except DBAPIError as error:
            # The insert policy checks the transfer is still in review. It can only have moved on
            # if another review got there first and the worker has settled it.
            if getattr(error.orig, "sqlstate", None) == _INSUFFICIENT_PRIVILEGE:
                raise ApplicationError("conflict") from None
            raise
        if values["outcome"] == "not_matched":
            await _queue_settle(session, deposit_id, now)
        await audit.record(
            session,
            AuditEvent(
                category="approval",
                action="payments.bank_transfer_reviewed",
                resource_type="payment_deposit",
                resource_id=deposit_id,
                actor=AuditActor(staff.kind, staff.id),
                reason=str(values["outcome"]),
                details={
                    "outcome": values["outcome"],
                    "not_matched_reason": values["reason"],
                    "statement_line_id": values["statement_line_id"],
                },
            ),
            clock,
        )
        return Receipt(200, {"deposit_id": deposit_id, "outcome": values["outcome"]})

    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    request: dict[str, object] = (
        {"outcome": "matched", "statement_line_id": review.statement_line_id}
        if isinstance(review, Matched)
        else {"outcome": "not_matched", "reason": NotMatchedReason(review.reason).value}
    )
    outcome = await run_once(
        session,
        actor=staff,
        operation=REVIEW_OPERATION,
        key=idempotency_key,
        request={"deposit_id": deposit_id, **request},
        now=now,
        command=command,
    )
    if not outcome.replayed:
        logger.info("bank_transfer_reviewed", deposit_id=deposit_id, outcome=request["outcome"])


async def approve_bank_match(
    session: AsyncSession,
    staff: Actor,
    deposit_id: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> None:
    """A second member of staff approves a match, and the job that confirms it is queued.

    Refused from the person who made the match, for a transfer that was not matched, and for one
    already approved. The line is checked against the statement again, so an approval cannot
    confirm a match that has stopped being possible.
    """
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    now = clock.now()

    async def command() -> Receipt:
        transfer = await _in_review(session, staff, deposit_id)
        review = await get_review(session, staff, deposit_id)
        if review is None or review.outcome != "matched" or review.statement_line_id is None:
            raise ApplicationError("conflict")
        if review.reviewed_by == staff.id:
            # Two people: the one who matched it cannot also approve it.
            raise ApplicationError("forbidden")
        if review.approved_by is not None:
            raise ApplicationError("conflict")
        line = next(
            (
                found
                for found in await _lines_for(session, transfer)
                if found.id == review.statement_line_id
            ),
            None,
        )
        if line is None or _problem(transfer, line) is not None:
            raise _invalid("statement_line_id", "line_not_found")
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT_APPROVAL,
                    {"deposit_id": deposit_id, "approved_by": staff.id, "now": now},
                )
        except IntegrityError as error:
            if _APPROVAL_KEY in str(error.orig):
                raise ApplicationError("conflict") from None
            raise
        await _queue_settle(session, deposit_id, now)
        await audit.record(
            session,
            AuditEvent(
                category="approval",
                action="payments.bank_match_approved",
                resource_type="payment_deposit",
                resource_id=deposit_id,
                actor=AuditActor(staff.kind, staff.id),
                reason="second_person_approval",
                details={
                    "statement_line_id": review.statement_line_id,
                    "matched_by": review.reviewed_by,
                },
            ),
            clock,
        )
        return Receipt(200, {"deposit_id": deposit_id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"deposit_id": deposit_id},
        now=now,
        command=command,
    )
    if not outcome.replayed:
        logger.info("bank_match_approved", deposit_id=deposit_id)


async def get_review(session: AsyncSession, staff: Actor, deposit_id: str) -> ReviewView | None:
    """The review of a bank transfer, for staff. None if nobody has reviewed it yet."""
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    row = (await session.execute(_SELECT_REVIEW_VIEW, {"id": deposit_id})).one_or_none()
    if row is None:
        return None
    return ReviewView(
        outcome=row.outcome,
        reason=NotMatchedReason(row.reason) if row.reason else None,
        statement_line_id=row.statement_line_id,
        account_reference=row.account_reference,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
    )


async def record_opened(session: AsyncSession, staff: Actor, deposit_id: str, clock: Clock) -> None:
    """Write to the audit trail that a member of staff opened a transfer. Its receipt and the
    bank's narrative can name the client and the sender, so each look is recorded."""
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    await audit.record(
        session,
        AuditEvent(
            category="access",
            action="payments.bank_transfer_opened",
            resource_type="payment_deposit",
            resource_id=deposit_id,
            actor=AuditActor(staff.kind, staff.id),
        ),
        clock,
    )


async def matched_line(
    session: AsyncSession, staff: Actor, deposit_id: str
) -> StatementLineView | None:
    """The statement line a staff member matched the transfer to, for the second person to check
    beside the receipt before approving. None if the transfer is not matched."""
    review = await get_review(session, staff, deposit_id)
    if review is None or review.statement_line_id is None:
        return None
    transfer = await get_bank_transfer(session, deposit_id)
    return next(
        (
            found
            for found in await _lines_for(session, transfer)
            if found.id == review.statement_line_id
        ),
        None,
    )


class BankTransferJobs:
    """The worker's side: settle a reviewed transfer. Calls no provider."""

    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {SETTLE_KIND: self.settle}

    async def settle(self, job: ClaimedJob, context: JobContext) -> None:
        deposit_id = job.payload.get("deposit_id")
        if not isinstance(deposit_id, str):
            raise PermanentJobError(code="invalid_payload")
        async with self._database.actor_transaction(SYSTEM) as session:
            deposit = await service.find_deposit(session, deposit_id, lock=True)
            if deposit is None or deposit.method is not Method.BANK_TRANSFER:
                raise PermanentJobError(code="deposit_not_found")
            if deposit.status is not DepositStatus.IN_REVIEW:
                return
            row = (await session.execute(_SELECT_REVIEW, {"id": deposit_id})).one_or_none()
            if row is None:
                raise PermanentJobError(code="review_not_found")
            if row.outcome == "matched":
                approved = (await session.execute(_SELECT_APPROVED, {"id": deposit_id})).first()
                if approved is None:
                    # Waiting for a second person. Their approval queues this job.
                    return
                await service.apply_move(
                    session,
                    deposit,
                    Move(DepositStatus.CONFIRMED, post=deposit.amount),
                    self._clock,
                    cause="staff_review",
                    paid_into=bank_account(deposit.amount.currency, row.account_reference),
                )
            else:
                await service.apply_move(
                    session,
                    deposit,
                    Move(DepositStatus.FAILED, code=row.reason),
                    self._clock,
                    cause="staff_review",
                )


def bank_account(currency: Currency, account_reference: str) -> AccountSpec:
    """The ledger account for the bank account a transfer arrived in.

    For a cash pot reconciled each day, such as the trust account, it is the pot's own ledger
    account: the one the day close compares with the bank statement and dealing pays from.
    Any other bank account gets an account of its own, named by its statement reference.
    """
    for pot in CASH_POTS:
        if pot.key == (StatementSource.BANK, account_reference) and pot.currency is currency:
            return pot.ledger_account
    return AccountSpec(
        BANK_ACCOUNT_CODE,
        AccountKind.ASSET,
        currency,
        f"Bank account ({account_reference})",
        reference=account_reference,
    )


async def _queue_settle(session: AsyncSession, deposit_id: str, now: datetime) -> None:
    await enqueue(
        session,
        JobRequest(
            kind=SETTLE_KIND, idempotency_key=deposit_id, payload={"deposit_id": deposit_id}
        ),
        now=now,
    )


async def _in_review(session: AsyncSession, staff: Actor, deposit_id: str) -> BankTransfer:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    transfer = await get_bank_transfer(session, deposit_id)
    if transfer.deposit.status is not DepositStatus.IN_REVIEW:
        raise ApplicationError("conflict")
    return transfer


async def _lines_for(
    session: AsyncSession, transfer: BankTransfer
) -> tuple[StatementLineView, ...]:
    date_from, date_to = match_window(transfer.receipt.sent_on)
    return await find_statement_lines(
        session,
        source=StatementSource.BANK,
        date_from=date_from,
        date_to=date_to,
        amount=transfer.deposit.amount,
    )


async def _chosen_line(
    session: AsyncSession, transfer: BankTransfer, line_id: str
) -> StatementLineView:
    line = next(
        (found for found in await _lines_for(session, transfer) if found.id == line_id), None
    )
    if line is None:
        raise _invalid("statement_line_id", "line_not_found")
    problem = _problem(transfer, line)
    if problem is not None:
        raise _invalid("statement_line_id", problem)
    return line


def _problem(transfer: BankTransfer, line: StatementLineView) -> str | None:
    return match_problem(
        owed=transfer.deposit.amount,
        sent_on=transfer.receipt.sent_on,
        line=LineFacts(line.direction is Direction.IN, line.amount, line.value_date),
    )


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _receipt(row: Row[Any]) -> BankReceipt:
    return BankReceipt(
        deposit_id=row.deposit_id,
        storage_key=row.storage_key,
        content_type=row.content_type,
        byte_size=int(row.byte_size),
        sha256=row.sha256,
        sent_on=row.sent_on,
        bank_name=row.bank_name,
        transaction_number=row.transaction_number,
        created_at=row.created_at,
    )
