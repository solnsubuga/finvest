"""Staff sending investment notes again, each time with a second person.

When a client lost a note's email, or a day's emails went wrong, one member of staff asks for
the notes to be sent again with a reason: one client's, or every client's, for the notes issued
between two days. A different one approves it, which queues `documents.reissue_notes` in the same
transaction, or declines it with a note. Only the proposer withdraws it. The database holds them
to that (migration 0335).

A note sent again is the same document: the stored PDF, with its number and figures, locked as
before and sent to the client's proved address under the same rules as the first email. Nothing
is built again, so a client is never told something different about the same investment. The
first email's record is never touched: each copy has its own row in `document_email_copies`.
See docs/documents.md, "Sending notes again".
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.documents import service
from app.shared.actors import Actor
from app.shared.business_day import KAMPALA, business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "note_reissue"
MAX_REASON_LENGTH = 500
MAX_REQUESTS = 200
# One request covers at most this many days of notes (migration 0335 holds the same).
MAX_DAYS = 31
# How far back a request may reach. Older notes are still in the app.
MAX_AGE_DAYS = 400
# Notes found and queued in one page of the worker's run.
PAGE = 500
logger = structlog.get_logger()

ReissueStatus = Literal["proposed", "approved", "declined", "withdrawn", "done"]


@dataclass(frozen=True, slots=True)
class Reissue:
    id: str
    client_id: str | None
    issued_from: date
    issued_to: date
    reason: str
    status: ReissueStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    job_id: str | None
    sent_count: int | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class CopyCounts:
    """How the copies of one request went, for the console."""

    queued: int
    sent: int
    skipped: int
    failed: int


_COLUMNS = (
    "id, client_id, issued_from, issued_to, reason, status, proposed_by, proposed_at, "
    "decided_by, decided_at, decision_note, job_id, sent_count, finished_at"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM note_reissues WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} FROM note_reissues WHERE id = :id FOR UPDATE")  # noqa: S608
_LIST = text(
    f"SELECT {_COLUMNS} FROM note_reissues "  # noqa: S608
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO note_reissues "
    "(id, client_id, issued_from, issued_to, reason, proposed_by, proposed_at) "
    "VALUES (:id, :client_id, :issued_from, :issued_to, :reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE note_reissues SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note, job_id = :job_id WHERE id = :id"
)
# The ready notes a request covers, by the Kampala day each was issued.
_SCOPE = (
    "FROM documents WHERE kind = 'investment_note' AND status = 'ready' "
    "AND (CAST(:client_id AS text) IS NULL OR client_id = :client_id) "
    "AND finished_at >= :start AND finished_at < :end"
)
_COUNT = text(f"SELECT count(*) {_SCOPE}")
_PAGE = text(f"SELECT id, client_id {_SCOPE} AND id > :after ORDER BY id LIMIT :limit")
_INSERT_COPY = text(
    "INSERT INTO document_email_copies (id, reissue_id, document_id, client_id, queued_at) "
    "VALUES (:id, :reissue_id, :document_id, :client_id, :now) "
    "ON CONFLICT (reissue_id, document_id) DO NOTHING RETURNING id"
)
_COPIES = text("SELECT count(*) FROM document_email_copies WHERE reissue_id = :id")
_COPY_COUNTS = text(
    "SELECT reissue_id, status, count(*) AS n FROM document_email_copies "
    "WHERE reissue_id = ANY(:ids) GROUP BY reissue_id, status"
)
_DONE = text(
    "UPDATE note_reissues SET status = 'done', sent_count = :sent, finished_at = :now "
    "WHERE id = :id AND status = 'approved'"
)


async def count_notes(
    session: AsyncSession,
    staff: Actor,
    *,
    client_id: str | None,
    issued_from: date,
    issued_to: date,
    clock: Clock,
    checked: bool = True,
) -> int:
    """How many notes a request for this client and these days would send again. A waiting
    request is counted with `checked=False`, since its days were checked when it was made."""
    _staff_only(staff)
    if checked:
        _check_days(issued_from, issued_to, business_date(clock.now()))
    return await _count(session, client_id, issued_from, issued_to)


async def propose_reissue(
    session: AsyncSession,
    staff: Actor,
    *,
    client_id: str | None,
    issued_from: date,
    issued_to: date,
    reason: str,
    clock: Clock,
) -> Reissue:
    """Ask for notes to be sent again, for a second member of staff to approve.

    422 on `reason` with `required`, on `issued_from` with `too_old` or `after_issued_to`, on
    `issued_to` with `not_yet` or `too_long`, and on `issued_from` with `no_notes` when the
    request would send nothing.
    """
    _staff_only(staff)
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise _invalid("reason", "required")
    _check_days(issued_from, issued_to, business_date(clock.now()))
    if await _count(session, client_id, issued_from, issued_to) == 0:
        raise _invalid("issued_from", "no_notes")
    request_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": request_id,
            "client_id": client_id,
            "issued_from": issued_from,
            "issued_to": issued_to,
            "reason": cleaned,
            "proposed_by": staff.id,
            "now": clock.now(),
        },
    )
    request = await _request(session, request_id)
    await _audit(session, staff, "change", "proposed", request, clock, reason=cleaned)
    return request


async def approve_reissue(
    session: AsyncSession, staff: Actor, request_id: str, clock: Clock
) -> Reissue:
    """Approve as the second person, and queue the sending in this transaction."""
    request = await _for_decision(session, staff, request_id)
    if request.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    now = clock.now()
    job_id = await enqueue(
        session,
        JobRequest(
            kind=service.REISSUE_KIND,
            idempotency_key=f"note-reissue:{request.id}",
            payload={"data": {"reissue_id": request.id, "after": ""}},
            queue=service.QUEUE,
        ),
        now=now,
    )
    if job_id is None:
        raise ApplicationError("conflict")
    await session.execute(
        _DECIDE,
        {
            "id": request.id,
            "status": "approved",
            "decided_by": staff.id,
            "now": now,
            "note": None,
            "job_id": job_id,
        },
    )
    approved = await _request(session, request.id)
    await _audit(session, staff, "approval", "approved", approved, clock, reason=approved.reason)
    logger.info("note_reissue_approved", note_reissue_id=request.id, job_id=job_id)
    return approved


async def decline_reissue(
    session: AsyncSession, staff: Actor, request_id: str, note: str, clock: Clock
) -> Reissue:
    """Decline as the second person, with a note for the proposer. Nothing is sent."""
    request = await _for_decision(session, staff, request_id)
    if request.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    cleaned = note.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise _invalid("note", "required")
    await session.execute(
        _DECIDE,
        {
            "id": request.id,
            "status": "declined",
            "decided_by": staff.id,
            "now": clock.now(),
            "note": cleaned,
            "job_id": None,
        },
    )
    declined = await _request(session, request.id)
    await _audit(session, staff, "approval", "declined", declined, clock, reason=cleaned)
    return declined


async def withdraw_reissue(
    session: AsyncSession, staff: Actor, request_id: str, clock: Clock
) -> Reissue:
    """Take back a request nobody has decided. Only its proposer can."""
    request = await _for_decision(session, staff, request_id)
    if request.proposed_by != staff.id:
        raise ApplicationError("forbidden")
    await session.execute(
        _DECIDE,
        {
            "id": request.id,
            "status": "withdrawn",
            "decided_by": staff.id,
            "now": clock.now(),
            "note": None,
            "job_id": None,
        },
    )
    withdrawn = await _request(session, request.id)
    await _audit(session, staff, "change", "withdrawn", withdrawn, clock)
    return withdrawn


async def list_reissues(
    session: AsyncSession,
    staff: Actor,
    *,
    status: ReissueStatus | None = None,
    limit: int = 50,
) -> list[Reissue]:
    """Reissue requests, newest first. For the console and the approvals inbox."""
    _staff_only(staff)
    rows = await session.execute(
        _LIST, {"status": status, "limit": max(1, min(limit, MAX_REQUESTS))}
    )
    return [_from_row(row) for row in rows.all()]


async def copy_counts(
    session: AsyncSession, staff: Actor, request_ids: list[str]
) -> dict[str, CopyCounts]:
    """How each request's copies went: still queued, sent, skipped or failed."""
    _staff_only(staff)
    found: dict[str, dict[str, int]] = {}
    if request_ids:
        for row in (await session.execute(_COPY_COUNTS, {"ids": request_ids})).all():
            found.setdefault(row.reissue_id, {})[row.status] = row.n
    return {
        request_id: CopyCounts(
            queued=found.get(request_id, {}).get("queued", 0),
            sent=found.get(request_id, {}).get("sent", 0),
            skipped=found.get(request_id, {}).get("skipped", 0),
            failed=found.get(request_id, {}).get("failed", 0),
        )
        for request_id in request_ids
    }


async def get_reissue(session: AsyncSession, staff: Actor, request_id: str) -> Reissue:
    _staff_only(staff)
    return await _request(session, request_id)


# The worker's side ------------------------------------------------------------------------------


async def queue_page(
    session: AsyncSession, request_id: str, after: str, now: datetime
) -> tuple[list[str], str | None]:
    """Record a copy of each note in the next page and return their IDs, with the last note's
    ID when another page may follow. A note already copied for this request is skipped, so a
    page run twice queues nothing more."""
    request = await _request(session, request_id)
    if request.status != "approved":
        return [], None
    start, end = _bounds(request.issued_from, request.issued_to)
    rows = (
        await session.execute(
            _PAGE,
            {
                "client_id": request.client_id,
                "start": start,
                "end": end,
                "after": after,
                "limit": PAGE,
            },
        )
    ).all()
    copies: list[str] = []
    for row in rows:
        saved = await session.execute(
            _INSERT_COPY,
            {
                "id": push_id(),
                "reissue_id": request.id,
                "document_id": row.id,
                "client_id": row.client_id,
                "now": now,
            },
        )
        copy_id = saved.scalar()
        if copy_id is not None:
            copies.append(copy_id)
    return copies, (rows[-1].id if len(rows) == PAGE else None)


async def finish(session: AsyncSession, request_id: str, clock: Clock) -> int:
    """Mark an approved request done, with how many notes it sent again."""
    sent = int((await session.execute(_COPIES, {"id": request_id})).scalar_one())
    finished = await session.execute(_DONE, {"id": request_id, "sent": sent, "now": clock.now()})
    if getattr(finished, "rowcount", 0):
        done = await _request(session, request_id)
        await _record(session, AuditActor.system(), "change", "done", done, clock)
    return sent


# Internals --------------------------------------------------------------------------------------


def _check_days(issued_from: date, issued_to: date, today: date) -> None:
    if issued_to > today:
        raise _invalid("issued_to", "not_yet")
    if issued_from > issued_to:
        raise _invalid("issued_from", "after_issued_to")
    if (issued_to - issued_from).days >= MAX_DAYS:
        raise _invalid("issued_to", "too_long")
    if (today - issued_from).days > MAX_AGE_DAYS:
        raise _invalid("issued_from", "too_old")


def _bounds(issued_from: date, issued_to: date) -> tuple[datetime, datetime]:
    """The Kampala days as times, the end not included."""
    start = datetime.combine(issued_from, time(0), KAMPALA)
    return start, datetime.combine(issued_to + timedelta(days=1), time(0), KAMPALA)


async def _count(
    session: AsyncSession, client_id: str | None, issued_from: date, issued_to: date
) -> int:
    start, end = _bounds(issued_from, issued_to)
    found = await session.execute(_COUNT, {"client_id": client_id, "start": start, "end": end})
    return int(found.scalar_one())


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


async def _request(session: AsyncSession, request_id: str) -> Reissue:
    row = (await session.execute(_BY_ID, {"id": request_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _for_decision(session: AsyncSession, staff: Actor, request_id: str) -> Reissue:
    _staff_only(staff)
    row = (await session.execute(_BY_ID_LOCKED, {"id": request_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    request = _from_row(row)
    if request.status != "proposed":
        raise ApplicationError("conflict")
    return request


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _from_row(row: Row[Any]) -> Reissue:
    return Reissue(
        id=row.id,
        client_id=row.client_id,
        issued_from=row.issued_from,
        issued_to=row.issued_to,
        reason=row.reason,
        status=row.status,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        job_id=row.job_id,
        sent_count=row.sent_count,
        finished_at=row.finished_at,
    )


async def _audit(
    session: AsyncSession,
    staff: Actor,
    category: AuditCategory,
    step: str,
    request: Reissue,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    assert staff.id is not None
    await _record(
        session, AuditActor.staff(staff.id), category, step, request, clock, reason=reason
    )


async def _record(
    session: AsyncSession,
    actor: AuditActor,
    category: AuditCategory,
    step: str,
    request: Reissue,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"documents.note_reissue.{step}",
            resource_type=RESOURCE,
            resource_id=request.id,
            actor=actor,
            reason=reason,
            details={
                "client_id": request.client_id,
                "issued_from": request.issued_from.isoformat(),
                "issued_to": request.issued_to.isoformat(),
                "job_id": request.job_id,
                "sent_count": request.sent_count,
            },
        ),
        clock,
    )
