"""Durable job rows: enqueueing, lease claims and fenced state transitions.

Enqueueing runs inside the caller's business transaction, so a change and its follow-up work are
saved together or not at all. Claims and every later transition are short transactions of their
own, and each transition checks the fencing token issued by the claim, so a worker whose lease
expired can never finish, retry or fail a job it lost.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs.errors import UNCERTAIN_OUTCOME, StaleLeaseError
from app.shared.push_ids import push_id

JsonObject = dict[str, object]
JobStatus = Literal["pending", "running", "succeeded", "failed"]

MAX_ERROR_MESSAGE = 500


def to_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


def from_json(value: object) -> JsonObject:
    loaded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(loaded, dict):
        raise TypeError("Stored JSON must be an object")
    return loaded


@dataclass(frozen=True)
class JobRequest:
    """Work to do later. The payload carries identifiers and codes, never personal data."""

    kind: str
    idempotency_key: str
    payload: Mapping[str, object]
    queue: str = "default"
    max_attempts: int = 8
    max_age: timedelta = timedelta(days=1)
    delay: timedelta = timedelta(0)


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    event_id: str | None
    kind: str
    idempotency_key: str
    payload: JsonObject
    attempt: int
    max_attempts: int
    fencing_token: int
    lease_expires_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class JobRecord:
    id: str
    kind: str
    queue: str
    status: JobStatus
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error_code: str | None
    last_error_message: str | None
    finished_at: datetime | None


async def enqueue(
    session: AsyncSession,
    request: JobRequest,
    *,
    now: datetime,
    event_id: str | None = None,
) -> str | None:
    """Insert a job in the caller's transaction. Returns None if an equal job is active."""
    if not session.in_transaction():
        raise RuntimeError("Jobs are enqueued inside the business transaction")
    if request.max_age <= request.delay:
        raise ValueError("A job must be able to run before it expires")
    job_id = push_id()
    # No RETURNING: the API role may add jobs but has no right to read them.
    result: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
        text(
            "INSERT INTO jobs (id, event_id, queue, kind, idempotency_key, payload, "
            "max_attempts, run_at, expires_at, created_at, updated_at) "
            "VALUES (:id, :event_id, :queue, :kind, :key, CAST(:payload AS jsonb), "
            ":max_attempts, :run_at, :expires_at, :now, :now) "
            "ON CONFLICT (kind, idempotency_key) WHERE status IN ('pending', 'running') "
            "DO NOTHING"
        ),
        {
            "id": job_id,
            "event_id": event_id,
            "queue": request.queue,
            "kind": request.kind,
            "key": request.idempotency_key,
            "payload": to_json(request.payload),
            "max_attempts": request.max_attempts,
            "run_at": now + request.delay,
            "expires_at": now + request.max_age,
            "now": now,
        },
    )
    return job_id if result.rowcount == 1 else None


async def claim(
    database: Database,
    *,
    queue: str,
    kinds: Sequence[str],
    worker: str,
    now: datetime,
    lease: timedelta,
    limit: int,
    external_kinds: Sequence[str] = (),
) -> list[ClaimedJob]:
    """Lease due jobs and commit the lease before any handler runs.

    A job of an `external_kinds` kind whose lease expired is not leased again. Its attempt
    started and nobody recorded how it ended, so it fails as `uncertain_outcome`.
    """
    async with database.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT * FROM finvest_claim_jobs(:queue, CAST(:kinds AS text[]), "
                "CAST(:external_kinds AS text[]), :worker, :now, :lease, :limit)"
            ),
            {
                "queue": queue,
                "kinds": list(kinds),
                "external_kinds": list(external_kinds),
                "worker": worker,
                "now": now,
                "lease": lease,
                "limit": limit,
            },
        )
        return [
            ClaimedJob(
                id=row.claimed_id,
                event_id=row.claimed_event_id,
                kind=row.claimed_kind,
                idempotency_key=row.claimed_idempotency_key,
                payload=from_json(row.claimed_payload),
                attempt=row.claimed_attempt,
                max_attempts=row.claimed_max_attempts,
                fencing_token=row.claimed_fencing_token,
                lease_expires_at=row.claimed_lease_expires_at,
                expires_at=row.claimed_expires_at,
            )
            for row in rows
        ]


async def lock_fenced(session: AsyncSession, job: ClaimedJob) -> None:
    """Row-lock the job if this claim still owns it, otherwise raise `StaleLeaseError`."""
    owned = await session.scalar(
        text(
            "SELECT id FROM jobs WHERE id = :id AND fencing_token = :token "
            "AND status = 'running' FOR UPDATE"
        ),
        {"id": job.id, "token": job.fencing_token},
    )
    if owned is None:
        raise StaleLeaseError(job.id)


async def extend_lease(
    session: AsyncSession, job: ClaimedJob, *, now: datetime, lease: timedelta
) -> datetime:
    expires: datetime | None = await session.scalar(
        text(
            "UPDATE jobs SET lease_expires_at = :expires, updated_at = :now "
            "WHERE id = :id AND fencing_token = :token AND status = 'running' "
            "AND lease_expires_at > :now RETURNING lease_expires_at"
        ),
        {"id": job.id, "token": job.fencing_token, "now": now, "expires": now + lease},
    )
    if expires is None:
        raise StaleLeaseError(job.id)
    return expires


async def finish(
    session: AsyncSession,
    job: ClaimedJob,
    *,
    now: datetime,
    outcome: Literal["succeeded", "failed", "retry"],
    run_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Record the attempt and move the job on, only if this claim still owns it."""
    message = error_message[:MAX_ERROR_MESSAGE] if error_message else None
    status: JobStatus = "pending" if outcome == "retry" else outcome
    updated = await session.scalar(
        text(
            "UPDATE jobs SET status = CAST(:status AS text), "
            "run_at = coalesce(:run_at, run_at), "
            "lease_owner = NULL, lease_expires_at = NULL, updated_at = :now, "
            "finished_at = CASE WHEN CAST(:status AS text) = 'pending' THEN NULL "
            "ELSE CAST(:now AS timestamptz) END, "
            "last_error_code = coalesce(CAST(:code AS text), last_error_code), "
            "last_error_message = CASE WHEN CAST(:code AS text) IS NULL "
            "THEN last_error_message ELSE CAST(:message AS text) END "
            "WHERE id = :id AND fencing_token = :token AND status = 'running' RETURNING id"
        ),
        {
            "status": status,
            "run_at": run_at,
            "now": now,
            "code": error_code,
            "message": message,
            "id": job.id,
            "token": job.fencing_token,
        },
    )
    if updated is None:
        raise StaleLeaseError(job.id)
    await session.execute(
        text(
            "UPDATE job_attempts SET outcome = :outcome, finished_at = :now, "
            "error_code = :code, error_message = :message "
            "WHERE job_id = :id AND attempt = :attempt AND fencing_token = :token"
        ),
        {
            "outcome": outcome,
            "now": now,
            "code": error_code,
            "message": message,
            "id": job.id,
            "attempt": job.attempt,
            "token": job.fencing_token,
        },
    )


async def read_receipt(session: AsyncSession, *, consumer: str, key: str) -> JsonObject | None:
    result = await session.scalar(
        text(
            "SELECT result FROM consumer_receipts "
            "WHERE consumer = :consumer AND idempotency_key = :key"
        ),
        {"consumer": consumer, "key": key},
    )
    return None if result is None else from_json(result)


async def write_receipt(
    session: AsyncSession, job: ClaimedJob, result: Mapping[str, object], *, now: datetime
) -> None:
    await session.execute(
        text(
            "INSERT INTO consumer_receipts (consumer, idempotency_key, job_id, result, created_at) "
            "VALUES (:consumer, :key, :job_id, CAST(:result AS jsonb), :now)"
        ),
        {
            "consumer": job.kind,
            "key": job.idempotency_key,
            "job_id": job.id,
            "result": to_json(result),
            "now": now,
        },
    )


_RECORD_COLUMNS = (
    "id, kind, queue, status, attempts, max_attempts, run_at, "
    "last_error_code, last_error_message, finished_at"
)


async def is_active(session: AsyncSession, *, kind: str, idempotency_key: str) -> bool:
    """Whether a job of this kind and key is waiting or running. The API may ask: it reads only
    the kind, the key and the status, the columns it is granted."""
    found = await session.scalar(
        text(
            "SELECT 1 FROM jobs WHERE kind = :kind AND idempotency_key = :key "
            "AND status IN ('pending', 'running')"
        ),
        {"kind": kind, "key": idempotency_key},
    )
    return found is not None


async def failed_jobs(session: AsyncSession, *, limit: int = 50) -> list[JobRecord]:
    """The failed-job queue, newest first."""
    rows = await session.execute(
        text(
            f"SELECT {_RECORD_COLUMNS} FROM jobs WHERE status = 'failed' "  # noqa: S608
            "ORDER BY finished_at DESC, id LIMIT :limit"
        ),
        {"limit": max(1, min(limit, 500))},
    )
    return [JobRecord(**row._mapping) for row in rows]


async def get_job(session: AsyncSession, job_id: str) -> JobRecord | None:
    row = await session.execute(
        text(f"SELECT {_RECORD_COLUMNS} FROM jobs WHERE id = :id"),  # noqa: S608
        {"id": job_id},
    )
    found = row.one_or_none()
    return None if found is None else JobRecord(**found._mapping)


async def requeue_failed(
    session: AsyncSession,
    job_id: str,
    *,
    now: datetime,
    extra_attempts: int = 3,
    max_age: timedelta = timedelta(days=1),
) -> bool:
    """Operator recovery: give a failed job more attempts and a fresh delivery window.

    Its idempotency key and consumer receipt are unchanged, so a result that was in fact applied
    is replayed, not applied again. Returns False if the job is not failed, has no attempt budget
    left, has an active twin, or failed with an uncertain outcome. An uncertain job is never
    requeued: reconciliation settles it from the provider's record.
    """
    if extra_attempts < 1:
        raise ValueError("Requeueing needs at least one more attempt")
    active = await session.scalar(
        text(
            "SELECT 1 FROM jobs failed JOIN jobs active "
            "ON active.kind = failed.kind AND active.idempotency_key = failed.idempotency_key "
            "AND active.status IN ('pending', 'running') WHERE failed.id = :id"
        ),
        {"id": job_id},
    )
    if active is not None:
        return False
    updated = await session.scalar(
        text(
            "UPDATE jobs SET status = 'pending', max_attempts = attempts + :extra, "
            "run_at = :now, expires_at = :expires, finished_at = NULL, updated_at = :now "
            "WHERE id = :id AND status = 'failed' AND attempts + :extra <= 100 "
            "AND last_error_code IS DISTINCT FROM :uncertain RETURNING id"
        ),
        {
            "id": job_id,
            "now": now,
            "expires": now + max_age,
            "extra": extra_attempts,
            "uncertain": UNCERTAIN_OUTCOME,
        },
    )
    return updated is not None
