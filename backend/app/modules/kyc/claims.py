"""Claim checks for clients carried over from Finvest 1.0. The rules are in `domain/claims.py`.

The client takes a selfie and a photo of the front of the document on file. One member of staff
compares them with each other and with the carried-over check, which they open as any other
identity check, and says whether they match. The decision is published as
`kyc.claim_check_decided`, so the migration module moves the claim on in the worker without
this module touching its tables. Files are stored as identity photos are: refusals first, the
file outside any transaction, then the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs.outbox import Consumer, publish
from app.modules.kyc.domain.checks import PhotoKind
from app.modules.kyc.domain.claims import (
    CLAIM_PHOTOS,
    DECIDED,
    ClaimCheckStatus,
    can_move,
    retake_problem,
)
from app.modules.kyc.service import (
    DEFAULT_PAGE_SIZE,
    MAX_PHOTO_BYTES,
    MAX_PHOTOS_PER_CHECK,
    PHOTO_CONTENT_TYPES,
    PhotoView,
    _audit,
    _invalid,
    _message,
    _person,
)
from app.modules.notifications import contract as notifications
from app.shared.actors import Actor, ActorKind
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "kyc_claim_check"
DECIDED_TOPIC = "kyc.claim_check_decided"
# Who hears that a claim check was decided. The migration module adds its consumer here.
DECIDED_CONSUMERS: tuple[Consumer, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimCheckView:
    id: str
    client_id: str
    # The carried-over identity check the photos are compared with.
    check_id: str
    status: ClaimCheckStatus
    submission: int
    submitted_at: datetime | None
    retake_photos: frozenset[PhotoKind]
    retake_requested_at: datetime | None
    client_message: str | None
    decided_at: datetime | None
    decided_by: str | None
    photos: tuple[PhotoView, ...]

    @property
    def retaken(self) -> frozenset[PhotoKind]:
        """The photos staff asked for that the client has already taken again."""
        if self.retake_requested_at is None:
            return frozenset()
        asked = self.retake_requested_at
        # Strictly later: a photo from the same instant as the request is the one staff saw.
        return self.retake_photos & {p.kind for p in self.photos if p.created_at > asked}

    @property
    def missing(self) -> list[str]:
        """The photos still needed before the client can hand it in."""
        have = frozenset(photo.kind for photo in self.photos)
        if self.status is ClaimCheckStatus.AWAITING_CLIENT:
            needed = self.retake_photos - self.retaken
        else:
            needed = CLAIM_PHOTOS - have
        return [kind.value for kind in PhotoKind if kind in needed]


_COLUMNS = (
    "id, client_id, check_id, status, submission, submitted_at, retake_photos, "
    "retake_requested_at, client_message, decided_at, decided_by"
)
_CARRIED_OVER = text(
    "SELECT id FROM kyc_checks WHERE client_id = :client_id AND source = 'finvest1' "
    "AND status = 'approved'"
)
_INSERT = text(
    "INSERT INTO kyc_claim_checks (id, client_id, check_id, created_at, updated_at) "
    "VALUES (:id, :client_id, :check_id, :now, :now) "
    "ON CONFLICT ON CONSTRAINT uq_kyc_claim_checks_client DO NOTHING"
)
_BY_CLIENT = text(f"SELECT {_COLUMNS} FROM kyc_claim_checks WHERE client_id = :client_id")  # noqa: S608
_BY_CLIENT_LOCKED = text(
    f"SELECT {_COLUMNS} FROM kyc_claim_checks WHERE client_id = :client_id FOR UPDATE"  # noqa: S608
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM kyc_claim_checks WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} FROM kyc_claim_checks WHERE id = :id FOR UPDATE")  # noqa: S608
_QUEUE = text(
    f"SELECT {_COLUMNS} FROM kyc_claim_checks WHERE status = 'needs_review' "  # noqa: S608
    "ORDER BY submitted_at, id LIMIT :limit"
)
_PHOTOS = text(
    "SELECT id, kind, storage_key, content_type, byte_size, created_at FROM kyc_claim_photos "
    "WHERE claim_check_id = :id AND replaced_at IS NULL ORDER BY kind"
)
_PHOTO_COUNT = text("SELECT count(*) FROM kyc_claim_photos WHERE claim_check_id = :id")
_REPLACE_PHOTO = text(
    "UPDATE kyc_claim_photos SET replaced_at = :now "
    "WHERE claim_check_id = :id AND kind = :kind AND replaced_at IS NULL"
)
_INSERT_PHOTO = text(
    "INSERT INTO kyc_claim_photos (id, claim_check_id, kind, storage_key, content_type, "
    "byte_size, sha256, created_at) VALUES (:photo_id, :id, :kind, :storage_key, "
    ":content_type, :byte_size, :sha256, :now)"
)
_SUBMIT = text(
    "UPDATE kyc_claim_checks SET status = 'needs_review', submission = submission + 1, "
    "submitted_at = :now, retake_photos = '{}', retake_requested_at = NULL, "
    "client_message = NULL, updated_at = :now WHERE id = :id"
)
_DECIDE = text(
    "UPDATE kyc_claim_checks SET status = :status, decided_at = :now, decided_by = :staff, "
    "updated_at = :now WHERE id = :id"
)
_ASK_FOR_PHOTOS = text(
    "UPDATE kyc_claim_checks SET status = 'awaiting_client', retake_photos = :photos, "
    "retake_requested_at = :now, client_message = :client_message, updated_at = :now "
    "WHERE id = :id"
)


def photo_key(claim_check_id: str, photo_id: str) -> str:
    """Where a claim photo's file is kept in private storage."""
    return f"kyc-claims/{claim_check_id}/{photo_id}"


# The client --------------------------------------------------------------------------------------


async def start(session: AsyncSession, client: Actor, clock: Clock) -> ClaimCheckView:
    """Start the client's claim check, or return the one they started. Only for a client whose
    identity was carried over from Finvest 1.0."""
    client_id = _person(client, "client")
    carried = (await session.execute(_CARRIED_OVER, {"client_id": client_id})).one_or_none()
    if carried is None:
        raise ApplicationError("not_found")
    now = clock.now()
    await session.execute(
        _INSERT, {"id": push_id(), "client_id": client_id, "check_id": carried.id, "now": now}
    )
    row = (await session.execute(_BY_CLIENT, {"client_id": client_id})).one()
    return await _view(session, row)


async def for_client(session: AsyncSession, client: Actor) -> ClaimCheckView | None:
    client_id = _person(client, "client")
    row = (await session.execute(_BY_CLIENT, {"client_id": client_id})).one_or_none()
    return None if row is None else await _view(session, row)


async def photo_slot(
    session: AsyncSession, client: Actor, kind: PhotoKind, *, content_type: str, byte_size: int
) -> str:
    """The claim check a photo of this kind may be added to now, or the refusal. Run before the
    file is stored, so a refused photo never leaves a file behind."""
    row = await _editable(session, client)
    _check_photo(row, kind, content_type, byte_size)
    await _require_room_for_photo(session, row.id)
    return str(row.id)


async def add_photo(
    session: AsyncSession,
    client: Actor,
    kind: PhotoKind,
    *,
    content_type: str,
    byte_size: int,
    sha256: str,
    clock: Clock,
    photo_id: str | None = None,
) -> PhotoView:
    """Record a photo the client uploaded, replacing the current one of the same kind."""
    row = await _editable(session, client)
    _check_photo(row, kind, content_type, byte_size)
    await _require_room_for_photo(session, row.id)
    now = clock.now()
    photo_id = photo_id or push_id()
    storage_key = photo_key(row.id, photo_id)
    await session.execute(_REPLACE_PHOTO, {"id": row.id, "kind": kind.value, "now": now})
    await session.execute(
        _INSERT_PHOTO,
        {
            "photo_id": photo_id,
            "id": row.id,
            "kind": kind.value,
            "storage_key": storage_key,
            "content_type": content_type,
            "byte_size": byte_size,
            "sha256": sha256,
            "now": now,
        },
    )
    return PhotoView(photo_id, kind, storage_key, content_type, byte_size, now)


async def submit(session: AsyncSession, client: Actor, clock: Clock) -> ClaimCheckView:
    """Hand the photos in for a person to compare. Answers which photos are missing."""
    row = await _editable(session, client)
    view = await _view(session, row)
    if view.missing:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=[name], code="missing") for name in view.missing],
        )
    _require_move("client", view.status, ClaimCheckStatus.NEEDS_REVIEW)
    await session.execute(_SUBMIT, {"id": row.id, "now": clock.now()})
    await _audit(
        session, client, "change", "kyc.claim_check.submitted", row.id, clock, resource=RESOURCE
    )
    return await _view_by_id(session, row.id)


# Staff ------------------------------------------------------------------------------------------


async def review_queue(
    session: AsyncSession, *, limit: int = DEFAULT_PAGE_SIZE
) -> list[ClaimCheckView]:
    """Claim checks waiting for a person, the longest waiting first."""
    rows = (await session.execute(_QUEUE, {"limit": max(1, min(limit, 200))})).all()
    return [await _view(session, row) for row in rows]


async def for_staff(session: AsyncSession, claim_check_id: str) -> ClaimCheckView:
    return await _view_by_id(session, claim_check_id)


async def photos_for_viewing(
    session: AsyncSession, staff: Actor, claim_check_id: str, clock: Clock
) -> tuple[PhotoView, ...]:
    """The current photos, for signing links to them. Each request is on the trail."""
    _person(staff, "staff")
    view = await _view_by_id(session, claim_check_id)
    await _audit(
        session, staff, "access", "kyc.claim_check.photos_viewed", view.id, clock, resource=RESOURCE
    )
    return view.photos


async def decide(
    session: AsyncSession,
    staff: Actor,
    claim_check_id: str,
    *,
    matched: bool,
    reason: str,
    clock: Clock,
) -> ClaimCheckView:
    """Say whether the photos match each other and the carried-over check. Final.

    Published as `kyc.claim_check_decided` in this same transaction, keyed by the claim check.
    """
    staff_id = _person(staff, "staff")
    target = ClaimCheckStatus.MATCHED if matched else ClaimCheckStatus.NOT_MATCHED
    row = await _for_decision(session, claim_check_id, target)
    now = clock.now()
    await session.execute(
        _DECIDE, {"id": row.id, "status": target.value, "staff": staff_id, "now": now}
    )
    await _audit(
        session,
        staff,
        "approval",
        f"kyc.claim_check.{target.value}",
        row.id,
        clock,
        reason=reason,
        resource=RESOURCE,
    )
    await publish(
        session,
        topic=DECIDED_TOPIC,
        idempotency_key=row.id,
        payload={"client_id": row.client_id, "claim_check_id": row.id, "matched": matched},
        consumers=DECIDED_CONSUMERS,
        now=now,
    )
    return await _view_by_id(session, row.id)


async def ask_for_photos(
    session: AsyncSession,
    staff: Actor,
    claim_check_id: str,
    photos: frozenset[PhotoKind],
    client_message: str,
    clock: Clock,
) -> ClaimCheckView:
    """Ask the client to take named photos again, with a message they will read."""
    _person(staff, "staff")
    row = await _for_decision(session, claim_check_id, ClaimCheckStatus.AWAITING_CLIENT)
    problem = retake_problem(photos)
    if problem is not None:
        raise _invalid("photos", problem)
    kinds = sorted(kind.value for kind in photos)
    await session.execute(
        _ASK_FOR_PHOTOS,
        {
            "id": row.id,
            "photos": kinds,
            "client_message": _message(client_message, required=True),
            "now": clock.now(),
        },
    )
    # The same notice as for an identity check. Once for each submission it is sent back.
    # No link: the notification links have no kind for a claim, and the app opens the claim.
    await notifications.notify(
        session,
        event_name="kyc_retake_requested",
        client_id=row.client_id,
        idempotency_key=f"claim:{row.id}:{row.submission}",
        variables={},
        now=clock.now(),
    )
    # The message can name the client, so only the photo kinds reach the trail.
    await _audit(
        session,
        staff,
        "approval",
        "kyc.claim_check.photos_requested",
        row.id,
        clock,
        reason="Asked the client for new photos",
        details={"photos": kinds},
        resource=RESOURCE,
    )
    return await _view_by_id(session, row.id)


# Internals --------------------------------------------------------------------------------------


async def _editable(session: AsyncSession, client: Actor) -> Row[Any]:
    client_id = _person(client, "client")
    row = (await session.execute(_BY_CLIENT_LOCKED, {"client_id": client_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    if row.status not in (ClaimCheckStatus.DRAFT, ClaimCheckStatus.AWAITING_CLIENT):
        raise ApplicationError("conflict")
    return row


async def _for_decision(
    session: AsyncSession, claim_check_id: str, target: ClaimCheckStatus
) -> Row[Any]:
    row = (await session.execute(_BY_ID_LOCKED, {"id": claim_check_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    _require_move("staff", ClaimCheckStatus(row.status), target)
    return row


def _require_move(actor: ActorKind, current: ClaimCheckStatus, target: ClaimCheckStatus) -> None:
    if current in DECIDED or not can_move(actor, current, target):
        raise ApplicationError("conflict")


async def _require_room_for_photo(session: AsyncSession, claim_check_id: str) -> None:
    # Every photo is kept, replaced ones too, so the same limit as an identity check applies.
    taken = (await session.execute(_PHOTO_COUNT, {"id": claim_check_id})).scalar_one()
    if taken >= MAX_PHOTOS_PER_CHECK:
        raise _invalid("photo", "too_many_photos")


def _check_photo(row: Row[Any], kind: PhotoKind, content_type: str, byte_size: int) -> None:
    if kind not in CLAIM_PHOTOS:
        raise _invalid("kind", "photo_not_part_of_claim")
    if row.status == ClaimCheckStatus.AWAITING_CLIENT and kind.value not in row.retake_photos:
        raise ApplicationError("conflict")
    if content_type not in PHOTO_CONTENT_TYPES:
        raise _invalid("content_type", "unsupported_content_type")
    if type(byte_size) is not int or not 0 < byte_size <= MAX_PHOTO_BYTES:
        raise _invalid("byte_size", "invalid_size")


async def _view_by_id(session: AsyncSession, claim_check_id: str) -> ClaimCheckView:
    row = (await session.execute(_BY_ID, {"id": claim_check_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return await _view(session, row)


async def _view(session: AsyncSession, row: Row[Any]) -> ClaimCheckView:
    photos = (await session.execute(_PHOTOS, {"id": row.id})).all()
    return ClaimCheckView(
        id=row.id,
        client_id=row.client_id,
        check_id=row.check_id,
        status=ClaimCheckStatus(row.status),
        submission=row.submission,
        submitted_at=row.submitted_at,
        retake_photos=frozenset(PhotoKind(kind) for kind in row.retake_photos),
        retake_requested_at=row.retake_requested_at,
        client_message=row.client_message,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        photos=tuple(
            PhotoView(
                id=photo.id,
                kind=PhotoKind(photo.kind),
                storage_key=photo.storage_key,
                content_type=photo.content_type,
                byte_size=photo.byte_size,
                created_at=photo.created_at,
            )
            for photo in photos
        ),
    )
