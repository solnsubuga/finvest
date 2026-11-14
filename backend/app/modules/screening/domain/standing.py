"""What a screen found, and where a client stands with screening. Pure: no I/O.

A screen that finds anything is a possible match. Staff decide each possible match on a
compliance case, with two people. A listed entity that staff already ruled out for this client,
or accepted as a politically exposed person, is recognised the next time it comes up and opens
no new case, so a rescreen does not raise the same false alarm every month.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.screening.domain.provider import Match

# Decided for now (docs/decisions.md): every approved client is screened again once this long
# has passed since their last finished screen. Lists change daily, so a monthly rescreen is the
# floor, not the aim: a provider's ongoing monitoring can replace it.
RESCREEN_EVERY = timedelta(days=30)
# A screen the provider never answered stops waiting after this and is recorded unavailable.
GIVE_UP_AFTER = timedelta(hours=6)


class Reason(StrEnum):
    ONBOARDING = "onboarding"  # the client handed in their identity check
    RESCREEN = "rescreen"  # the scheduled sweep
    PAYOUT = "payout"  # before money leaves
    STAFF = "staff"  # a member of staff asked for one


class RunStatus(StrEnum):
    PENDING = "pending"
    CLEAR = "clear"  # the provider found nothing
    POSSIBLE_MATCH = "possible_match"  # the provider found at least one listed entity
    UNAVAILABLE = "unavailable"  # the provider did not answer in time, or refused
    NOT_RUN = "not_run"  # no provider is set up, so nobody was screened
    NO_SUBJECT = "no_subject"  # the client has no handed-in identity check to screen

    @property
    def finished(self) -> bool:
        return self is not RunStatus.PENDING


class Outcome(StrEnum):
    """Where a client stands, for the identity check review and for payouts."""

    CLEAR = "clear"
    # A match is waiting for staff on an open case.
    POSSIBLE_MATCH = "possible_match"
    # Two members of staff confirmed the client is on a sanctions list.
    CONFIRMED_MATCH = "confirmed_match"
    UNAVAILABLE = "unavailable"
    PENDING = "pending"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class Found:
    status: RunStatus
    # Matches not already decided for this client. Only these open a case.
    new: tuple[Match, ...]


def what_was_found(matches: Iterable[Match], known_entity_ids: frozenset[str]) -> Found:
    found = tuple(matches)
    if not found:
        return Found(RunStatus.CLEAR, ())
    return Found(
        RunStatus.POSSIBLE_MATCH,
        tuple(match for match in found if match.entity_id not in known_entity_ids),
    )


@dataclass(frozen=True, slots=True)
class LastRun:
    status: RunStatus
    requested_at: datetime
    finished_at: datetime | None


def standing(
    last: LastRun | None,
    *,
    open_case: bool,
    sanctioned: bool,
) -> Outcome | None:
    """None for a client never screened.

    A confirmed sanctions match outweighs everything, then an open case. Otherwise the last
    screen speaks: a possible match with nothing new in it (every entity already ruled out or
    accepted) is clear.
    """
    if sanctioned:
        return Outcome.CONFIRMED_MATCH
    if open_case:
        return Outcome.POSSIBLE_MATCH
    if last is None:
        return None
    return {
        RunStatus.PENDING: Outcome.PENDING,
        RunStatus.CLEAR: Outcome.CLEAR,
        RunStatus.POSSIBLE_MATCH: Outcome.CLEAR,
        RunStatus.UNAVAILABLE: Outcome.UNAVAILABLE,
        RunStatus.NOT_RUN: Outcome.NOT_RUN,
        RunStatus.NO_SUBJECT: Outcome.NOT_RUN,
    }[last.status]


def due_for_rescreen(last_finished_at: datetime | None, now: datetime) -> bool:
    return last_finished_at is None or now - last_finished_at >= RESCREEN_EVERY


# A payout goes ahead on a clear screen finished at most this long before it is asked for.
PAYOUT_SCREEN_FRESH = timedelta(hours=24)


class Clearance(StrEnum):
    """Whether money may leave for a client now."""

    CLEAR = "clear"
    # A screen is under way, a case is open, or the last screen could not tell: wait.
    WAIT = "wait"
    # No recent screen: ask for one, and wait for it.
    SCREEN = "screen"
    # Two members of staff confirmed a sanctions match. Nothing leaves.
    BLOCKED = "blocked"


def payout_clearance(
    last: LastRun | None, *, open_case: bool, sanctioned: bool, now: datetime
) -> Clearance:
    """The rule PostgreSQL also holds (migration 0181), so a payout cannot skip it.

    A screen recorded as not run, because no provider is set up, clears like a clear one: a
    payout then waits only for the worker to record it. A screen that found only entities
    already decided for this client is clear.
    """
    if sanctioned:
        return Clearance.BLOCKED
    if open_case:
        return Clearance.WAIT
    if last is None:
        return Clearance.SCREEN
    if last.status is RunStatus.PENDING:
        return Clearance.WAIT
    assert last.finished_at is not None
    if now - last.finished_at > PAYOUT_SCREEN_FRESH:
        return Clearance.SCREEN
    if last.status in (RunStatus.UNAVAILABLE, RunStatus.NO_SUBJECT):
        # Asked moments ago and could not tell: staff screen the client again by hand.
        return Clearance.WAIT
    return Clearance.CLEAR
