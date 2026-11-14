"""Pose frames for the guided selfie. See docs/identity-checks.md#guided-selfie.

The API reads each frame with the face quality service before anything is kept. The rules are
in `domain/poses.py`; the table and its guard are migration 0371. Pose frames follow the
selfie: they may change only while it may, and a new selfie retires them (`service.add_photo`).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.kyc import service
from app.modules.kyc.domain.checks import CheckStatus, PhotoKind
from app.modules.kyc.domain.liveness import selfie_editable
from app.modules.kyc.domain.poses import OPPOSITE, FrameReading, Pose
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

# Frames kept per check, retakes included, so a client cannot fill storage without end.
MAX_FRAMES_PER_CHECK = 60

_HAS_SELFIE = text(
    "SELECT EXISTS (SELECT FROM kyc_photos WHERE check_id = :check_id AND kind = 'selfie' "
    "AND replaced_at IS NULL)"
)
_COUNT = text("SELECT count(*) FROM kyc_pose_frames WHERE check_id = :check_id")
_RETIRE = text(
    "UPDATE kyc_pose_frames SET replaced_at = :now "
    "WHERE check_id = :check_id AND pose = :pose AND replaced_at IS NULL"
)
_INSERT = text(
    "INSERT INTO kyc_pose_frames (id, check_id, pose, storage_key, content_type, byte_size, "
    "sha256, yaw, pitch, created_at) VALUES (:id, :check_id, :pose, :storage_key, "
    ":content_type, :byte_size, :sha256, :yaw, :pitch, :now)"
)
_CURRENT = text(
    "SELECT id, pose, storage_key, content_type, byte_size, yaw, pitch, created_at "
    "FROM kyc_pose_frames WHERE check_id = :check_id AND replaced_at IS NULL ORDER BY pose"
)
_KEPT_READING = text(
    "SELECT yaw, pitch FROM kyc_pose_frames WHERE check_id = :check_id AND pose = :pose "
    "AND replaced_at IS NULL"
)


@dataclass(frozen=True, slots=True)
class PoseFrame:
    id: str
    pose: Pose
    storage_key: str
    content_type: str
    byte_size: int
    yaw: int
    pitch: int
    created_at: datetime


def frame_key(check_id: str, frame_id: str) -> str:
    """Where a pose frame's file is kept in private storage, beside the check's photos."""
    return f"kyc/{check_id}/poses/{frame_id}"


async def frame_slot(
    session: AsyncSession, client: Actor, pose: Pose, *, content_type: str, byte_size: int
) -> tuple[str, FrameReading | None]:
    """The check a frame of this pose may be added to now, and the kept frame of the opposite
    pose, if any, so the new one can be told to go the other way. Refuses as add_frame would.

    Run before the frame is read or stored, so a refused frame is never sent or kept.
    """
    row = await _frame_row(session, client, content_type, byte_size)
    opposite = (
        await session.execute(_KEPT_READING, {"check_id": row.id, "pose": OPPOSITE[pose].value})
    ).one_or_none()
    reading = (
        None if opposite is None else FrameReading(faces=1, yaw=opposite.yaw, pitch=opposite.pitch)
    )
    return str(row.id), reading


async def add_frame(
    session: AsyncSession,
    client: Actor,
    pose: Pose,
    *,
    frame_id: str,
    content_type: str,
    byte_size: int,
    sha256: str,
    reading: FrameReading,
    clock: Clock,
) -> PoseFrame:
    """Keep a frame the face quality service passed, retiring the earlier one of its pose."""
    row = await _frame_row(session, client, content_type, byte_size)
    if reading.yaw is None or reading.pitch is None:
        raise ApplicationError("conflict")
    now = clock.now()
    key = frame_key(row.id, frame_id)
    await session.execute(_RETIRE, {"check_id": row.id, "pose": pose.value, "now": now})
    yaw, pitch = round(reading.yaw), round(reading.pitch)
    # The check's row is locked first (`_frame_row`), so two uploads for one check take turns:
    # the later retires the earlier, and the unique index never sees two current frames.
    await session.execute(
        _INSERT,
        {
            "id": frame_id,
            "check_id": row.id,
            "pose": pose.value,
            "storage_key": key,
            "content_type": content_type,
            "byte_size": byte_size,
            "sha256": sha256,
            "yaw": yaw,
            "pitch": pitch,
            "now": now,
        },
    )
    return PoseFrame(frame_id, pose, key, content_type, byte_size, yaw, pitch, now)


async def current_frames(session: AsyncSession, check_id: str) -> list[PoseFrame]:
    """The frames kept with the check's current selfie, for the client and for staff."""
    rows = await session.execute(_CURRENT, {"check_id": check_id})
    return [_frame(r) for r in rows]


async def _frame_row(
    session: AsyncSession, client: Actor, content_type: str, byte_size: int
) -> Row[Any]:
    row = await service._editable(session, client)
    retakes = frozenset(PhotoKind(kind) for kind in row.retake_photos)
    if PhotoKind.SELFIE not in service._required(row):
        raise service._invalid("pose", "photo_not_part_of_document")
    if not selfie_editable(CheckStatus(row.status), retakes):
        raise ApplicationError("conflict")
    if content_type not in service.PHOTO_CONTENT_TYPES:
        raise service._invalid("content_type", "unsupported_content_type")
    if type(byte_size) is not int or not 0 < byte_size <= service.MAX_PHOTO_BYTES:
        raise service._invalid("byte_size", "invalid_size")
    if not (await session.execute(_HAS_SELFIE, {"check_id": row.id})).scalar_one():
        # A new selfie retires the poses, so they are taken after it.
        raise service._invalid("pose", "selfie_first")
    taken = (await session.execute(_COUNT, {"check_id": row.id})).scalar_one()
    if taken >= MAX_FRAMES_PER_CHECK:
        raise service._invalid("photo", "too_many_photos")
    return row


def _frame(row: Row[Any]) -> PoseFrame:
    return PoseFrame(
        row.id,
        Pose(row.pose),
        row.storage_key,
        row.content_type,
        row.byte_size,
        row.yaw,
        row.pitch,
        row.created_at,
    )
