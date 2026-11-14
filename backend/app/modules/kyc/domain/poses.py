"""The guided selfie: a straight-on selfie and a frame for each of four head poses.

The owner's decision (docs/decisions.md, 2026-11-14): the client takes the selfie looking
straight at the camera, then turns left, turns right, looks up and looks down. Every frame is
checked for quality, and only a good one is kept, so staff and the face match see clear images.
The app checks each frame on the phone first and guides the client; the API checks it again
with the face quality service before keeping it, because the phone can be wrong or tampered
with.

Pure rules and the face quality port. Nothing here reads a clock, a database or the network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class Pose(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


# Turning the head moves the yaw, nodding moves the pitch. Which way is negative depends on the
# camera and the face service, so a turn is measured by size here, and left and right are only
# required to go opposite ways of each other (and up and down the same).
TURNED = {Pose.LEFT: "yaw", Pose.RIGHT: "yaw", Pose.UP: "pitch", Pose.DOWN: "pitch"}
OPPOSITE = {Pose.LEFT: Pose.RIGHT, Pose.RIGHT: Pose.LEFT, Pose.UP: Pose.DOWN, Pose.DOWN: Pose.UP}

# Degrees. A straight-on selfie is within STRAIGHT of the camera both ways. A turn is at least
# TURN, a nod at least NOD, and the other angle stays within STEADY.
STRAIGHT = 10
TURN = 20
NOD = 12
STEADY = 15
# The face's width as a share of the image's, and how far its middle may be from the image's.
FACE_WIDTH_AT_LEAST = 0.25
OFF_CENTRE_AT_MOST = 0.2


class Quality(StrEnum):
    """What a frame can be wrong for. Each is a short prompt the app shows to retake it."""

    NO_FACE = "no_face"
    SEVERAL_FACES = "several_faces"
    FACE_TOO_SMALL = "face_too_small"
    NOT_CENTRED = "not_centred"
    BLURRY = "blurry"
    TOO_DARK = "too_dark"
    TOO_BRIGHT = "too_bright"
    GLARE = "glare"
    EYES_CLOSED = "eyes_closed"
    LOOK_STRAIGHT = "look_straight"
    TURN_MORE = "turn_more"
    HOLD_STEADY = "hold_steady"
    WRONG_WAY = "wrong_way"
    NOT_READ = "not_read"


@dataclass(frozen=True, slots=True)
class FrameReading:
    """What the face quality service saw in one frame.

    `faces` is how many faces it found. The rest describe the face it looked at: angles in
    degrees, the face's width and the offsets of its middle as shares of the image, and the
    quality characteristics it found wanting. A value it did not give is None.
    """

    faces: int
    yaw: float | None = None
    pitch: float | None = None
    face_width: float | None = None
    off_centre_x: float | None = None
    off_centre_y: float | None = None
    failed: frozenset[Quality] = field(default_factory=frozenset)
    # Each eye, if the service could tell: True open, False closed, absent not known.
    eyes_open: Mapping[str, bool] = field(default_factory=dict)


def frame_problems(
    pose: Pose | None, reading: FrameReading, opposite: FrameReading | None = None
) -> tuple[Quality, ...]:
    """Why a frame should be taken again, or nothing if it is good. `pose` None is the
    straight-on selfie. `opposite` is the frame already kept for the opposite pose, if any.

    A face the service could not find, size, place or angle is a reason to retake.
    """
    if reading.faces == 0:
        return (Quality.NO_FACE,)
    if reading.faces > 1:
        return (Quality.SEVERAL_FACES,)
    problems: list[Quality] = []
    if reading.face_width is None or reading.face_width < FACE_WIDTH_AT_LEAST:
        problems.append(Quality.FACE_TOO_SMALL)
    if (
        reading.off_centre_x is None
        or reading.off_centre_y is None
        or max(abs(reading.off_centre_x), abs(reading.off_centre_y)) > OFF_CENTRE_AT_MOST
    ):
        problems.append(Quality.NOT_CENTRED)
    problems += [
        q
        for q in (Quality.BLURRY, Quality.TOO_DARK, Quality.TOO_BRIGHT, Quality.GLARE)
        if q in reading.failed
    ]
    if _eyes_closed(pose, reading):
        problems.append(Quality.EYES_CLOSED)
    problems += _angle_problems(pose, reading, opposite)
    return tuple(dict.fromkeys(problems))


def _eyes_closed(pose: Pose | None, reading: FrameReading) -> bool:
    closed = [eye for eye, is_open in reading.eyes_open.items() if not is_open]
    if pose in (Pose.LEFT, Pose.RIGHT):
        # Turned away, the far eye is often hidden: closed only if both are seen closed.
        return len(closed) >= 2
    return bool(closed)


def _angle_problems(
    pose: Pose | None, reading: FrameReading, opposite: FrameReading | None
) -> list[Quality]:
    yaw, pitch = reading.yaw, reading.pitch
    if yaw is None or pitch is None:
        return [Quality.NOT_READ]
    if pose is None:
        return [] if abs(yaw) <= STRAIGHT and abs(pitch) <= STRAIGHT else [Quality.LOOK_STRAIGHT]
    moved, other = (yaw, pitch) if TURNED[pose] == "yaw" else (pitch, yaw)
    if abs(moved) < (TURN if TURNED[pose] == "yaw" else NOD):
        return [Quality.TURN_MORE]
    if abs(other) > STEADY:
        return [Quality.HOLD_STEADY]
    if opposite is not None:
        theirs = opposite.yaw if TURNED[pose] == "yaw" else opposite.pitch
        if theirs is not None and (theirs > 0) == (moved > 0):
            return [Quality.WRONG_WAY]
    return []


@dataclass(frozen=True, slots=True)
class Kept:
    """A pose frame kept on the check, with the angles it was taken at."""

    pose: Pose
    yaw: int
    pitch: int


def poses_missing(kept: Mapping[Pose, Kept]) -> list[Pose]:
    """The poses still to take, in the order the app asks for them."""
    return [pose for pose in Pose if pose not in kept]


def poses_apart(kept: Mapping[Pose, Kept]) -> bool:
    """Whether each pair went opposite ways: left against right, up against down.

    Checked again at hand-in, because the two of a pair can be taken in either order.
    """
    for first, second, angle in ((Pose.LEFT, Pose.RIGHT, "yaw"), (Pose.UP, Pose.DOWN, "pitch")):
        a, b = kept.get(first), kept.get(second)
        if a is None or b is None:
            return False
        if (getattr(a, angle) > 0) == (getattr(b, angle) > 0):
            return False
    return True


class QualityUnavailable(Exception):
    """The face quality service did not answer. Nothing is kept; the client tries again."""


class FaceQuality(Protocol):
    """Reads one frame. It changes nothing, so it can be asked as often as needed."""

    async def read(self, image: bytes) -> FrameReading: ...
