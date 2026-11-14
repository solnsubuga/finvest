"""Staff routes for checking the custodian's holdings statement.

See docs/reconciliation.md#custodian.

Seeing checks needs `reconciliation.view`. Uploading, correcting a row, explaining a difference
and sending for sign-off need `reconciliation.change`. Signing off and sending back also need a
passkey or app code from the last few minutes, and are refused to whoever sent the check for
sign-off. Upload, send, sign-off and send back take an `Idempotency-Key`.
"""

import contextlib
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.storage import FileStorage, InvalidFile, NewFile, StorageError
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.reconciliation import custodian
from app.modules.reconciliation.custodian_settings import CustodianReaderSettings
from app.modules.reconciliation.domain.custodian import (
    XLSX,
    CheckStatus,
    CustodianCheckError,
)
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.push_ids import push_id

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
FILE_LINK_LIFETIME = timedelta(minutes=5)

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
}
# Refusals that mean the request itself is wrong, as opposed to the state of the check.
_INVALID = frozenset(
    {
        "statement_date_in_the_future",
        "note_invalid",
        "row_amount_not_positive",
        "correction_empty",
    }
)

router = APIRouter(
    prefix="/staff/reconciliation/custodian-checks",
    tags=["staff reconciliation"],
    responses=_REFUSALS,
)
routers = [router]


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
Time = Annotated[Clock, Depends(_clock)]
Store = Annotated[FileStorage, Depends(_storage)]
Viewer = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
Changer = Annotated[StaffSession, Depends(require_permission("reconciliation.change"))]
# The second signature needs a passkey or app code from the last few minutes.
Signer = Annotated[StaffSession, Depends(require_confirmed("reconciliation.change"))]

Note = Annotated[str, Field(min_length=1, max_length=1000)]
Day = Annotated[date, Field(strict=False)]
Action = Literal["correct", "note", "submit", "sign_off", "send_back"]


class CheckOut(Contract):
    id: PushId
    statement_date: date
    status: Literal["reading", "unreadable", "to_review", "waiting_sign_off", "signed_off"]
    file_content_type: str
    file_bytes: int
    uploaded_by: PushId
    uploaded_at: datetime
    # Which model read it, such as `claude:claude-opus-5-5`, or the stub.
    reader: str | None
    read_at: datetime | None
    # The date the statement itself gives, which may differ from `statement_date`.
    read_statement_date: date | None
    custodian_name: str | None
    account_reference: str | None
    amount_scale: Literal["one", "thousand", "million"] | None
    # Why it could not be read, such as `reader_declined` or `statement_not_a_spreadsheet`.
    failure_code: str | None
    submitted_by: PushId | None
    submitted_at: datetime | None
    submit_note: str | None
    signed_off_by: PushId | None
    signed_off_at: datetime | None
    sign_off_note: str | None
    sent_back_by: PushId | None
    sent_back_note: str | None
    # What the person asking may do now.
    allowed: list[Action]


class ChecksOut(Contract):
    # False when no reader is set up here: uploads are refused with `custodian_reading_off`.
    reading_on: bool
    checks: list[CheckOut]


class RowOut(Contract):
    id: PushId
    line_no: int
    # Exactly as the reader copied it from the statement.
    instrument: str
    code: str | None
    face_value_text: str
    currency_text: str | None
    matures_on_text: str | None
    location: str
    source_text: str
    # What Finvest's code made of it, or why it could not.
    currency: Literal["UGX", "USD"] | None
    face_minor: int | None
    matures_on: date | None
    problem: str | None
    corrected_face_minor: int | None
    corrected_matures_on: date | None
    left_out: bool
    corrected_by: PushId | None
    correction_reason: str | None


class ItemOut(Contract):
    key: str
    kind: Literal[
        "match",
        "amount_differs",
        "missing_at_custodian",
        "not_in_finvest",
        "row_unreadable",
        "statement_date_differs",
        "statement_date_unread",
    ]
    currency: Literal["UGX", "USD"] | None
    matures_on: date | None
    finvest_face_minor: int
    custodian_face_minor: int
    # Custodian minus Finvest. Negative when the custodian shows less.
    difference_minor: int
    product_codes: list[str]
    row_ids: list[str]
    problem: str | None
    needs_note: bool
    note: str | None
    noted_by: PushId | None


class CheckDetailOut(Contract):
    check: CheckOut
    rows: list[RowOut]
    items: list[ItemOut]
    # Differences with no note yet. The check can be sent for sign-off when this is zero.
    unexplained: int


class FileLinkOut(Contract):
    url: str
    content_type: str
    expires_at: datetime


class CorrectionIn(Contract):
    # Leave the row out of the comparison, such as a cash line the reader took for a bill.
    left_out: bool = False
    face_minor: Annotated[int, Field(gt=0)] | None = None
    matures_on: Day | None = None
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class NoteIn(Contract):
    note: Note


class OptionalNoteIn(Contract):
    note: Annotated[str, Field(max_length=1000)] | None = None


class ReasonIn(Contract):
    reason: Note


def _allowed(view: custodian.CheckView, staff: StaffSession) -> list[Action]:
    if "reconciliation.change" not in staff.permissions:
        return []
    if view.status is CheckStatus.TO_REVIEW:
        return ["correct", "note", "submit"]
    if view.status is CheckStatus.WAITING_SIGN_OFF and view.submitted_by != staff.staff_id:
        return ["sign_off", "send_back"]
    return []


def _check_out(view: custodian.CheckView, staff: StaffSession) -> CheckOut:
    return CheckOut(
        id=view.id,
        statement_date=view.statement_date,
        status=view.status.value,
        file_content_type=view.file_content_type,
        file_bytes=view.file_bytes,
        uploaded_by=view.uploaded_by,
        uploaded_at=view.uploaded_at,
        reader=view.reader,
        read_at=view.read_at,
        read_statement_date=view.read_statement_date,
        custodian_name=view.custodian_name,
        account_reference=view.account_reference,
        amount_scale=view.amount_scale.value if view.amount_scale else None,
        failure_code=view.failure_code,
        submitted_by=view.submitted_by,
        submitted_at=view.submitted_at,
        submit_note=view.submit_note,
        signed_off_by=view.signed_off_by,
        signed_off_at=view.signed_off_at,
        sign_off_note=view.sign_off_note,
        sent_back_by=view.sent_back_by,
        sent_back_note=view.sent_back_note,
        allowed=_allowed(view, staff),
    )


def _row_out(row: custodian.RowView) -> RowOut:
    return RowOut(
        id=row.id,
        line_no=row.line_no,
        instrument=row.instrument,
        code=row.code,
        face_value_text=row.face_value_text,
        currency_text=row.currency_text,
        matures_on_text=row.matures_on_text,
        location=row.location,
        source_text=row.source_text,
        currency=row.currency.value if row.currency else None,
        face_minor=row.face_minor,
        matures_on=row.matures_on,
        problem=row.problem,
        corrected_face_minor=row.corrected_face_minor,
        corrected_matures_on=row.corrected_matures_on,
        left_out=row.left_out,
        corrected_by=row.corrected_by,
        correction_reason=row.correction_reason,
    )


def _item_out(found: custodian.ItemView) -> ItemOut:
    item = found.item
    return ItemOut(
        key=item.key,
        kind=item.kind.value,
        currency=item.currency.value if item.currency else None,
        matures_on=item.matures_on,
        finvest_face_minor=item.finvest_face_minor,
        custodian_face_minor=item.custodian_face_minor,
        difference_minor=item.difference_minor,
        product_codes=list(item.product_codes),
        row_ids=list(item.row_ids),
        problem=item.problem,
        needs_note=item.needs_note,
        note=found.note,
        noted_by=found.noted_by,
    )


def _refused(error: CustodianCheckError) -> ApplicationError:
    if error.code == "check_needs_second_person":
        return ApplicationError("forbidden", fields=[_problem(error.code)])
    status: Literal["conflict", "validation_failed"] = (
        "validation_failed" if error.code in _INVALID else "conflict"
    )
    return ApplicationError(status, fields=[_problem(error.code)])


def _problem(code: str, *path: str) -> FieldProblem:
    return FieldProblem(field_path=list(path) or ["body"], code=code)


def sniff(data: bytes) -> str | None:
    """What the file really is, from its first bytes. A CSV is any text without NUL bytes."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"PK\x03\x04"):
        return XLSX
    if b"\x00" not in data[:4096]:
        return "text/csv"
    return None


async def _detail(
    session: AsyncSession, view: custodian.CheckView, staff: StaffSession
) -> CheckDetailOut:
    rows = await custodian.rows_of(session, view.id)
    items = await custodian.items_of(session, view.id)
    return CheckDetailOut(
        check=_check_out(view, staff),
        rows=[_row_out(row) for row in rows],
        items=[_item_out(item) for item in items],
        unexplained=sum(
            1 for found in items if found.item.needs_note and not (found.note or "").strip()
        ),
    )


@router.get("")
async def list_checks(staff: Viewer, database: Db) -> ChecksOut:
    """The latest checks, newest first, and whether statements can be read here."""
    async with database.actor_transaction(staff.actor) as session:
        found = await custodian.list_checks(session, limit=50)
    return ChecksOut(
        reading_on=CustodianReaderSettings().reading_on,
        checks=[_check_out(view, staff) for view in found],
    )


@router.post("", status_code=201)
async def upload(
    staff: Changer,
    database: Db,
    clock: Time,
    storage: Store,
    idempotency_key: IdempotencyKey,
    statement_date: Annotated[date, Form()],
    file: Annotated[UploadFile, File(description="PDF, XLSX, CSV, PNG or JPEG, at most 10 MiB")],
) -> CheckOut:
    """Upload the custodian's statement for the day it states holdings at. The worker reads it
    and compares it; read the check again until it is no longer `reading`.

    503 `custodian_reading_off` when no reader is set up; 422 `statement_file_size`,
    `statement_type_not_supported` or `statement_date_in_the_future`.
    """
    if not CustodianReaderSettings().reading_on:
        raise ApplicationError("unavailable", fields=[_problem("custodian_reading_off")])
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not 0 < len(data) <= MAX_UPLOAD_BYTES:
        raise ApplicationError(
            "validation_failed", fields=[_problem("statement_file_size", "file")]
        )
    content_type = sniff(data)
    if content_type is None:
        raise ApplicationError(
            "validation_failed", fields=[_problem("statement_type_not_supported", "file")]
        )
    check_id = push_id()
    key = custodian.file_key(check_id, push_id())
    try:
        stored = await storage.put(NewFile(key, data, content_type))
    except InvalidFile:
        raise ApplicationError(
            "validation_failed", fields=[_problem("statement_type_not_supported", "file")]
        ) from None
    except StorageError:
        # Nothing was saved, so the same file can be sent again.
        raise ApplicationError("unavailable") from None
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await custodian.upload(
                session,
                staff.actor,
                check_id=check_id,
                statement_date=statement_date,
                stored_key=stored.key,
                sha256=stored.sha256,
                content_type=stored.content_type,
                byte_size=stored.byte_size,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except CustodianCheckError as error:
        await _discard(storage, key)
        raise _refused(error) from None
    except Exception:
        await _discard(storage, key)
        raise
    if view.file_key != key:
        # A retry of an upload already recorded: keep only the first copy.
        await _discard(storage, key)
    return _check_out(view, staff)


async def _discard(storage: FileStorage, key: str) -> None:
    # An orphan file holds nothing the record points to; it is only space.
    with contextlib.suppress(StorageError):
        await storage.delete(key)


@router.get("/{check_id}")
async def get_check(check_id: PushId, staff: Viewer, database: Db) -> CheckDetailOut:
    """One check with every row as read beside what was made of it, and the comparison."""
    async with database.actor_transaction(staff.actor) as session:
        view = await custodian.get_check(session, check_id)
        return await _detail(session, view, staff)


@router.get("/{check_id}/file")
async def file_link(
    check_id: PushId, staff: Viewer, database: Db, clock: Time, storage: Store
) -> FileLinkOut:
    """A link to the uploaded statement that works for a few minutes."""
    async with database.actor_transaction(staff.actor) as session:
        view = await custodian.get_check(session, check_id)
    now = clock.now()
    try:
        link = storage.read_link(
            view.file_key,
            content_type=view.file_content_type,
            lifetime=FILE_LINK_LIFETIME,
            now=now,
        )
    except StorageError:
        raise ApplicationError("unavailable") from None
    return FileLinkOut(
        url=link.url, content_type=view.file_content_type, expires_at=link.expires_at
    )


@router.put("/{check_id}/rows/{row_id}")
async def correct_row(
    check_id: PushId,
    row_id: PushId,
    body: CorrectionIn,
    staff: Changer,
    database: Db,
    clock: Time,
) -> CheckDetailOut:
    """Put right a row the reader got wrong, or leave it out, with a reason. The comparison is
    worked out again. 409 `check_not_in_review`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await custodian.correct_row(
                session,
                staff.actor,
                check_id,
                row_id,
                face_minor=body.face_minor,
                matures_on=body.matures_on,
                left_out=body.left_out,
                reason=body.reason,
                clock=clock,
            )
            return await _detail(session, view, staff)
    except CustodianCheckError as error:
        raise _refused(error) from None


@router.put("/{check_id}/notes/{item_key}")
async def note_item(
    check_id: PushId,
    item_key: Annotated[str, Field(min_length=1, max_length=80)],
    body: NoteIn,
    staff: Changer,
    database: Db,
    clock: Time,
) -> CheckDetailOut:
    """Explain one difference. 409 `check_not_in_review`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            await custodian.note_item(session, staff.actor, check_id, item_key, body.note, clock)
            view = await custodian.get_check(session, check_id)
            return await _detail(session, view, staff)
    except CustodianCheckError as error:
        raise _refused(error) from None


@router.post("/{check_id}/submit")
async def submit(
    check_id: PushId,
    body: OptionalNoteIn,
    staff: Changer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> CheckDetailOut:
    """Send the check for sign-off. The comparison is worked out again first. 409
    `differences_unexplained` while a difference has no note, or `check_not_in_review`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await custodian.submit(
                session,
                staff.actor,
                check_id,
                body.note,
                idempotency_key=idempotency_key,
                clock=clock,
            )
            return await _detail(session, view, staff)
    except CustodianCheckError as error:
        raise _refused(error) from None


@router.post("/{check_id}/sign-off")
async def sign_off(
    check_id: PushId,
    body: OptionalNoteIn,
    staff: Signer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> CheckDetailOut:
    """The second signature. 403 `check_needs_second_person` for whoever sent it; 409
    `check_not_waiting`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await custodian.sign_off(
                session,
                staff.actor,
                check_id,
                body.note,
                idempotency_key=idempotency_key,
                clock=clock,
            )
            return await _detail(session, view, staff)
    except CustodianCheckError as error:
        raise _refused(error) from None


@router.post("/{check_id}/send-back")
async def send_back(
    check_id: PushId,
    body: ReasonIn,
    staff: Signer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> CheckDetailOut:
    """Send it back for more review, with a reason. 403 `check_needs_second_person`; 409
    `check_not_waiting`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await custodian.send_back(
                session,
                staff.actor,
                check_id,
                body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
            return await _detail(session, view, staff)
    except CustodianCheckError as error:
        raise _refused(error) from None
