"""Staff routes for checking that a payout account is the client's own.

Two people approve an account: one records a check of the proof against the name on the
client's verified ID, and a different one approves that check. One person may refuse the
proof, and staff may remove an account for good with a reason. Seeing the queue and opening an
account needs `review_queue.view`; every decision needs `payout_accounts.change`.

Opening an account shows its whole number, the name on the ID and a short-lived link to the
proof, so each opening is written to the audit trail.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.storage import FileStorage, StorageError
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.modules.kyc import service as kyc
from app.modules.payouts import destinations
from app.modules.payouts.api import proof_key
from app.modules.payouts.destinations import DestinationView
from app.modules.payouts.domain.destinations import DestinationError, RejectionReason
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem

PROOF_LINK_LIFETIME = timedelta(minutes=5)
_PROOF_TYPES = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}
# Refusals that point at what the checker typed.
_FIELD_REFUSALS = {
    "name_different": "proof_name",
    "statement_date_required": "statement_date",
    "statement_too_old": "statement_date",
    "proof_kind_not_accepted": "proof_name",
}
_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

staff_router = APIRouter(prefix="/staff", tags=["staff payout accounts"], responses=_REFUSALS)
routers = [staff_router]


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
Viewer = Annotated[StaffSession, Depends(require_permission("review_queue.view"))]
Decider = Annotated[StaffSession, Depends(require_permission("payout_accounts.change"))]

Status = Literal["awaiting_code", "awaiting_proof", "in_review", "approved", "rejected", "removed"]
Rejection = Literal[
    "name_different",
    "proof_unreadable",
    "proof_too_old",
    "proof_wrong_account",
    "proof_wrong_document",
    "suspected_fraud",
]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class CheckOut(Contract):
    """The first person's check of the current proof."""

    proof_name: str
    name_match: Literal["exact", "partial"]
    statement_date: date | None
    checked_by: PushId
    checked_at: datetime


class StaffDestinationOut(Contract):
    """An account in the queue. `check` is None until the first person has checked it; an
    account with a check waits for a different person to approve it."""

    id: PushId
    client_id: PushId
    kind: Literal["mobile_money", "bank"]
    network: Literal["mtn", "airtel"] | None
    bank_code: str | None
    account: str
    holder_name: str
    status: Status
    proof_kind: (
        Literal["bank_statement", "bank_letter", "bank_card", "network_registered_name"] | None
    )
    check: CheckOut | None
    rejection_reason: Rejection | None
    approved_by: PushId | None
    approved_at: datetime | None
    payable_from: datetime | None
    created_at: datetime


class ProofLinkOut(Contract):
    url: str
    expires_at: datetime


class StaffDestinationDetail(StaffDestinationOut):
    """One account opened to check it: the whole number, the name on the verified ID (None if
    the client has no approved identity check) and a link to the proof that works for five
    minutes (None when there is no file or the store cannot be reached)."""

    account_number: str
    id_name: str | None
    proof_link: ProofLinkOut | None


class NewCheck(Contract):
    """What the first person read on the proof. The name is compared on the server."""

    proof_name: Annotated[str, Field(min_length=1, max_length=120)]
    # The date printed on a bank statement. Required for a statement, ignored otherwise.
    # Not strict, so the ISO date in the JSON body is read as a date.
    statement_date: Annotated[date | None, Field(strict=False)] = None
    reason: Reason


class Approval(Contract):
    reason: Reason


class Refusal(Contract):
    rejection_reason: Rejection
    reason: Reason


class Removal(Contract):
    reason: Reason


def _out(view: DestinationView) -> StaffDestinationOut:
    return StaffDestinationOut.model_validate(_fields(view))


def _fields(view: DestinationView) -> dict[str, object]:
    check = None
    if (
        view.status.value == "in_review"
        and view.checked_by is not None
        and view.checked_at is not None
        and view.proof_name is not None
        and view.name_match is not None
    ):
        check = {
            "proof_name": view.proof_name,
            "name_match": view.name_match.value,
            "statement_date": view.statement_date,
            "checked_by": view.checked_by,
            "checked_at": view.checked_at,
        }
    return {
        "id": view.id,
        "client_id": view.client_id,
        "kind": view.kind.value,
        "network": view.network,
        "bank_code": view.bank_code,
        "account": view.masked_account,
        "holder_name": view.holder_name,
        "status": view.status.value,
        "proof_kind": view.proof_kind.value if view.proof_kind else None,
        "check": check,
        "rejection_reason": view.rejection_reason.value if view.rejection_reason else None,
        "approved_by": view.approved_by,
        "approved_at": view.approved_at,
        "payable_from": view.payable_from,
        "created_at": view.created_at,
    }


def _proof_link(storage: FileStorage, view: DestinationView, clock: Clock) -> ProofLinkOut | None:
    document_id = view.proof_document_id
    if document_id is None or "_" not in document_id:
        return None
    content_type = _PROOF_TYPES.get(document_id.rsplit("_", 1)[1])
    if content_type is None:
        return None
    try:
        link = storage.read_link(
            proof_key(view.id, document_id),
            content_type=content_type,
            lifetime=PROOF_LINK_LIFETIME,
            now=clock.now(),
        )
    except StorageError:
        return None
    return ProofLinkOut(url=link.url, expires_at=link.expires_at)


async def _detail(
    session: AsyncSession, view: DestinationView, storage: FileStorage, clock: Clock
) -> StaffDestinationDetail:
    return StaffDestinationDetail.model_validate(
        {
            **_fields(view),
            "account_number": await destinations.account_number(session, view.id),
            "id_name": await kyc.verified_name(session, view.client_id),
            "proof_link": _proof_link(storage, view, clock),
        }
    )


def _refused(error: DestinationError) -> ApplicationError:
    if error.code == "checked_by_you":
        # The two-person rule: the same answer as approving your own bank transfer match.
        return ApplicationError(
            "forbidden", fields=[FieldProblem(field_path=["body"], code=error.code)]
        )
    field = _FIELD_REFUSALS.get(error.code)
    if field is not None:
        return ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["body", field], code=error.code)]
        )
    return ApplicationError("conflict", fields=[FieldProblem(field_path=["body"], code=error.code)])


@staff_router.get("/payout-destinations")
async def review_queue(staff: Viewer, database: Db) -> list[StaffDestinationOut]:
    """Accounts waiting for staff, oldest first: those with no check yet, and those with a
    check waiting for a second person."""
    async with database.actor_transaction(staff.actor) as session:
        views = await destinations.review_queue(session, limit=destinations.MAX_QUEUE_PAGE)
    return [_out(view) for view in views]


@staff_router.get("/payout-destinations/{destination_id}")
async def open_destination(
    destination_id: PushId, staff: Viewer, database: Db, clock: Time, storage: Files
) -> StaffDestinationDetail:
    """Open one account to check it. Writes a `payouts.destination_opened` access event."""
    async with database.actor_transaction(staff.actor) as session:
        view = await destinations.get_destination(session, destination_id)
        detail = await _detail(session, view, storage, clock)
        await destinations.record_opened(session, staff.actor, destination_id, clock)
    return detail


@staff_router.post("/payout-destinations/{destination_id}/check")
async def check_destination(
    destination_id: PushId, body: NewCheck, staff: Decider, database: Db, clock: Time
) -> StaffDestinationOut:
    """Record what you read on the proof. The account then waits for a different person.

    422 `name_different` when the name on the proof is not the client's; refuse the proof
    instead. 409 `client_not_verified` when the client has no approved identity check.
    """
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await destinations.check(
                session,
                staff.actor,
                destination_id,
                proof_name=body.proof_name,
                statement_date=body.statement_date,
                reason=body.reason,
                clock=clock,
            )
    except DestinationError as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-destinations/{destination_id}/approve")
async def approve_destination(
    destination_id: PushId, body: Approval, staff: Decider, database: Db, clock: Time
) -> StaffDestinationOut:
    """Approve someone else's check. The account can be paid 24 hours later.

    403 `checked_by_you` for whoever made the check. 409 `destination_not_checked` before a
    check, and `account_in_use` when the account is approved for another client.
    """
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await destinations.approve(
                session, staff.actor, destination_id, reason=body.reason, clock=clock
            )
    except DestinationError as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-destinations/{destination_id}/reject")
async def reject_destination(
    destination_id: PushId, body: Refusal, staff: Decider, database: Db, clock: Time
) -> StaffDestinationOut:
    """Refuse the proof. The client may send better proof, unless it is `suspected_fraud`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await destinations.reject(
                session,
                staff.actor,
                destination_id,
                RejectionReason(body.rejection_reason),
                body.reason,
                clock,
            )
    except DestinationError as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-destinations/{destination_id}/remove")
async def remove_destination(
    destination_id: PushId, body: Removal, staff: Decider, database: Db, clock: Time
) -> StaffDestinationOut:
    """Take an account out of use for good, recorded as an operator control. A payout already
    on its way is not stopped."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            await destinations.remove(
                session, staff.actor, destination_id, clock, reason=body.reason
            )
            view = await destinations.get_destination(session, destination_id)
    except DestinationError as error:
        raise _refused(error) from None
    return _out(view)
