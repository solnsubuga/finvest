"""Staff asking for the interest run or the maturity run again, each with a second person.

When a day's interest run failed part way, or this morning's maturity run left terms open, one
member of staff asks for that run again with a reason. A different one approves it, which
queues the run in the same transaction, or declines it with a note. Only the proposer
withdraws it. The database holds them to that (migration 0330).

Nothing here moves money itself. The runs do, exactly as on their schedule, and both are safe
to repeat: interest posts only what is still owed for the day, and maturity closes only the
terms still open. See docs/investing.md, "Running a day again".
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments.catalogue import invalid
from app.modules.investments.domain.runs import RunKind, run_date_problem
from app.shared.actors import Actor
from app.shared.business_day import KAMPALA, business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "investment_run_request"
MAX_REASON_LENGTH = 500
MAX_REQUESTS = 200
# The job kinds the scheduler queues. Named here, not imported, so the jobs module can import
# this one without a cycle; a unit test checks they match.
JOB_KINDS = {
    RunKind.INTEREST: "investments.accrue_interest",
    RunKind.MATURITY: "investments.mature",
}
logger = structlog.get_logger()

RunStatus = Literal["proposed", "approved", "declined", "withdrawn"]


@dataclass(frozen=True, slots=True)
class RunRequest:
    id: str
    kind: RunKind
    run_date: date
    reason: str
    status: RunStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    job_id: str | None


_COLUMNS = (
    "id, kind, run_date, reason, status, proposed_by, proposed_at, decided_by, decided_at, "
    "decision_note, job_id"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM investment_run_requests WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(
    f"SELECT {_COLUMNS} FROM investment_run_requests WHERE id = :id FOR UPDATE"  # noqa: S608
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM investment_run_requests "  # noqa: S608
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO investment_run_requests (id, kind, run_date, reason, proposed_by, proposed_at) "
    "VALUES (:id, :kind, :run_date, :reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE investment_run_requests SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note, job_id = :job_id WHERE id = :id"
)


async def propose_run(
    session: AsyncSession,
    staff: Actor,
    *,
    kind: RunKind,
    run_date: date,
    reason: str,
    clock: Clock,
) -> RunRequest:
    """Ask for a run again, for a second member of staff to approve. Nothing runs yet.

    422 on `run_date` with `not_yet`, `too_old` or `maturity_today_only`. `conflict` if a
    request for the same run and day is already waiting.
    """
    _staff_only(staff)
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise invalid("reason", "required")
    problem = run_date_problem(kind, run_date, business_date(clock.now()))
    if problem is not None:
        raise invalid("run_date", problem)
    request_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": request_id,
                    "kind": kind.value,
                    "run_date": run_date,
                    "reason": cleaned,
                    "proposed_by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        raise ApplicationError("conflict") from error
    request = await _request(session, request_id)
    await _audit(session, staff, "change", "proposed", request, clock, reason=cleaned)
    return request


async def approve_run(
    session: AsyncSession, staff: Actor, request_id: str, clock: Clock
) -> RunRequest:
    """Approve as the second person, and queue the run in this transaction.

    The day is checked again: a maturity run asked for yesterday is refused today as
    `conflict` with `run_date_passed`, because today's own run already covers it.
    """
    request = await _for_decision(session, staff, request_id)
    if request.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    now = clock.now()
    if run_date_problem(request.kind, request.run_date, business_date(now)) is not None:
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["run_date"], code="run_date_passed")]
        )
    job_id = await enqueue(
        session,
        JobRequest(
            kind=JOB_KINDS[request.kind],
            # Its own key, so it never stands in for or is stood in for by a scheduled run.
            idempotency_key=f"staff-run:{request.id}",
            # Noon in Kampala on the day, which the run reads back as the business day.
            payload={
                "scheduled_for": datetime.combine(request.run_date, time(12), KAMPALA).isoformat(),
                "run_request_id": request.id,
            },
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
    logger.info(
        "investment_run_requested",
        run_request_id=request.id,
        kind=request.kind.value,
        job_id=job_id,
    )
    return approved


async def decline_run(
    session: AsyncSession, staff: Actor, request_id: str, note: str, clock: Clock
) -> RunRequest:
    """Decline as the second person, with a note for the proposer. Nothing runs."""
    request = await _for_decision(session, staff, request_id)
    if request.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    cleaned = note.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise invalid("note", "required")
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


async def withdraw_run(
    session: AsyncSession, staff: Actor, request_id: str, clock: Clock
) -> RunRequest:
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


async def list_runs(
    session: AsyncSession, staff: Actor, *, status: RunStatus | None = None, limit: int = 50
) -> list[RunRequest]:
    """Run requests, newest first. For the console and the approvals inbox."""
    _staff_only(staff)
    rows = await session.execute(
        _LIST, {"status": status, "limit": max(1, min(limit, MAX_REQUESTS))}
    )
    return [_from_row(row) for row in rows.all()]


async def get_run(session: AsyncSession, staff: Actor, request_id: str) -> RunRequest:
    _staff_only(staff)
    return await _request(session, request_id)


# Internals --------------------------------------------------------------------------------------


async def _request(session: AsyncSession, request_id: str) -> RunRequest:
    row = (await session.execute(_BY_ID, {"id": request_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _for_decision(session: AsyncSession, staff: Actor, request_id: str) -> RunRequest:
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


def _from_row(row: Row[Any]) -> RunRequest:
    return RunRequest(
        id=row.id,
        kind=RunKind(row.kind),
        run_date=row.run_date,
        reason=row.reason,
        status=row.status,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        job_id=row.job_id,
    )


async def _audit(
    session: AsyncSession,
    staff: Actor,
    category: AuditCategory,
    step: str,
    request: RunRequest,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    assert staff.id is not None
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"investments.run_request.{step}",
            resource_type=RESOURCE,
            resource_id=request.id,
            actor=AuditActor.staff(staff.id),
            reason=reason,
            details={
                "kind": request.kind.value,
                "run_date": request.run_date.isoformat(),
                "job_id": request.job_id,
            },
        ),
        clock,
    )
