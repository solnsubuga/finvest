"""Client routes for where Finvest pays a client, and for the client's payouts.

A client adds a mobile money number or bank account, uploads proof that it is theirs, and sees
it move through staff review and the 24-hour safety wait. Money is never requested here: a
payout is created by the module whose money is paid out, such as an investment that matures or
is withdrawn early, and the client follows it with these routes.

A proof file is stored first, outside any transaction, then the destination is saved pointing
at it. A file that nothing records is deleted again.
"""

from datetime import date, datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.storage import FileStorage, InvalidFile, NewFile, StorageError
from app.modules.identity.api import CurrentClient
from app.modules.identity.codes import CodeService
from app.modules.payments.domain.bank_transfers import (
    MAX_RECEIPT_BYTES,
    RECEIPT_CONTENT_TYPES,
    InvalidReceipt,
    check_receipt_bytes,
)
from app.modules.payouts import destinations, payouts, returns
from app.modules.payouts.destinations import DestinationView
from app.modules.payouts.domain.banks import BANKS
from app.modules.payouts.domain.destinations import (
    BankAccount,
    DestinationError,
    DestinationKind,
    InvalidDestination,
    MobileMoneyNumber,
    ProofKind,
)
from app.modules.payouts.domain.payouts import PayoutStatus
from app.modules.payouts.payouts import PayoutView
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Money
from app.shared.push_ids import push_id

logger = structlog.get_logger()

MAX_PROOF_BYTES = MAX_RECEIPT_BYTES
_EXTENSIONS = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

client_router = APIRouter(prefix="/client", tags=["client payouts"], responses=_REFUSALS)
routers = [client_router]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _storage(request: Request) -> FileStorage:
    storage: FileStorage = request.app.state.storage
    return storage


def _codes(request: Request) -> CodeService:
    codes: CodeService = request.app.state.codes
    return codes


Codes = Annotated[CodeService, Depends(_codes)]
Db = Annotated[Database, Depends(_database)]
Files = Annotated[FileStorage, Depends(_storage)]
Time = Annotated[Clock, Depends(_clock)]


class MoneyView(Contract):
    minor: int
    currency: Literal["UGX", "USD"]


def _money(amount: Money) -> MoneyView:
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


class NewDestination(Contract):
    """A mobile money number (`phone`) or a bank account (`bank_code` and `account_number`).

    `holder_name` is the name the client says the account is in. Staff compare the name on the
    proof with the name on the client's ID, never with this.
    """

    kind: Literal["mobile_money", "bank"]
    holder_name: Annotated[str, Field(min_length=1, max_length=120)]
    phone: str | None = None
    bank_code: str | None = None
    account_number: str | None = None


class CodeAnswer(Contract):
    code: Annotated[str, Field(min_length=4, max_length=12)]


class DestinationOut(Contract):
    """A payout account. A new one is `awaiting_code` until the client enters the code texted
    for it, then `awaiting_proof`."""

    id: PushId
    kind: Literal["mobile_money", "bank"]
    network: Literal["mtn", "airtel"] | None
    bank_code: str | None
    # Only the last digits, as the client may see them again.
    account: str
    holder_name: str
    status: Literal["awaiting_code", "awaiting_proof", "in_review", "approved", "rejected"]
    proof_kind: (
        Literal["bank_statement", "bank_letter", "bank_card", "network_registered_name"] | None
    )
    rejection_reason: (
        Literal[
            "name_different",
            "proof_unreadable",
            "proof_too_old",
            "proof_wrong_account",
            "proof_wrong_document",
            "suspected_fraud",
        ]
        | None
    )
    # When it can first be paid: approval plus the 24-hour safety wait. None until approved.
    payable_from: datetime | None
    first_paid_at: datetime | None
    created_at: datetime


class PayoutOut(Contract):
    """A payout as the client sees it. `processing` covers every step from queued to the
    provider's answer; whether we are still finding out is for staff."""

    id: PushId
    destination_id: PushId
    kind: Literal["mobile_money", "bank"]
    amount: MoneyView
    status: Literal["in_review", "processing", "paid", "failed", "cancelled"]
    business_date: date
    created_at: datetime
    # Set when a paid payout came back from the bank or network. The money is safe with us, and
    # the client can send it again (`POST /client/payouts/{id}/resend`).
    returned_at: datetime | None = None
    # The payout it was sent again as, once the client sent it again. Null until then.
    resent_as: str | None = None


class ResendIn(Contract):
    destination_id: PushId


class LimitsOut(Contract):
    """The withdrawal limits in force today. A limit that is None is not set."""

    effective_from: date
    mobile_money_per_payout: MoneyView
    mobile_money_per_day: MoneyView | None
    bank_per_payout: MoneyView | None
    bank_per_day: MoneyView | None


_CLIENT_STATUS: dict[PayoutStatus, str] = {
    PayoutStatus.HELD: "in_review",
    PayoutStatus.QUEUED: "processing",
    PayoutStatus.SENT: "processing",
    PayoutStatus.UNCERTAIN: "processing",
    PayoutStatus.PAID: "paid",
    PayoutStatus.FAILED: "failed",
    PayoutStatus.CANCELLED: "cancelled",
}


def _destination_out(view: DestinationView) -> DestinationOut:
    return DestinationOut.model_validate(
        {
            "id": view.id,
            "kind": view.kind.value,
            "network": view.network,
            "bank_code": view.bank_code,
            "account": view.masked_account,
            "holder_name": view.holder_name,
            "status": view.status.value,
            "proof_kind": view.proof_kind.value if view.proof_kind else None,
            "rejection_reason": view.rejection_reason.value if view.rejection_reason else None,
            "payable_from": view.payable_from,
            "first_paid_at": view.first_paid_at,
            "created_at": view.created_at,
        }
    )


def _payout_out(
    view: PayoutView, returned: returns.ReturnView | None = None, resent_as: str | None = None
) -> PayoutOut:
    return PayoutOut.model_validate(
        {
            "id": view.id,
            "destination_id": view.destination_id,
            "kind": view.kind.value,
            "amount": _money(view.amount),
            "status": _CLIENT_STATUS[view.status],
            "business_date": view.business_date,
            "created_at": view.created_at,
            "returned_at": None if returned is None else returned.decided_at,
            "resent_as": resent_as,
        }
    )


def _problem(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _refused(code: str) -> ApplicationError:
    return ApplicationError("conflict", fields=[FieldProblem(field_path=["body"], code=code)])


async def _own_destination(
    session: AsyncSession, client_id: str, destination_id: str
) -> DestinationView:
    view = await destinations.get_destination(session, destination_id)
    if view.client_id != client_id or view.status.value == "removed":
        raise ApplicationError("not_found")
    return view


def _account(body: NewDestination) -> MobileMoneyNumber | BankAccount:
    try:
        if body.kind == "mobile_money":
            if body.phone is None or body.bank_code or body.account_number:
                raise _problem("phone", "required")
            return MobileMoneyNumber.parse(body.phone)
        if body.bank_code is None or body.account_number is None or body.phone:
            raise _problem("account_number", "required")
        return BankAccount(body.bank_code.strip(), body.account_number.replace(" ", ""))
    except InvalidDestination as error:
        field = "phone" if body.kind == "mobile_money" else "account_number"
        if error.code == "bank_invalid":
            field = "bank_code"
        raise _problem(field, error.code) from None


class BankOut(Contract):
    code: str
    name: str


@client_router.get("/banks")
async def banks(client: CurrentClient) -> list[BankOut]:
    """The banks a client may be paid to, by name. A payout account names one by its code."""
    return [BankOut(code=bank.code, name=bank.name) for bank in BANKS]


@client_router.get("/payout-destinations")
async def list_destinations(client: CurrentClient, database: Db) -> list[DestinationOut]:
    """The client's payout accounts that are not removed, newest first."""
    async with database.actor_transaction(client.actor) as session:
        views = await destinations.destinations_for_client(session, client.client_id)
    return [_destination_out(view) for view in views]


@client_router.post("/payout-destinations", status_code=201)
async def add_destination(
    body: NewDestination, client: CurrentClient, database: Db, clock: Time
) -> DestinationOut:
    """Add a mobile money number or bank account. It starts awaiting the SMS code.

    409 `conflict` if the client already has the same account saved.
    """
    account = _account(body)
    async with database.actor_transaction(client.actor) as session:
        view = await destinations.add_destination(
            session, client.actor, account, body.holder_name, clock
        )
    return _destination_out(view)


@client_router.get("/payout-destinations/{destination_id}")
async def get_destination(
    destination_id: PushId, client: CurrentClient, database: Db
) -> DestinationOut:
    async with database.actor_transaction(client.actor) as session:
        return _destination_out(await _own_destination(session, client.client_id, destination_id))


@client_router.delete("/payout-destinations/{destination_id}", status_code=204)
async def remove_destination(
    destination_id: PushId, client: CurrentClient, database: Db, clock: Time
) -> Response:
    """Take the account out of use for good. A payout already on its way is not stopped."""
    async with database.actor_transaction(client.actor) as session:
        await _own_destination(session, client.client_id, destination_id)
        try:
            await destinations.remove(session, client.actor, destination_id, clock)
        except DestinationError as error:
            raise _refused(error.code) from None
    return Response(status_code=204)


@client_router.post("/payout-destinations/{destination_id}/code", status_code=204)
async def send_code(
    destination_id: PushId, client: CurrentClient, database: Db, codes: Codes
) -> Response:
    """Text a code that proves the client holds the account.

    For a mobile money number the code goes to that number. For a bank account it goes to the
    client's own number. A new code replaces the one before it; 429 after three in 15 minutes.
    409 if the account is not waiting for its code.
    """
    async with database.actor_transaction(client.actor) as session:
        view = await _own_destination(session, client.client_id, destination_id)
        if view.status.value != "awaiting_code":
            raise _refused("destination_not_awaiting_code")
        if view.kind is DestinationKind.MOBILE_MONEY:
            _, msisdn = await destinations.mobile_money_number(session, destination_id)
            phone = f"+{msisdn}"
        else:
            phone = client.phone
        await codes.send_payout_account_code(
            session, client_id=client.client_id, account_id=destination_id, phone=phone
        )
    return Response(status_code=204)


@client_router.post("/payout-destinations/{destination_id}/code/verify")
async def verify_code(
    destination_id: PushId,
    body: CodeAnswer,
    client: CurrentClient,
    database: Db,
    clock: Time,
    codes: Codes,
) -> DestinationOut:
    """Enter the texted code. The account moves on to `awaiting_proof` in the same transaction
    that spends the code. 401 `invalid_code` for a wrong code, which still counts as a try;
    410 `expired` when no live code is left, after five tries or ten minutes."""
    async with database.actor_transaction(client.actor) as session:
        view = await _own_destination(session, client.client_id, destination_id)
        if view.status.value != "awaiting_code":
            raise _refused("destination_not_awaiting_code")
        right = await codes.spend_payout_account_code(
            session, client_id=client.client_id, account_id=destination_id, code=body.code
        )
        if right:
            view = await destinations.confirm_code(session, client.actor, destination_id, clock)
    # A wrong try is committed above, so it counts, and only then refused.
    if not right:
        raise ApplicationError("invalid_code")
    return _destination_out(view)


@client_router.post("/payout-destinations/{destination_id}/proof")
async def upload_proof(
    destination_id: PushId,
    proof_kind: Annotated[Literal["bank_statement", "bank_letter", "bank_card"], Form()],
    file: Annotated[UploadFile, File(description="PDF, JPEG or PNG, at most 10 MiB")],
    client: CurrentClient,
    database: Db,
    clock: Time,
    storage: Files,
) -> DestinationOut:
    """Upload proof that a bank account is the client's, and send it to staff.

    A `multipart/form-data` body. A bank statement must be dated within the last three months.
    After a refusal the client may upload another. 409 if the account is not waiting for proof.
    """
    data = await file.read(MAX_PROOF_BYTES + 1)
    content_type = file.content_type or ""
    if content_type not in RECEIPT_CONTENT_TYPES:
        raise _problem("file", "unsupported_content_type")
    if not 0 < len(data) <= MAX_PROOF_BYTES:
        raise _problem("file", "invalid_size")
    try:
        check_receipt_bytes(content_type, data)
    except InvalidReceipt as error:
        raise _problem("file", error.code) from None
    kind = ProofKind(proof_kind)
    # Refuse before storing anything, so a refused upload leaves no file behind.
    async with database.actor_transaction(client.actor) as session:
        view = await _own_destination(session, client.client_id, destination_id)
        if view.kind is not DestinationKind.BANK:
            raise _refused("proof_not_for_this_account")
        if view.status.value not in ("awaiting_proof", "rejected"):
            raise _refused("destination_not_awaiting_proof")

    document_id = f"{push_id(clock)}_{_EXTENSIONS[content_type]}"
    key = proof_key(destination_id, document_id)
    try:
        await storage.put(NewFile(key, data, content_type))
    except InvalidFile:
        raise _problem("file", "unsupported_content_type") from None
    except StorageError:
        raise ApplicationError("unavailable") from None
    try:
        async with database.actor_transaction(client.actor) as session:
            view = await destinations.submit_proof(
                session, client.actor, destination_id, kind, document_id, clock
            )
    except DestinationError as error:
        await _discard(storage, key, destination_id)
        raise _refused(error.code) from None
    except Exception:
        await _discard(storage, key, destination_id)
        raise
    return _destination_out(view)


def proof_key(destination_id: str, document_id: str) -> str:
    """Where a destination's proof file is stored. The document ID ends in its type: `_pdf`."""
    return f"payouts/{destination_id}/{document_id}"


async def _discard(storage: FileStorage, key: str, destination_id: str) -> None:
    try:
        await storage.delete(key)
    except StorageError:
        logger.warning("proof_orphaned", destination_id=destination_id)


@client_router.get("/payouts")
async def list_payouts(client: CurrentClient, database: Db) -> list[PayoutOut]:
    """The client's payouts, newest first, at most 50."""
    async with database.actor_transaction(client.actor) as session:
        views = await payouts.payouts_for_client(session, client.client_id)
        returned = await returns.approved_for(session, [view.id for view in views])
        resent = await returns.resent_as(session, client.client_id, returned)
    return [_payout_out(view, returned.get(view.id), resent.get(view.id)) for view in views]


@client_router.get("/payouts/{payout_id}")
async def get_payout(payout_id: PushId, client: CurrentClient, database: Db) -> PayoutOut:
    async with database.actor_transaction(client.actor) as session:
        view = await payouts.get_payout(session, payout_id)
        if view.client_id != client.client_id:
            raise ApplicationError("not_found")
        returned = await returns.approved_for(session, [view.id])
        resent = await returns.resent_as(session, client.client_id, returned)
    return _payout_out(view, returned.get(view.id), resent.get(view.id))


@client_router.post("/payouts/{payout_id}/resend", status_code=201)
async def resend(
    payout_id: PushId, body: ResendIn, client: CurrentClient, database: Db, clock: Time
) -> PayoutOut:
    """Send a payout that came back again, to an approved account. A new payout, returned here.

    Asking again is the same payout, so a lost answer can be tried again as it was. 409
    `payout_not_returned`; 422 on `destination_id` with the refusal codes of a new payout, such
    as `destination_not_approved`, `destination_in_safety_wait` or `over_mobile_money_limit`;
    422 `idempotency_key_reused` when it was already sent again to another account.
    """
    try:
        async with database.actor_transaction(client.actor) as session:
            view = await returns.resend(
                session, client.actor, payout_id, destination_id=body.destination_id, clock=clock
            )
    except returns.ReturnRefused as error:
        raise _refused(error.code) from None
    except payouts.PayoutRefused as refused:
        raise _problem("destination_id", refused.code) from None
    return _payout_out(view)


@client_router.get("/withdrawal-limits")
async def withdrawal_limits(client: CurrentClient, database: Db, clock: Time) -> LimitsOut:
    async with database.actor_transaction(client.actor) as session:
        limits = await payouts.current_limits(session, business_date(clock.now()))

    def optional(amount: Money | None) -> MoneyView | None:
        return None if amount is None else _money(amount)

    return LimitsOut(
        effective_from=limits.effective_from,
        mobile_money_per_payout=_money(limits.mobile_money_per_payout),
        mobile_money_per_day=optional(limits.mobile_money_per_day),
        bank_per_payout=optional(limits.bank_per_payout),
        bank_per_day=optional(limits.bank_per_day),
    )
