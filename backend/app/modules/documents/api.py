"""Routes for the client's Documents screen: ask for a statement, list documents, download one."""

from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.documents import service
from app.modules.documents.service import DocumentKind, DocumentStatus, DocumentView
from app.modules.identity.api import CurrentClient
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
routers = [client_router]


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
