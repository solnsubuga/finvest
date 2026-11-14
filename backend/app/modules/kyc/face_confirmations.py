"""Face confirmations: commands and queries. Every function works inside the caller's transaction.

A signed-in client with an approved check starts an attempt. The worker asks the liveness
provider for a page, saves it sealed, and reads it until it answers (see
face_confirmation_jobs.py). When a live person was seen, the worker stores the captured face and
asks the face match provider to compare it with the selfie on the approved check. A match passes
the attempt, and a passed attempt confirms a sign-in change on the same session for
`COUNTS_FOR`.

The identity module calls `needed` and `confirmed` before a password or number change. No
provider is called from here. See docs/identity-checks.md#face-confirmations.
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
from app.modules.kyc.domain.face_confirmations import (
    COUNTS_FOR,
    DAY,
    MAX_PER_DAY,
    OPEN_FOR,
    WAITING,
    ConfirmationStatus,
    counts,
    match_passes,
    shown_status,
    start_problem,
)
from app.modules.kyc.domain.provider import PhotoRef
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id

START = "kyc.start_face_confirmation"
READ = "kyc.read_face_confirmation"
MATCH = "kyc.match_face_confirmation"
READ_MATCH = "kyc.read_face_match"
# How often the worker asks a provider about an attempt. The client is waiting on it.
POLL_EVERY = timedelta(seconds=5)
RESOURCE = "kyc_face_confirmation"

_COLUMNS = (
    "id, client_id, check_id, reference_photo_id, reference_claim_photo_id, session_id, "
    "status, provider, matcher, "
    "provider_request_id, url_sealed, capture_key, capture_sha256, capture_content_type, "
    "match_request_id, score, failure_code, created_at, finished_at"
)
_ATTEMPT = text(f"SELECT {_COLUMNS} FROM kyc_face_confirmations WHERE id = :id")  # noqa: S608
_ATTEMPT_LOCKED = text(
    f"SELECT {_COLUMNS} FROM kyc_face_confirmations WHERE id = :id FOR UPDATE"  # noqa: S608
)
_OWN = text(
    f"SELECT {_COLUMNS} FROM kyc_face_confirmations "  # noqa: S608
    "WHERE id = :id AND client_id = :client_id"
)
_LATEST = text(
    f"SELECT {_COLUMNS} FROM kyc_face_confirmations "  # noqa: S608
    "WHERE client_id = :client_id AND session_id = :session_id "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)
_STARTED_SINCE = text(
    "SELECT count(*) FROM kyc_face_confirmations "
    "WHERE client_id = :client_id AND created_at > :since"
)
_PASSED = text(
    "SELECT status, finished_at FROM kyc_face_confirmations "
    "WHERE client_id = :client_id AND session_id = :session_id AND status = 'passed' "
    "AND finished_at > :since ORDER BY finished_at DESC LIMIT 1"
)
_PHOTO = text("SELECT storage_key, sha256, content_type FROM kyc_photos WHERE id = :id")
_CLAIM_PHOTO = text("SELECT storage_key, sha256, content_type FROM kyc_claim_photos WHERE id = :id")
_INSERT = text(
    "INSERT INTO kyc_face_confirmations (id, client_id, check_id, reference_photo_id, "
    "reference_claim_photo_id, session_id, status, provider, matcher, created_at, updated_at) "
    "VALUES (:id, :client_id, :check_id, :photo_id, :claim_photo_id, :session_id, 'starting', "
    ":provider, :matcher, :now, :now)"
)
_RETIRE_WAITING = text(
    "UPDATE kyc_face_confirmations SET status = 'expired', finished_at = :now, updated_at = :now "
    "WHERE client_id = :client_id AND status IN ('starting', 'open', 'matching')"
)
_OPEN = text(
    "UPDATE kyc_face_confirmations SET status = 'open', provider_request_id = :request_id, "
    "url_sealed = :url_sealed, updated_at = :now WHERE id = :id"
)
_CAPTURED = text(
    "UPDATE kyc_face_confirmations SET status = 'matching', capture_key = :key, "
    "capture_sha256 = :sha256, capture_content_type = :content_type, updated_at = :now "
    "WHERE id = :id"
)
_MATCH_SENT = text(
    "UPDATE kyc_face_confirmations SET match_request_id = :request_id, updated_at = :now "
    "WHERE id = :id"
)
_FINISH = text(
    "UPDATE kyc_face_confirmations SET status = :status, failure_code = :failure_code, "
    "score = coalesce(:score, score), finished_at = :now, updated_at = :now WHERE id = :id"
)


@dataclass(frozen=True, slots=True)
class ConfirmationView:
    """An attempt as its client sees it. `url` is given only while the attempt is open."""

    id: str
    status: ConfirmationStatus
    url: str | None
    failure_code: str | None
    created_at: datetime
    expires_at: datetime
    # Until when a passed attempt confirms a change on this session.
    counts_until: datetime | None


@dataclass(frozen=True, slots=True)
class ConfirmationState:
    """What the app needs before a password or number change on this session."""

    # Whether the change needs a live selfie: liveness is on and a selfie is on file.
    needed: bool
    # Whether a passed attempt confirms a change on this session now.
    confirmed: bool
    # Why a new attempt cannot start now, as a stable code, or None.
    problem: str | None
    attempts_left: int
    latest: ConfirmationView | None


# The identity module's questions --------------------------------------------------------------


async def needed(session: AsyncSession, client_id: str, *, on: bool) -> bool:
    """Whether a password or number change needs a live selfie from this client.

    Only when the provider is on and the client's approved check has a selfie to compare with.
    Otherwise the passkey or app alone confirms the change.
    """
    return on and await _reference(session, client_id) is not None


async def confirmed(session: AsyncSession, client_id: str, session_id: str, clock: Clock) -> bool:
    """Whether a passed attempt on this session confirms a change made now."""
    now = clock.now()
    row = (
        await session.execute(
            _PASSED, {"client_id": client_id, "session_id": session_id, "since": now - COUNTS_FOR}
        )
    ).one_or_none()
    return row is not None and counts(ConfirmationStatus(row.status), row.finished_at, now)


# Client steps ---------------------------------------------------------------------------------


async def start(
    session: AsyncSession,
    client: Actor,
    session_id: str,
    provider_name: str,
    matcher_name: str,
    clock: Clock,
) -> ConfirmationView:
    """Start an attempt. An attempt still waiting is retired, so only the newest one counts.

    Saved with the job that asks the provider for a page, so it is never started and lost.
    """
    client_id = service._person(client, "client")
    now = clock.now()
    # The client's check is locked first, so two starts at once take turns: the second retires
    # the first, and both count towards the day's limit.
    await session.execute(service._BY_CLIENT_LOCKED, {"client_id": client_id})
    reference = await _reference(session, client_id)
    started_today = await _started_since(session, client_id, now - DAY)
    problem = start_problem(has_reference=reference is not None, started_today=started_today)
    if problem is not None:
        raise service._invalid("face_confirmation", problem)
    assert reference is not None  # start_problem refuses without one
    await session.execute(_RETIRE_WAITING, {"client_id": client_id, "now": now})
    attempt_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": attempt_id,
            "client_id": client_id,
            "check_id": reference.check_id,
            "photo_id": reference.photo_id,
            "claim_photo_id": reference.claim_photo_id,
            "session_id": session_id,
            "provider": provider_name,
            "matcher": matcher_name,
            "now": now,
        },
    )
    await enqueue(
        session,
        JobRequest(
            kind=START,
            idempotency_key=attempt_id,
            payload={"attempt_id": attempt_id},
            max_age=OPEN_FOR,
        ),
        now=now,
    )
    await service._audit(
        session,
        client,
        "change",
        "kyc.face_confirmation.started",
        attempt_id,
        clock,
        resource=RESOURCE,
    )
    row = (await session.execute(_ATTEMPT, {"id": attempt_id})).one()
    return _view(row, None, now)


async def state(
    session: AsyncSession,
    sealer: Sealer | None,
    client: Actor,
    session_id: str,
    clock: Clock,
    *,
    on: bool,
) -> ConfirmationState:
    """Whether this session needs a selfie, whether it has one, and the latest attempt.

    Without `sealer` an open attempt is listed with no page.
    """
    client_id = service._person(client, "client")
    now = clock.now()
    reference = await _reference(session, client_id) if on else None
    started_today = await _started_since(session, client_id, now - DAY)
    latest_row = (
        await session.execute(_LATEST, {"client_id": client_id, "session_id": session_id})
    ).one_or_none()
    return ConfirmationState(
        needed=reference is not None,
        confirmed=await confirmed(session, client_id, session_id, clock),
        problem=(
            start_problem(has_reference=True, started_today=started_today)
            if reference is not None
            else ("no_selfie_on_file" if on else None)
        ),
        attempts_left=max(0, MAX_PER_DAY - started_today),
        latest=None if latest_row is None else _view(latest_row, sealer, now),
    )


async def attempt(
    session: AsyncSession, sealer: Sealer, client: Actor, attempt_id: str, clock: Clock
) -> ConfirmationView:
    """One of the client's own attempts. Read it every few seconds while it is waiting."""
    client_id = service._person(client, "client")
    row = (await session.execute(_OWN, {"id": attempt_id, "client_id": client_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row, sealer, clock.now())


# The worker, around the providers ------------------------------------------------------------


async def to_start(session: AsyncSession, attempt_id: str, clock: Clock) -> bool:
    """Whether this attempt still waits for a liveness page. An expired one is retired here."""
    row = await _locked(session, attempt_id, ConfirmationStatus.STARTING)
    return row is not None and not await _expire_if_old(session, row, clock)


async def note_open(
    session: AsyncSession,
    sealer: Sealer,
    attempt_id: str,
    request_id: str,
    url: str,
    clock: Clock,
) -> bool:
    """Save the provider's page and start reading it. False when the attempt moved on."""
    row = await _locked(session, attempt_id, ConfirmationStatus.STARTING)
    if row is None or await _expire_if_old(session, row, clock):
        return False
    now = clock.now()
    await session.execute(
        _OPEN,
        {
            "id": attempt_id,
            "request_id": request_id,
            "url_sealed": sealer.seal(url, _url_data(attempt_id)),
            "now": now,
        },
    )
    await _enqueue_poll(session, READ, attempt_id, 1, now)
    return True


async def to_read(session: AsyncSession, attempt_id: str, clock: Clock) -> str | None:
    """The liveness request ID of an attempt still open, or None. An old one is retired."""
    row = await _locked(session, attempt_id, ConfirmationStatus.OPEN)
    if row is None or await _expire_if_old(session, row, clock):
        return None
    request_id: str = row.provider_request_id
    return request_id


async def read_again(
    session: AsyncSession, kind: str, attempt_id: str, read: int, clock: Clock
) -> None:
    """Queue the next read, `POLL_EVERY` from now."""
    await _enqueue_poll(session, kind, attempt_id, read, clock.now())


def capture_key(check_id: str, attempt_id: str) -> str:
    """Where the face a liveness page captured is kept in private storage."""
    return f"kyc/{check_id}/face-confirmations/{attempt_id}"


async def check_of(session: AsyncSession, attempt_id: str) -> str:
    """The approved check an attempt compares with."""
    check_id: str = (await session.execute(_ATTEMPT, {"id": attempt_id})).one().check_id
    return check_id


async def note_capture(
    session: AsyncSession, attempt_id: str, stored: StoredFile, clock: Clock
) -> bool:
    """Record the live capture and queue the face match. False when the attempt moved on."""
    row = await _locked(session, attempt_id, ConfirmationStatus.OPEN)
    if row is None or await _expire_if_old(session, row, clock):
        return False
    if stored.key != capture_key(row.check_id, attempt_id):
        raise ValueError("A capture is stored under its own attempt's key")
    now = clock.now()
    await session.execute(
        _CAPTURED,
        {
            "id": attempt_id,
            "key": stored.key,
            "sha256": stored.sha256,
            "content_type": stored.content_type,
            "now": now,
        },
    )
    await enqueue(
        session,
        JobRequest(
            kind=MATCH,
            idempotency_key=f"{attempt_id}:match",
            payload={"attempt_id": attempt_id},
            max_age=OPEN_FOR,
        ),
        now=now,
    )
    return True


async def to_match(
    session: AsyncSession, attempt_id: str, clock: Clock
) -> tuple[PhotoRef, PhotoRef] | None:
    """The live capture and the selfie on file, for an attempt not yet sent to the face match."""
    row = await _locked(session, attempt_id, ConfirmationStatus.MATCHING)
    if row is None or row.match_request_id is not None:
        return None
    if await _expire_if_old(session, row, clock):
        return None
    if row.reference_photo_id is not None:
        on_file = (await session.execute(_PHOTO, {"id": row.reference_photo_id})).one()
    else:
        on_file = (await session.execute(_CLAIM_PHOTO, {"id": row.reference_claim_photo_id})).one()
    return (
        PhotoRef(row.capture_key, row.capture_sha256, row.capture_content_type),
        PhotoRef(on_file.storage_key, on_file.sha256, on_file.content_type),
    )


async def note_match_sent(
    session: AsyncSession, attempt_id: str, request_id: str, clock: Clock
) -> bool:
    """Save the face match's request and start reading it. False when the attempt moved on."""
    row = await _locked(session, attempt_id, ConfirmationStatus.MATCHING)
    if row is None or row.match_request_id is not None:
        return False
    now = clock.now()
    await session.execute(_MATCH_SENT, {"id": attempt_id, "request_id": request_id, "now": now})
    await _enqueue_poll(session, READ_MATCH, attempt_id, 1, now)
    return True


async def to_read_match(session: AsyncSession, attempt_id: str, clock: Clock) -> str | None:
    """The face match request ID of an attempt waiting on it, or None. An old one is retired."""
    row = await _locked(session, attempt_id, ConfirmationStatus.MATCHING)
    if row is None or row.match_request_id is None:
        return None
    if await _expire_if_old(session, row, clock):
        return None
    request_id: str = row.match_request_id
    return request_id


async def record_match(
    session: AsyncSession, attempt_id: str, score: int | None, identical: bool, clock: Clock
) -> ConfirmationStatus | None:
    """Record the face match's answer. Returns the attempt's end, or None if it moved on."""
    row = await _locked(session, attempt_id, ConfirmationStatus.MATCHING)
    if row is None or row.match_request_id is None:
        return None
    passed = match_passes(score, identical)
    status = ConfirmationStatus.PASSED if passed else ConfirmationStatus.FAILED
    # A score is kept for a pass. A failure keeps none, so it is never read as a near pass.
    if passed:
        await _finish(session, attempt_id, status, None, score, clock)
    else:
        await _finish(session, attempt_id, status, "face_not_matched", None, clock)
    return status


async def finish(
    session: AsyncSession,
    attempt_id: str,
    status: ConfirmationStatus,
    code: str,
    clock: Clock,
) -> bool:
    """End an attempt that failed or never started. False if it had already moved on."""
    if status not in (ConfirmationStatus.FAILED, ConfirmationStatus.NOT_STARTED):
        raise ValueError("A passed attempt is recorded with record_match")
    row = (await session.execute(_ATTEMPT_LOCKED, {"id": attempt_id})).one_or_none()
    if row is None or ConfirmationStatus(row.status) not in WAITING:
        return False
    await _finish(session, attempt_id, status, code, None, clock)
    return True


# Internals ------------------------------------------------------------------------------------


async def _reference(session: AsyncSession, client_id: str) -> service.ApprovedSelfie | None:
    return await service.approved_selfie(session, client_id)


async def _started_since(session: AsyncSession, client_id: str, since: datetime) -> int:
    count: int = (
        await session.execute(_STARTED_SINCE, {"client_id": client_id, "since": since})
    ).scalar_one()
    return count


async def _locked(
    session: AsyncSession, attempt_id: str, status: ConfirmationStatus
) -> Row[Any] | None:
    row = (await session.execute(_ATTEMPT_LOCKED, {"id": attempt_id})).one_or_none()
    if row is None or row.status != status:
        return None
    return row


async def _enqueue_poll(
    session: AsyncSession, kind: str, attempt_id: str, read: int, now: datetime
) -> None:
    # Each read is its own job, `POLL_EVERY` apart, and never outlives the attempt.
    await enqueue(
        session,
        JobRequest(
            kind=kind,
            idempotency_key=f"{attempt_id}:{kind}:{read}",
            payload={"attempt_id": attempt_id, "read": read},
            max_age=OPEN_FOR + POLL_EVERY,
            delay=POLL_EVERY,
        ),
        now=now,
    )


async def _expire_if_old(session: AsyncSession, row: Row[Any], clock: Clock) -> bool:
    now = clock.now()
    shown = shown_status(ConfirmationStatus(row.status), row.created_at, now)
    if shown is not ConfirmationStatus.EXPIRED:
        return False
    await session.execute(
        _FINISH,
        {"id": row.id, "status": "expired", "failure_code": None, "score": None, "now": now},
    )
    return True


async def _finish(
    session: AsyncSession,
    attempt_id: str,
    status: ConfirmationStatus,
    code: str | None,
    score: int | None,
    clock: Clock,
) -> None:
    await session.execute(
        _FINISH,
        {
            "id": attempt_id,
            "status": status.value,
            "failure_code": code,
            "score": score,
            "now": clock.now(),
        },
    )
    await service._audit(
        session,
        Actor("system"),
        "change",
        f"kyc.face_confirmation.{status.value}",
        attempt_id,
        clock,
        details={"code": code} if code else None,
        resource=RESOURCE,
    )


def _url_data(attempt_id: str) -> bytes:
    return associated_data("kyc_face_confirmations", "url", attempt_id)


def _view(row: Row[Any], sealer: Sealer | None, now: datetime) -> ConfirmationView:
    status = shown_status(ConfirmationStatus(row.status), row.created_at, now)
    url = None
    if status is ConfirmationStatus.OPEN and sealer is not None:
        url = sealer.open(row.url_sealed, _url_data(row.id))
    counts_until = None
    if counts(status, row.finished_at, now):
        counts_until = row.finished_at + COUNTS_FOR
    return ConfirmationView(
        id=row.id,
        status=status,
        url=url,
        failure_code=row.failure_code,
        created_at=row.created_at,
        expires_at=row.created_at + OPEN_FOR,
        counts_until=counts_until,
    )
