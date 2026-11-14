"""Face confirmations: a live selfie, matched to the approved check, before a sign-in change.

A passkey or authenticator app proves the device. A live selfie proves the person. Before a
client changes their own password or mobile number, they take a selfie on the liveness
provider's page, and the face it captured is compared with the selfie of their approved
identity check. Only a live person whose face matches confirms the change.

Pure rules and the face match port. Nothing here reads a clock, a database or the network.
See docs/identity-checks.md#face-confirmations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.modules.kyc.domain.provider import PhotoRef

# How long an attempt may take, from start to a face match answer. The provider's page is meant
# to take a minute or two; after this the attempt shows as expired and the client starts again.
OPEN_FOR = timedelta(minutes=15)
# A passed confirmation counts for the session it was taken on, this long. The same window as a
# passkey or app confirmation, so the client confirms both and then makes the change.
COUNTS_FOR = timedelta(minutes=10)
# Attempts a client may start in a day, so nobody can start paid requests without end.
MAX_PER_DAY = 5
DAY = timedelta(days=1)
# The same bar as the face match at hand-in: called identical, at this confidence or above.
MATCH_PASS = 80


class ConfirmationStatus(StrEnum):
    STARTING = "starting"  # saved; the worker is asking the provider for a liveness session
    OPEN = "open"  # the provider's page is ready for the client
    MATCHING = "matching"  # a live person; the capture is being compared with the check's selfie
    PASSED = "passed"  # a live person whose face matches: the change may go ahead
    FAILED = "failed"  # not live, no usable capture, or the face did not match
    NOT_STARTED = "not_started"  # the provider refused, or never confirmed, a request
    EXPIRED = "expired"  # took too long, or replaced by a newer attempt


# Statuses an attempt can still leave. The others are final.
WAITING = frozenset(
    {ConfirmationStatus.STARTING, ConfirmationStatus.OPEN, ConfirmationStatus.MATCHING}
)


def shown_status(
    status: ConfirmationStatus, created_at: datetime, now: datetime
) -> ConfirmationStatus:
    """What the client sees. An attempt still waiting after `OPEN_FOR` is expired."""
    if status in WAITING and now - created_at >= OPEN_FOR:
        return ConfirmationStatus.EXPIRED
    return status


def counts(status: ConfirmationStatus, finished_at: datetime | None, now: datetime) -> bool:
    """Whether a passed attempt still confirms a change made now."""
    return (
        status is ConfirmationStatus.PASSED
        and finished_at is not None
        and timedelta(0) <= now - finished_at < COUNTS_FOR
    )


def start_problem(*, has_reference: bool, started_today: int) -> str | None:
    """Why the client may not start an attempt now, or None if they may."""
    if not has_reference:
        # Nothing to compare the face with: an approved check with a selfie is needed.
        return "no_selfie_on_file"
    if started_today >= MAX_PER_DAY:
        return "too_many_attempts"
    return None


def match_passes(score: int | None, identical: bool) -> bool:
    """A face match passes when it is called identical and its score is high enough.

    Anything less is a failure here, not a question for a person: the client tries again or
    asks support, who can change a number with two staff.
    """
    return identical and score is not None and score >= MATCH_PASS


# The port ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchSent:
    request_id: str


@dataclass(frozen=True, slots=True)
class MatchRefused:
    """The provider answered and will not compare these photos."""

    code: str


@dataclass(frozen=True, slots=True)
class MatchPending:
    pass


@dataclass(frozen=True, slots=True)
class MatchAnswer:
    """The comparison. `score` is a whole percentage, or None when it was not understood."""

    score: int | None
    identical: bool


@dataclass(frozen=True, slots=True)
class MatchNotRun:
    """The provider ended the request without comparing the photos."""

    code: str


class FaceMatcher(Protocol):
    name: str

    async def send(self, key: str, live: PhotoRef, on_file: PhotoRef) -> MatchSent | MatchRefused:
        """Ask for a comparison. `key` is the attempt's ID, the same on every retry of it.

        Raises `ProviderDeferred` when the request certainly did not arrive and
        `ProviderUncertain` when it may have.
        """
        ...

    async def result(self, request_id: str) -> MatchPending | MatchAnswer | MatchNotRun:
        """The comparison, once made. Raises `ProviderDeferred` when it cannot be read now."""
        ...
