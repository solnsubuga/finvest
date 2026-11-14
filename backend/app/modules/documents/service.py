"""Asking for statements, receipts and tax certificates, and reading them back. Each runs in
the caller's session.

A document is asked for first and built later by the worker (`jobs.py`), so no request waits
for the ledger to be read or a PDF to be drawn. Asking twice with the same key asks once.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.documents.domain.statements import (
    Period,
    PeriodRefused,
    requested_period,
)
from app.shared.actors import Actor
from app.shared.business_day import KAMPALA, business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

BUILD_KIND = "documents.build"
EMAIL_KIND = "documents.email"
MONTHLY_KIND = "documents.monthly_run"
EXPIRE_EMAILS_KIND = "documents.expire_emails"
# A client may ask for this many statements a day. Each one reads their whole history.
REQUESTS_PER_DAY = 10
MAX_PAGE_SIZE = 100
# The job queue documents are built on. One name, so tests can give themselves a queue.
QUEUE = "default"


class DocumentKind(StrEnum):
    MONTHLY_STATEMENT = "monthly_statement"
    REQUESTED_STATEMENT = "requested_statement"
    RECEIPT = "receipt"
    # Uganda's tax year, 1 July to 30 June, keyed by its label (`2026-27`). The only document
    # that shows tax withheld, and never a rate.
    TAX_CERTIFICATE = "tax_certificate"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DocumentView:
    """A document's record, without its file."""

    id: str
    client_id: str
    kind: DocumentKind
    idempotency_key: str
    period: Period | None
    source_id: str | None
    status: DocumentStatus
    failure_code: str | None
    requested_at: datetime
    finished_at: datetime | None
    file_name: str | None
    byte_size: int | None
    # For a statement the client asked for: the first day whose figures were not yet final.
    not_final_from: date | None = None


@dataclass(frozen=True, slots=True)
class DocumentFile:
    file_name: str
    content: bytes


_COLUMNS = (
    "id, client_id, kind, idempotency_key, period_start, period_end, source_id, status, "
    "failure_code, requested_at, finished_at, file_name, byte_size, not_final_from"
)
_INSERT = text(
    "INSERT INTO documents (id, client_id, kind, idempotency_key, period_start, period_end, "
    "source_id, requested_at) VALUES (:id, :client_id, :kind, :key, :period_start, "
    ":period_end, :source_id, :now) "
    "ON CONFLICT (client_id, kind, idempotency_key) DO NOTHING"
)
_BY_KEY = text(
    f"SELECT {_COLUMNS} FROM documents "  # noqa: S608 - constant column list
    "WHERE client_id = :client_id AND kind = :kind AND idempotency_key = :key"
)
_MONTH_ASKED = text(
    "SELECT EXISTS (SELECT FROM documents WHERE kind = 'monthly_statement' "
    "AND idempotency_key = :label)"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM documents WHERE id = :id")  # noqa: S608
_FILE = text(
    "SELECT client_id, file_name, content FROM documents WHERE id = :id AND status = 'ready'"
)
_REQUESTED_SINCE = text(
    "SELECT count(*) FROM documents WHERE client_id = :client_id "
    "AND kind = 'requested_statement' AND requested_at >= :since"
)
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} FROM documents "  # noqa: S608 - constant column list
    "WHERE client_id = :client_id AND status <> 'empty' "
    "ORDER BY requested_at DESC, id DESC LIMIT :limit"
)


async def request_statement(
    session: AsyncSession,
    client: Actor,
    *,
    date_from: date,
    date_to: date,
    idempotency_key: str,
    clock: Clock,
) -> DocumentView:
    """Ask for a statement of the client's chosen dates. The worker builds and sends it.

    Refuses a period that is backwards, ends after today or is longer than a year, with the
    reason on the field. The same key for the same dates returns the first request.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    now = clock.now()
    try:
        period = requested_period(date_from, date_to, today=business_date(now))
    except PeriodRefused as refused:
        field = "date_to" if refused.code == "period_in_future" else "date_from"
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=[field], code=refused.code)]
        ) from None
    earlier = await _by_key(session, client.id, DocumentKind.REQUESTED_STATEMENT, idempotency_key)
    if earlier is not None:
        if earlier.period != period:
            raise ApplicationError("idempotency_key_reused")
        return earlier
    since = datetime.combine(business_date(now), time(), tzinfo=KAMPALA)
    asked = int(
        (
            await session.execute(_REQUESTED_SINCE, {"client_id": client.id, "since": since})
        ).scalar_one()
    )
    if asked >= REQUESTS_PER_DAY:
        raise ApplicationError("rate_limited")
    view = await ask(
        session,
        client_id=client.id,
        kind=DocumentKind.REQUESTED_STATEMENT,
        key=idempotency_key,
        period=period,
        now=now,
    )
    if view.period != period:
        raise ApplicationError("idempotency_key_reused")
    return view


async def ask(
    session: AsyncSession,
    *,
    client_id: str,
    kind: DocumentKind,
    key: str,
    now: datetime,
    period: Period | None = None,
    source_id: str | None = None,
) -> DocumentView:
    """Record that a document is wanted and queue the job that builds it. Asking again with
    the same client, kind and key changes nothing and returns the first record."""
    document_id = push_id()
    saved = await session.execute(
        _INSERT,
        {
            "id": document_id,
            "client_id": client_id,
            "kind": kind.value,
            "key": key,
            "period_start": period.start if period else None,
            "period_end": period.end if period else None,
            "source_id": source_id,
            "now": now,
        },
    )
    if saved.rowcount == 1:  # type: ignore[attr-defined]
        # The job names the document by its ID: no name, figure or address is in the payload.
        await enqueue(
            session,
            JobRequest(
                kind=BUILD_KIND,
                idempotency_key=f"document:{document_id}",
                payload={"document_id": document_id},
                queue=QUEUE,
            ),
            now=now,
        )
    view = await _by_key(session, client_id, kind, key)
    assert view is not None
    return view


async def get_document(session: AsyncSession, document_id: str) -> DocumentView | None:
    """The record, if this transaction may see it: a client sees only their own."""
    row = (await session.execute(_BY_ID, {"id": document_id})).one_or_none()
    return None if row is None else _view(row)


async def client_documents(
    session: AsyncSession, client: Actor, *, limit: int = 50
) -> list[DocumentView]:
    """The client's statements and receipts, newest first. A month with nothing is left out."""
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    rows = await session.execute(
        _FOR_CLIENT, {"client_id": client.id, "limit": max(1, min(limit, MAX_PAGE_SIZE))}
    )
    return [_view(row) for row in rows.all()]


async def client_document(session: AsyncSession, client: Actor, document_id: str) -> DocumentView:
    if client.kind != "client":
        raise ApplicationError("forbidden")
    view = await get_document(session, document_id)
    if view is None or view.client_id != client.id or view.status is DocumentStatus.EMPTY:
        raise ApplicationError("not_found")
    return view


async def client_file(session: AsyncSession, client: Actor, document_id: str) -> DocumentFile:
    """The PDF of one of the client's ready documents."""
    if client.kind != "client":
        raise ApplicationError("forbidden")
    row = (await session.execute(_FILE, {"id": document_id})).one_or_none()
    if row is None or row.client_id != client.id:
        raise ApplicationError("not_found")
    return DocumentFile(row.file_name, bytes(row.content))


async def document_file(session: AsyncSession, document_id: str) -> DocumentFile | None:
    """The PDF of a ready document, for the worker that emails it."""
    row = (await session.execute(_FILE, {"id": document_id})).one_or_none()
    return DocumentFile(row.file_name, bytes(row.content)) if row is not None else None


async def _by_key(
    session: AsyncSession, client_id: str, kind: DocumentKind, key: str
) -> DocumentView | None:
    row = (
        await session.execute(_BY_KEY, {"client_id": client_id, "kind": kind.value, "key": key})
    ).one_or_none()
    return None if row is None else _view(row)


async def monthly_asked(session: AsyncSession, label: str) -> bool:
    """Whether the monthly run for `label` (`2026-10`) has started asking."""
    return bool((await session.execute(_MONTH_ASKED, {"label": label})).scalar_one())


def _view(row: Row[Any]) -> DocumentView:
    return DocumentView(
        id=row.id,
        client_id=row.client_id,
        kind=DocumentKind(row.kind),
        idempotency_key=row.idempotency_key,
        period=Period(row.period_start, row.period_end) if row.period_start else None,
        source_id=row.source_id,
        status=DocumentStatus(row.status),
        failure_code=row.failure_code,
        requested_at=row.requested_at,
        finished_at=row.finished_at,
        file_name=row.file_name,
        byte_size=row.byte_size,
        not_final_from=row.not_final_from,
    )
