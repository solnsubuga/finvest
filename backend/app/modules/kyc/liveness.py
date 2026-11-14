"""Liveness attempts: commands and queries. Every function works inside the caller's transaction.

The client starts an attempt from the API. The worker asks the provider for a session, saves
its page sealed, and reads the answer until there is one (see liveness_jobs.py). When a live
person was seen, the worker adds the captured face as the check's selfie, retiring the one
before it, so the face match at hand-in compares the live capture with the document.

No provider is called from here. See docs/identity-checks.md#liveness.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.infrastructure.sealing import Sealer, associated_data
from app.infrastructure.storage import StoredFile
from app.modules.kyc import service
from app.modules.kyc.domain.checks import CheckStatus, DocumentType, PhotoKind
from app.modules.kyc.domain.liveness import (
    MAX_ATTEMPTS,
    OPEN_FOR,
    WAITING,
    LivenessStatus,
    selfie_editable,
    shown_status,
    start_problem,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id

START_LIVENESS = "kyc.start_liveness"
READ_LIVENESS = "kyc.read_liveness"
# How often the worker asks the provider about an open attempt. The client is waiting on it.
POLL_EVERY = timedelta(seconds=10)
RESOURCE = "kyc_liveness"

_COLUMNS = (
    "id, check_id, status, provider, provider_request_id, url_sealed, failure_code, photo_id, "
    "created_at, finished_at"
)
_ATTEMPT = text(f"SELECT {_COLUMNS} FROM kyc_liveness WHERE id = :id")  # noqa: S608
_ATTEMPT_LOCKED = text(f"SELECT {_COLUMNS} FROM kyc_liveness WHERE id = :id FOR UPDATE")  # noqa: S608
_LATEST = text(
    f"SELECT {_COLUMNS} FROM kyc_liveness WHERE check_id = :check_id "  # noqa: S608
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)
_COUNT = text("SELECT count(*) FROM kyc_liveness WHERE check_id = :check_id")
_INSERT = text(
    "INSERT INTO kyc_liveness (id, check_id, status, provider, created_at, updated_at) "
    "VALUES (:id, :check_id, 'starting', :provider, :now, :now)"
)
_RETIRE_WAITING = text(
    "UPDATE kyc_liveness SET status = 'expired', finished_at = :now, updated_at = :now "
    "WHERE check_id = :check_id AND status IN ('starting', 'open')"
)
_OPEN = text(
    "UPDATE kyc_liveness SET status = 'open', provider_request_id = :request_id, "
    "url_sealed = :url_sealed, updated_at = :now WHERE id = :id"
)
_FINISH = text(
    "UPDATE kyc_liveness SET status = :status, failure_code = :failure_code, "
    "photo_id = :photo_id, finished_at = :now, updated_at = :now WHERE id = :id"
)
_INSERT_LIVE_PHOTO = text(
    "INSERT INTO kyc_photos (id, check_id, kind, storage_key, content_type, byte_size, sha256, "
    "source, created_at) VALUES (:id, :check_id, 'selfie', :storage_key, :content_type, "
    ":byte_size, :sha256, 'liveness', :now)"
)
_RETIRE_SELFIE = text(
    "UPDATE kyc_photos SET replaced_at = :now "
    "WHERE check_id = :check_id AND kind = 'selfie' AND replaced_at IS NULL"
)


@dataclass(frozen=True, slots=True)
class LivenessView:
    """An attempt as its client sees it. `url` is given only while the attempt is open."""

    id: str
    check_id: str
    status: LivenessStatus
    url: str | None
    failure_code: str | None
    # True when the capture became the check's selfie.
    photo_used: bool
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether the client can start an attempt now, and the latest one."""

    available: bool
    # Why not, as a stable code, when the provider is on but the client may not start now.
    problem: str | None
    attempts_left: int
    latest: LivenessView | None


# Client steps ---------------------------------------------------------------------------------


async def start(
    session: AsyncSession, client: Actor, provider_name: str, clock: Clock
) -> LivenessView:
    """Start an attempt. An attempt still waiting is retired, so only the newest one counts.

    Saved with the job that asks the provider for a session, so it is never started and lost.
    """
    row = await service._editable(session, client)
    attempts = await _count(session, row.id)
    problem = start_problem(
        CheckStatus(row.status),
        DocumentType(row.document_type),
        _retakes(row),
        attempts=attempts,
    )
    if problem == "too_many_attempts":
        raise service._invalid("liveness", problem)
    if problem is not None:
        raise ApplicationError("conflict")
    now = clock.now()
    await session.execute(_RETIRE_WAITING, {"check_id": row.id, "now": now})
    attempt_id = push_id()
    await session.execute(
        _INSERT,
        {"id": attempt_id, "check_id": row.id, "provider": provider_name, "now": now},
    )
    await enqueue(
        session,
        JobRequest(
            kind=START_LIVENESS,
            idempotency_key=attempt_id,
            payload={"attempt_id": attempt_id},
            max_age=OPEN_FOR,
        ),
        now=now,
    )
    await service._audit(
        session, client, "change", "kyc.liveness.started", attempt_id, clock, resource=RESOURCE
    )
    view = await _view_by_id(session, None, attempt_id, now)
    return view


async def availability(
    session: AsyncSession,
    sealer: Sealer,
    client: Actor,
    clock: Clock,
    *,
    provider_on: bool,
) -> Availability:
    """What the client's selfie step shows. With no provider, a camera selfie is the only way."""
    client_id = service._person(client, "client")
    row = (await session.execute(service._BY_CLIENT, {"client_id": client_id})).one_or_none()
    if row is None:
        return Availability(provider_on, "no_check" if provider_on else None, MAX_ATTEMPTS, None)
    now = clock.now()
    attempts = await _count(session, row.id)
    latest_row = (await session.execute(_LATEST, {"check_id": row.id})).one_or_none()
    latest = None if latest_row is None else _view(latest_row, sealer, now)
    problem = None
    if provider_on:
        problem = start_problem(
            CheckStatus(row.status),
            DocumentType(row.document_type),
            _retakes(row),
            attempts=attempts,
        )
    return Availability(
        available=provider_on,
        problem=problem,
        attempts_left=max(0, MAX_ATTEMPTS - attempts),
        latest=latest,
    )


# The worker, around the provider -------------------------------------------------------------


async def to_start(session: AsyncSession, attempt_id: str, clock: Clock) -> bool:
    """Whether this attempt still waits for a session. An expired one is retired here."""
    row = (await session.execute(_ATTEMPT_LOCKED, {"id": attempt_id})).one_or_none()
    if row is None or row.status != LivenessStatus.STARTING:
        return False
    return not await _expire_if_old(session, row, clock)


async def note_open(
    session: AsyncSession,
    sealer: Sealer,
    attempt_id: str,
    request_id: str,
    url: str,
    clock: Clock,
) -> bool:
    """Save the provider's session and start reading it. False when the attempt moved on."""
    row = (await session.execute(_ATTEMPT_LOCKED, {"id": attempt_id})).one_or_none()
    if row is None or row.status != LivenessStatus.STARTING:
        return False
    if await _expire_if_old(session, row, clock):
        return False
    now = clock.now()
    await session.execute(
        _OPEN,
        {
            "id": attempt_id,
            "request_id": request_id,
            "url_sealed": sealer.seal(url, associated_data("kyc_liveness", "url", attempt_id)),
            "now": now,
        },
    )
    await enqueue_read(session, attempt_id, 1, now, delay=POLL_EVERY)
    return True


async def enqueue_read(
    session: AsyncSession,
    attempt_id: str,
    read: int,
    now: datetime,
    *,
    delay: timedelta,
) -> None:
    """Queue the next read of an open attempt. Each read is its own job, `delay` apart."""
    await enqueue(
        session,
        JobRequest(
            kind=READ_LIVENESS,
            idempotency_key=f"{attempt_id}:read:{read}",
            payload={"attempt_id": attempt_id, "read": read},
            # Long enough to wait out the provider's backoff, never past the attempt's life.
            max_age=OPEN_FOR + delay,
            delay=delay,
        ),
        now=now,
    )


async def to_read(session: AsyncSession, attempt_id: str, clock: Clock) -> str | None:
    """The provider's request ID of an attempt still open, or None. An old one is retired."""
    row = (await session.execute(_ATTEMPT_LOCKED, {"id": attempt_id})).one_or_none()
    if row is None or row.status != LivenessStatus.OPEN:
        return None
    if await _expire_if_old(session, row, clock):
        return None
    request_id: str = row.provider_request_id
    return request_id


async def check_of(session: AsyncSession, attempt_id: str) -> str:
    """The check an attempt belongs to."""
    check_id: str = (await session.execute(_ATTEMPT, {"id": attempt_id})).one().check_id
    return check_id


async def finish(
    session: AsyncSession,
    attempt_id: str,
    status: LivenessStatus,
    code: str | None,
    clock: Clock,
) -> bool:
    """Record an attempt that did not see a live person, or never started. False if moved on."""
    if status not in (LivenessStatus.FAILED, LivenessStatus.NOT_STARTED):
        raise ValueError("A passed attempt is recorded with record_live")
    row = (await session.execute(_ATTEMPT_LOCKED, {"id": attempt_id})).one_or_none()
    if row is None or LivenessStatus(row.status) not in WAITING:
        return False
    await _finish(session, attempt_id, status, code, None, clock.now())
    await _audit_finish(session, attempt_id, status, code, clock)
    return True


async def record_live(
    session: AsyncSession,
    attempt_id: str,
    photo_id: str,
    stored: StoredFile,
    clock: Clock,
) -> bool:
    """Record a pass, and make the stored capture the check's selfie if the selfie may change.

    True when the capture became the selfie. False when it did not, for example because the
    client handed the check in meanwhile: the caller then deletes the stored file.
    """
    attempt = (await session.execute(_ATTEMPT, {"id": attempt_id})).one_or_none()
    if attempt is None:
        return False
    # The check first, then the attempt: the order the client's start takes them in.
    check = (await session.execute(service._BY_ID_LOCKED, {"id": attempt.check_id})).one()
    attempt = (await session.execute(_ATTEMPT_LOCKED, {"id": attempt_id})).one()
    if LivenessStatus(attempt.status) not in WAITING:
        return False
    now = clock.now()
    usable = (
        selfie_editable(CheckStatus(check.status), _retakes(check))
        and stored.key == service.photo_key(check.id, photo_id)
        and await _room_for_photo(session, check.id)
    )
    if usable:
        await session.execute(_RETIRE_SELFIE, {"check_id": check.id, "now": now})
        await session.execute(
            _INSERT_LIVE_PHOTO,
            {
                "id": photo_id,
                "check_id": check.id,
                "storage_key": stored.key,
                "content_type": stored.content_type,
                "byte_size": stored.byte_size,
                "sha256": stored.sha256,
                "now": now,
            },
        )
    await _finish(
        session,
        attempt_id,
        LivenessStatus.PASSED,
        None if usable else "check_moved_on",
        photo_id if usable else None,
        now,
    )
    await _audit_finish(session, attempt_id, LivenessStatus.PASSED, None, clock)
    return usable


# Internals ------------------------------------------------------------------------------------


def _retakes(row: Row[Any]) -> frozenset[PhotoKind]:
    return frozenset(PhotoKind(kind) for kind in row.retake_photos)


async def _count(session: AsyncSession, check_id: str) -> int:
    count: int = (await session.execute(_COUNT, {"check_id": check_id})).scalar_one()
    return count


async def _room_for_photo(session: AsyncSession, check_id: str) -> bool:
    taken = (await session.execute(service._PHOTO_COUNT, {"check_id": check_id})).scalar_one()
    return bool(taken < service.MAX_PHOTOS_PER_CHECK)


async def _expire_if_old(session: AsyncSession, row: Row[Any], clock: Clock) -> bool:
    now = clock.now()
    if shown_status(LivenessStatus(row.status), row.created_at, now) is not LivenessStatus.EXPIRED:
        return False
    await _finish(session, row.id, LivenessStatus.EXPIRED, None, None, now)
    return True


async def _finish(
    session: AsyncSession,
    attempt_id: str,
    status: LivenessStatus,
    code: str | None,
    photo_id: str | None,
    now: datetime,
) -> None:
    await session.execute(
        _FINISH,
        {
            "id": attempt_id,
            "status": status.value,
            "failure_code": code,
            "photo_id": photo_id,
            "now": now,
        },
    )


async def _audit_finish(
    session: AsyncSession,
    attempt_id: str,
    status: LivenessStatus,
    code: str | None,
    clock: Clock,
) -> None:
    await service._audit(
        session,
        Actor("system"),
        "change",
        f"kyc.liveness.{status.value}",
        attempt_id,
        clock,
        details={"code": code} if code else None,
        resource=RESOURCE,
    )


async def _view_by_id(
    session: AsyncSession, sealer: Sealer | None, attempt_id: str, now: datetime
) -> LivenessView:
    row = (await session.execute(_ATTEMPT, {"id": attempt_id})).one()
    return _view(row, sealer, now)


def _view(row: Row[Any], sealer: Sealer | None, now: datetime) -> LivenessView:
    status = shown_status(LivenessStatus(row.status), row.created_at, now)
    url = None
    if status is LivenessStatus.OPEN and sealer is not None:
        url = sealer.open(row.url_sealed, associated_data("kyc_liveness", "url", row.id))
    return LivenessView(
        id=row.id,
        check_id=row.check_id,
        status=status,
        url=url,
        failure_code=row.failure_code,
        photo_used=row.photo_id is not None,
        created_at=row.created_at,
        expires_at=row.created_at + OPEN_FOR,
    )
