"""Routes for the client's Documents screen: ask for a statement, list documents, download one.
And for staff, sending investment notes again with a second person (`reissues.py`)."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.modules.documents import reissues, service
from app.modules.documents.service import DocumentKind, DocumentStatus, DocumentView
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

client_router = APIRouter(
    prefix="/client",
    tags=["client documents"],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
    },
)
staff_router = APIRouter(
    prefix="/staff",
    tags=["staff documents"],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
routers = [client_router, staff_router]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


# A calendar date as `YYYY-MM-DD`. Contracts are strict, so the text is parsed here on purpose.
IsoDate = Annotated[date, Field(strict=False)]


class StatementRequest(Contract):
    date_from: IsoDate
    date_to: IsoDate


class DocumentItem(Contract):
    id: PushId
    kind: Literal[
        "monthly_statement", "requested_statement", "receipt", "tax_certificate", "investment_note"
    ]
    # `pending` while the worker builds it, then `ready` to download, or `failed`.
    status: Literal["pending", "ready", "failed"]
    date_from: date | None
    date_to: date | None
    requested_at: datetime
    finished_at: datetime | None
    file_name: str | None
    byte_size: int | None
    # A statement for dates the ledger has not all closed yet: the first day whose figures
    # may still change. Null when every figure is final.
    not_final_from: date | None
    # What the document is about, so the app can offer it where that thing is shown: the
    # investment an investment note confirms, or the payment a receipt is for. Null otherwise.
    investment_id: str | None
    deposit_id: str | None
    # What the PDF and its email call the document: "ST-", "RC-", "TC-" or "IN-" and its ID.
    reference: str
    # For an investment note, whether it confirms the investment started or ended. Null for
    # every other kind.
    note: Literal["started", "ended"] | None


class DocumentList(Contract):
    items: list[DocumentItem]


def _item(view: DocumentView) -> DocumentItem:
    if view.status is DocumentStatus.EMPTY:
        raise ApplicationError("not_found")  # a month with nothing is never shown
    return DocumentItem(
        id=view.id,
        kind=view.kind.value,
        status=view.status.value,  # type: ignore[arg-type]
        date_from=view.period.start if view.period else None,
        date_to=view.period.end if view.period else None,
        requested_at=view.requested_at,
        finished_at=view.finished_at,
        file_name=view.file_name,
        byte_size=view.byte_size,
        not_final_from=view.not_final_from,
        investment_id=view.source_id if view.kind is DocumentKind.INVESTMENT_NOTE else None,
        deposit_id=view.source_id if view.kind is DocumentKind.RECEIPT else None,
        reference=view.reference,
        note=view.note.value if view.note else None,
    )


@client_router.post(
    "/statements",
    status_code=202,
    responses={422: {"model": ErrorEnvelope}, 429: {"model": ErrorEnvelope}},
)
async def ask_for_statement(
    body: StatementRequest,
    client: CurrentClient,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> DocumentItem:
    """Ask for a statement of any dates up to a year, ending no later than today.

    It is built in the background: poll the document until it is `ready`, then download it.
    It is also emailed to the client when they have an email address.
    """
    async with database.actor_transaction(client.actor) as session:
        view = await service.request_statement(
            session,
            client.actor,
            date_from=body.date_from,
            date_to=body.date_to,
            idempotency_key=idempotency_key,
            clock=clock,
        )
    return _item(view)


@client_router.get("/documents")
async def list_documents(
    client: CurrentClient,
    database: Db,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 50,
) -> DocumentList:
    """The client's statements and receipts, newest first."""
    async with database.actor_transaction(client.actor) as session:
        views = await service.client_documents(session, client.actor, limit=limit)
    return DocumentList(items=[_item(view) for view in views])


@client_router.get("/documents/{document_id}")
async def get_document(document_id: PushId, client: CurrentClient, database: Db) -> DocumentItem:
    async with database.actor_transaction(client.actor) as session:
        return _item(await service.client_document(session, client.actor, document_id))


@client_router.get(
    "/documents/{document_id}/file",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_document(document_id: PushId, client: CurrentClient, database: Db) -> Response:
    """The PDF. Sent as a download and never cached, because it shows what the client holds."""
    async with database.actor_transaction(client.actor) as session:
        found = await service.client_file(session, client.actor, document_id)
    return Response(
        content=found.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(found.file_name)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# Staff: sending investment notes again ------------------------------------------------------------

# Seeing requests needs `investments.view`. Asking, withdrawing and deciding need
# `client_statements.send`, the permission for sending documents to clients, and deciding also
# a passkey or app code from the last few minutes.
NotesViewer = Annotated[StaffSession, Depends(require_permission("investments.view"))]
NotesSender = Annotated[StaffSession, Depends(require_permission("client_statements.send"))]
NotesApprover = Annotated[StaffSession, Depends(require_confirmed("client_statements.send"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
ReissueState = Literal["proposed", "approved", "declined", "withdrawn", "done"]


class ReissueIn(Contract):
    # One client's notes, or every client's when left out.
    client_id: PushId | None = None
    issued_from: IsoDate
    issued_to: IsoDate
    reason: Reason


class ReissueDecline(Contract):
    note: Reason


class ReissueCopies(Contract):
    """How the notes sent again went, once the worker has queued them."""

    queued: int
    sent: int
    skipped: int
    failed: int


class ReissueOut(Contract):
    """A request to email investment notes again: the same stored notes, with the same numbers.
    It waits for a different person to approve it, and approving queues the sending."""

    id: PushId
    client_id: PushId | None
    issued_from: date
    issued_to: date
    reason: str
    status: ReissueState
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_note: str | None
    # How many notes it covers: now while it waits, and as sent once done.
    note_count: int
    copies: ReissueCopies
    finished_at: datetime | None


class ReissueCount(Contract):
    note_count: int


async def _reissue_out(
    session: AsyncSession, staff: StaffSession, found: list[reissues.Reissue], clock: Clock
) -> list[ReissueOut]:
    counts = await reissues.copy_counts(session, staff.actor, [r.id for r in found])
    out: list[ReissueOut] = []
    for request in found:
        if request.sent_count is not None:
            note_count = request.sent_count
        else:
            note_count = await reissues.count_notes(
                session,
                staff.actor,
                client_id=request.client_id,
                issued_from=request.issued_from,
                issued_to=request.issued_to,
                clock=clock,
                checked=False,
            )
        copies = counts[request.id]
        out.append(
            ReissueOut(
                id=request.id,
                client_id=request.client_id,
                issued_from=request.issued_from,
                issued_to=request.issued_to,
                reason=request.reason,
                status=request.status,
                proposed_by=request.proposed_by,
                proposed_at=request.proposed_at,
                decided_by=request.decided_by,
                decided_at=request.decided_at,
                decision_note=request.decision_note,
                note_count=note_count,
                copies=ReissueCopies(
                    queued=copies.queued,
                    sent=copies.sent,
                    skipped=copies.skipped,
                    failed=copies.failed,
                ),
                finished_at=request.finished_at,
            )
        )
    return out


@staff_router.get("/note-reissues")
async def staff_note_reissues(
    database: Db,
    clock: Time,
    staff: NotesViewer,
    status: ReissueState | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReissueOut]:
    """Requests to send investment notes again, newest first."""
    async with database.actor_transaction(staff.actor) as session:
        found = await reissues.list_reissues(session, staff.actor, status=status, limit=limit)
        return await _reissue_out(session, staff, found, clock)


@staff_router.get("/note-reissues/count")
async def staff_note_reissue_count(
    database: Db,
    clock: Time,
    staff: NotesSender,
    issued_from: Annotated[date, Query()],
    issued_to: Annotated[date, Query()],
    client_id: Annotated[PushId | None, Query()] = None,
) -> ReissueCount:
    """How many notes a request for these days, and this client if named, would send again."""
    async with database.actor_transaction(staff.actor) as session:
        count = await reissues.count_notes(
            session,
            staff.actor,
            client_id=client_id,
            issued_from=issued_from,
            issued_to=issued_to,
            clock=clock,
        )
    return ReissueCount(note_count=count)


async def _reissue_command(
    database: Database,
    staff: StaffSession,
    clock: Clock,
    *,
    operation: str,
    key: str,
    body: dict[str, object],
    command: Callable[[AsyncSession], Awaitable[reissues.Reissue]],
) -> ReissueOut:
    """Run one reissue command once per key, and answer with the request as it is now."""
    async with database.actor_transaction(staff.actor) as session:

        async def run() -> Receipt:
            found = await command(session)
            return Receipt(200, {"note_reissue_id": found.id})

        outcome = await run_once(
            session,
            actor=staff.actor,
            operation=operation,
            key=key,
            request=body,
            now=clock.now(),
            command=run,
        )
        request_id = outcome.receipt.response.get("note_reissue_id")
        assert isinstance(request_id, str)
        found = await reissues.get_reissue(session, staff.actor, request_id)
        [out] = await _reissue_out(session, staff, [found], clock)
        return out


@staff_router.post("/note-reissues", status_code=201)
async def staff_propose_note_reissue(
    database: Db, clock: Time, staff: NotesSender, key: IdempotencyKey, body: ReissueIn
) -> ReissueOut:
    """Ask for investment notes to be emailed again. A second person must approve it."""
    return await _reissue_command(
        database,
        staff,
        clock,
        operation="documents.propose_note_reissue",
        key=key,
        body=body.model_dump(mode="json"),
        command=lambda session: reissues.propose_reissue(
            session,
            staff.actor,
            client_id=body.client_id,
            issued_from=body.issued_from,
            issued_to=body.issued_to,
            reason=body.reason,
            clock=clock,
        ),
    )


@staff_router.post("/note-reissues/{request_id}/approve")
async def staff_approve_note_reissue(
    database: Db, clock: Time, staff: NotesApprover, key: IdempotencyKey, request_id: PushId
) -> ReissueOut:
    """Approve someone else's request. The notes are queued to send at once."""
    return await _reissue_command(
        database,
        staff,
        clock,
        operation="documents.approve_note_reissue",
        key=key,
        body={"note_reissue_id": request_id},
        command=lambda session: reissues.approve_reissue(session, staff.actor, request_id, clock),
    )


@staff_router.post("/note-reissues/{request_id}/decline")
async def staff_decline_note_reissue(
    database: Db,
    clock: Time,
    staff: NotesApprover,
    key: IdempotencyKey,
    request_id: PushId,
    body: ReissueDecline,
) -> ReissueOut:
    return await _reissue_command(
        database,
        staff,
        clock,
        operation="documents.decline_note_reissue",
        key=key,
        body={"note_reissue_id": request_id, "note": body.note},
        command=lambda session: reissues.decline_reissue(
            session, staff.actor, request_id, body.note, clock
        ),
    )


@staff_router.post("/note-reissues/{request_id}/withdraw")
async def staff_withdraw_note_reissue(
    database: Db, clock: Time, staff: NotesSender, key: IdempotencyKey, request_id: PushId
) -> ReissueOut:
    return await _reissue_command(
        database,
        staff,
        clock,
        operation="documents.withdraw_note_reissue",
        key=key,
        body={"note_reissue_id": request_id},
        command=lambda session: reissues.withdraw_reissue(session, staff.actor, request_id, clock),
    )
