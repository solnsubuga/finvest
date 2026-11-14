"""The liveness check: proof that the selfie is of a live person in front of the camera.

A face match compares two photos, so a printed photo or a screen held up to the camera can pass
it. A liveness provider runs the camera itself, on a page of its own the client opens, and
answers whether a live person was there. The face it captured then becomes the check's selfie,
so the face match compares the live capture with the document, not a photo the client chose.

Pure rules and the provider port. Nothing here reads a clock, a database or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.modules.kyc.domain.checks import CheckStatus, DocumentType, PhotoKind, required_photos

# How long a started attempt stays open. The provider's page is meant to take a minute or two;
# after this the attempt shows as expired and the client may start another.
OPEN_FOR = timedelta(minutes=30)
# Attempts per check, so a client cannot start paid requests without end.
MAX_ATTEMPTS = 10


class LivenessStatus(StrEnum):
    STARTING = "starting"  # saved; the worker is asking the provider for a session
    OPEN = "open"  # the provider's page is ready for the client
    PASSED = "passed"  # a live person; the captured face is the check's selfie
    FAILED = "failed"  # the provider did not see a live person, or the capture was unusable
    NOT_STARTED = "not_started"  # the provider refused, or never confirmed, the session
    EXPIRED = "expired"  # open too long, or replaced by a newer attempt


# Statuses an attempt can still leave. The others are final.
WAITING = frozenset({LivenessStatus.STARTING, LivenessStatus.OPEN})


def shown_status(status: LivenessStatus, created_at: datetime, now: datetime) -> LivenessStatus:
    """What the client sees. An attempt left open past `OPEN_FOR` is expired, saved or not."""
    if status in WAITING and now - created_at >= OPEN_FOR:
        return LivenessStatus.EXPIRED
    return status


def selfie_editable(status: CheckStatus, retake_photos: frozenset[PhotoKind]) -> bool:
    """Whether the selfie may change now. The photo trigger of migration 0050 holds the same."""
    if status is CheckStatus.DRAFT:
        return True
    return status is CheckStatus.AWAITING_CLIENT and PhotoKind.SELFIE in retake_photos


def start_problem(
    status: CheckStatus,
    document_type: DocumentType,
    retake_photos: frozenset[PhotoKind],
    *,
    attempts: int,
) -> str | None:
    """Why the client may not start an attempt now, or None if they may."""
    if PhotoKind.SELFIE not in required_photos(document_type):
        return "photo_not_part_of_document"
    if not selfie_editable(status, retake_photos):
        return "selfie_not_editable"
    if attempts >= MAX_ATTEMPTS:
        return "too_many_attempts"
    return None


class LivenessSelfie(StrEnum):
    """Where the check's current selfie came from, as staff and the automatic decision see it."""

    LIVE = "live"  # captured by a liveness attempt that passed
    CAMERA = "camera"  # sent by the client, with no liveness behind it


# The port ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Started:
    request_id: str
    # The provider's page for this attempt. It carries a token, so it is sealed at rest and
    # given only to the client it belongs to.
    url: str


@dataclass(frozen=True, slots=True)
class NotStarted:
    """The provider answered and will not open a session."""

    code: str


@dataclass(frozen=True, slots=True)
class StillOpen:
    pass


@dataclass(frozen=True, slots=True)
class Live:
    """A live person. `photo` is the face the provider captured, and `content_type` its type."""

    photo: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class NotLive:
    code: str


class LivenessProvider(Protocol):
    name: str

    async def start(self, key: str) -> Started | NotStarted:
        """Open a session. `key` is the attempt's ID, the same on every retry of it.

        Raises `ProviderDeferred` when the request certainly did not arrive and
        `ProviderUncertain` when it may have.
        """
        ...

    async def result(self, request_id: str) -> StillOpen | Live | NotLive:
        """Where the session stands. Raises `ProviderDeferred` when that cannot be read now."""
        ...
