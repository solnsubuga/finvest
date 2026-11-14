"""Reading the name a mobile network holds for a number, and what follows from it. Pure.

A mobile money number is proved by the name its network holds for it, read through the provider
(ioTec Verify) after the client has entered the code texted to the number. The name is compared
with the name on the client's verified ID by `compare_names`, the same rule as for a bank
account:

* The same names, or one with a name left out: the number goes to staff, who check and approve
  it as two people, with the name the network gave. Nobody types it in.
* A different name: refused at once, for good. No member of staff and no pair of staff can
  approve it (the owner's rule).
* The network holds no name for the number, or has barred it: refused at once.

A lookup that got no answer leaves the number waiting, and the client may ask for it to be
looked up again, up to `MAX_LOOKUPS` times. Nothing is ever looked up again on its own after a
send that may have arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.modules.payouts.domain.destinations import (
    NameMatch,
    RejectionReason,
    compare_names,
)

# Lookups one number may have, counting the first. Each costs a fee at ioTec.
MAX_LOOKUPS = 3
# A lookup still unanswered this long after it was asked for is treated as unanswered: its job
# has stopped, so the client may ask again.
STALE_AFTER = timedelta(hours=1)
# How long the worker keeps reading a lookup ioTec accepted.
READ_WITHIN = timedelta(minutes=50)


class LookupStatus(StrEnum):
    QUEUED = "queued"  # asked for; the worker has not sent it yet
    SENT = "sent"  # ioTec accepted it; the worker reads the answer
    FOUND = "found"  # the network gave a name
    NO_NAME = "no_name"  # the network holds no name for the number, or barred it
    UNANSWERED = "unanswered"  # refused, failed, or may have arrived with no way to read it


SETTLED = frozenset({LookupStatus.FOUND, LookupStatus.NO_NAME, LookupStatus.UNANSWERED})


# What the provider says. ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Accepted:
    request_id: str


@dataclass(frozen=True, slots=True)
class NotReceived:
    """It certainly never arrived. Safe to send again."""


@dataclass(frozen=True, slots=True)
class Unanswerable:
    """Refused, or it may have arrived with nothing to read it back by. Not sent again."""

    code: str


@dataclass(frozen=True, slots=True)
class Pending:
    pass


@dataclass(frozen=True, slots=True)
class Found:
    registered_name: str


@dataclass(frozen=True, slots=True)
class NoName:
    pass


@dataclass(frozen=True, slots=True)
class NoAnswer:
    """The request ended at ioTec without a result: failed, expired or cancelled."""

    code: str


class LookupDeferred(Exception):
    """The provider could not be read just now. Reading changes nothing, so it is tried again."""


class RegisteredNamesProvider(Protocol):
    name: str

    async def send(self, msisdn: str, reference: str) -> Accepted | NotReceived | Unanswerable:
        """Ask for the name the network holds for `msisdn` (12 digits, 256...)."""
        ...

    async def read(self, request_id: str) -> Pending | Found | NoName | NoAnswer:
        """Read what became of an accepted lookup. Raises `LookupDeferred`."""
        ...


# What follows. ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToStaff:
    match: NameMatch  # exact or partial


@dataclass(frozen=True, slots=True)
class Refuse:
    reason: RejectionReason


def decide(id_name: str, answer: Found | NoName) -> ToStaff | Refuse:
    """What a lookup's answer does to the number, given the name on the client's verified ID."""
    if isinstance(answer, NoName):
        return Refuse(RejectionReason.NETWORK_NAME_UNAVAILABLE)
    match = compare_names(id_name, answer.registered_name)
    if match is NameMatch.DIFFERENT:
        return Refuse(RejectionReason.NAME_DIFFERENT)
    return ToStaff(match)


class CheckAgain(StrEnum):
    """Why a number cannot be looked up again right now."""

    NOT_WAITING = "destination_not_awaiting_proof"
    IN_PROGRESS = "name_check_in_progress"
    TOO_MANY = "name_check_limit_reached"


def why_not_again(
    latest: LookupStatus | None, asked_at: datetime | None, count: int, now: datetime
) -> CheckAgain | None:
    """None if the client may ask for another lookup of a number still waiting for its name.

    Allowed only once the last lookup is over without an answer, so two are never in flight,
    and never past `MAX_LOOKUPS`. A lookup that found a name is not repeated: it is decided
    again from the same answer once the client's ID is approved.
    """
    if latest is None:
        return None if count < MAX_LOOKUPS else CheckAgain.TOO_MANY
    if latest in (LookupStatus.QUEUED, LookupStatus.SENT):
        if asked_at is None or now - asked_at < STALE_AFTER:
            return CheckAgain.IN_PROGRESS
    elif latest is not LookupStatus.UNANSWERED:
        return CheckAgain.NOT_WAITING
    if count >= MAX_LOOKUPS:
        return CheckAgain.TOO_MANY
    return None


class NameCheck(StrEnum):
    """Where the lookup of a number waiting for its name stands, as the client sees it."""

    CHECKING = "checking"  # asked, no answer yet
    NOT_ANSWERED = "not_answered"  # no answer came; the client may ask again
    LIMIT_REACHED = "limit_reached"  # no answer came, and no lookup is left
    WAITING_FOR_ID = "waiting_for_id"  # a name came; it is compared once the ID is approved


def name_check(
    latest: LookupStatus | None,
    asked_at: datetime | None,
    count: int,
    now: datetime,
    *,
    deciding: bool = False,
) -> NameCheck:
    """Where a number's lookup stands for the client. `deciding`: a name that waited for the ID
    is being compared now, after the client asked again."""
    if latest is LookupStatus.FOUND:
        return NameCheck.CHECKING if deciding else NameCheck.WAITING_FOR_ID
    refused = why_not_again(latest, asked_at, count, now)
    if refused is CheckAgain.IN_PROGRESS:
        return NameCheck.CHECKING
    if refused is CheckAgain.TOO_MANY:
        return NameCheck.LIMIT_REACHED
    return NameCheck.NOT_ANSWERED
