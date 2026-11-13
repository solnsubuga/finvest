"""Claiming a migrated account: the one-time link, the steps and who may take each. Pure.

A client moved from Finvest 1.0 does nothing until invited. The invitation is a personal link
sent by SMS and email. Opening it proves nothing by itself: the client then confirms the phone
on file by code and creates a password, proves who they are with a selfie matched to the ID on
file (or the full ID check, if that ID is missing or out of date), and finally confirms that
the investments shown are right, or says that something is wrong.

Only the hash of a link's token is stored. A link works once, for 72 hours, and a fresh link
cancels the one before it. Nothing here reads a clock: `now` is passed in.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

LINK_LIFETIME = timedelta(hours=72)
# 32 random bytes, base64url without padding, as the service generates them.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
REMINDER_EVERY = timedelta(days=7)
# After this many reminders the client is left for staff to call.
MAX_REMINDERS = 8
MAX_PROBLEM_LENGTH = 1000

ActorKind = Literal["client", "staff", "system"]


class ClaimStatus(StrEnum):
    # Imported, and no link sent yet: there is no phone or email, or the switch has not run.
    NOT_INVITED = "not_invited"
    # A link is out. The client has not used it.
    INVITED = "invited"
    # The phone is confirmed and a password is set. The client can sign in and look.
    SIGNED_IN = "signed_in"
    # A selfie or a full ID check is with the identity checks module.
    IDENTITY_SUBMITTED = "identity_submitted"
    # The person signing in is the person the investments belong to.
    IDENTITY_CONFIRMED = "identity_confirmed"
    # The client said the investments shown are right. Final.
    CONFIRMED = "confirmed"
    # The client said something is wrong. Staff look into it, and the money keeps earning.
    PROBLEM_REPORTED = "problem_reported"


_TRANSITIONS: dict[ActorKind, dict[ClaimStatus, frozenset[ClaimStatus]]] = {
    # Staff send the first link, and a fresh one after checking a changed phone number.
    "staff": {
        ClaimStatus.NOT_INVITED: frozenset({ClaimStatus.INVITED}),
        # Staff close a reported problem. The client then confirms again.
        ClaimStatus.PROBLEM_REPORTED: frozenset({ClaimStatus.IDENTITY_CONFIRMED}),
    },
    "client": {
        ClaimStatus.INVITED: frozenset({ClaimStatus.SIGNED_IN}),
        ClaimStatus.SIGNED_IN: frozenset({ClaimStatus.IDENTITY_SUBMITTED}),
        ClaimStatus.IDENTITY_CONFIRMED: frozenset(
            {ClaimStatus.CONFIRMED, ClaimStatus.PROBLEM_REPORTED}
        ),
    },
    # The switch invites everyone who can be reached. The identity checks module reports the
    # result of a selfie or an ID check: matched, or try again.
    "system": {
        ClaimStatus.NOT_INVITED: frozenset({ClaimStatus.INVITED}),
        ClaimStatus.IDENTITY_SUBMITTED: frozenset(
            {ClaimStatus.IDENTITY_CONFIRMED, ClaimStatus.SIGNED_IN}
        ),
    },
}

# States in which a link can still be sent or used.
LINK_STATES = frozenset({ClaimStatus.NOT_INVITED, ClaimStatus.INVITED})
# The client has an account they can sign in to.
CLAIMED = frozenset(ClaimStatus) - LINK_STATES


def can_move(actor: ActorKind, current: ClaimStatus, target: ClaimStatus) -> bool:
    return target in _TRANSITIONS[actor].get(current, frozenset())


def can_see_investments(status: ClaimStatus) -> bool:
    """A signed-in client sees their investments, read-only, while identity is checked."""
    return status in CLAIMED


def can_transact(status: ClaimStatus) -> bool:
    """Withdrawals, new investments and maturity choices wait until identity is confirmed."""
    return status in {
        ClaimStatus.IDENTITY_CONFIRMED,
        ClaimStatus.CONFIRMED,
        ClaimStatus.PROBLEM_REPORTED,
    }


class IdentityStep(StrEnum):
    SELFIE = "selfie"  # matched to the ID photo carried over from Finvest 1.0
    FULL_ID_CHECK = "full_id_check"  # the same check a new client goes through


def identity_step(*, needs_id_check: bool) -> IdentityStep:
    return IdentityStep.FULL_ID_CHECK if needs_id_check else IdentityStep.SELFIE


def hash_token(token: str) -> str:
    """What is stored for a link. The token itself exists only in the message to the client."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Link:
    token_hash: str
    sent_at: datetime
    used_at: datetime | None = None
    # Set when a fresh link was sent, or when staff changed the phone number.
    cancelled_at: datetime | None = None

    @property
    def expires_at(self) -> datetime:
        return self.sent_at + LINK_LIFETIME


class LinkProblem(StrEnum):
    """Why a link does not open. Staff see which. The client is told only that the link no
    longer works and how to get a new one, so a guessed link learns nothing."""

    NOT_FOUND = "not_found"
    USED = "used"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ALREADY_CLAIMED = "already_claimed"


def link_problem(
    link: Link | None, token: str, status: ClaimStatus, now: datetime
) -> LinkProblem | None:
    """Whether `token` opens the claim. `None` means it does."""
    if (
        link is None
        or _TOKEN.fullmatch(token) is None
        or not hmac.compare_digest(link.token_hash, hash_token(token))
    ):
        return LinkProblem.NOT_FOUND
    if link.used_at is not None:
        return LinkProblem.USED
    if link.cancelled_at is not None:
        return LinkProblem.CANCELLED
    if status in CLAIMED:
        return LinkProblem.ALREADY_CLAIMED
    if now >= link.expires_at:
        return LinkProblem.EXPIRED
    return None


class Channel(StrEnum):
    SMS = "sms"
    EMAIL = "email"


def invite_channels(*, phone: str | None, email: str | None) -> tuple[Channel, ...]:
    """Where a link is sent. Empty when it cannot be sent at all.

    The claim confirms the phone on file by code, so without a phone there is nothing to
    claim with: staff add a number first, after checking who the client is.
    """
    if phone is None:
        return ()
    return (Channel.SMS,) if email is None else (Channel.SMS, Channel.EMAIL)


def reminder_due(
    status: ClaimStatus, *, last_sent_at: datetime, reminders_sent: int, now: datetime
) -> bool:
    """Whether to send a fresh link to a client who has not used the last one."""
    if status is not ClaimStatus.INVITED or reminders_sent >= MAX_REMINDERS:
        return False
    return now - last_sent_at >= REMINDER_EVERY


def mask_phone(phone: str) -> str:
    """`+256772123456` as `+256 772 *** 456`: enough for the owner to recognise it."""
    digits = phone.removeprefix("+")
    if not phone.startswith("+") or not digits.isdigit() or len(digits) < 9:
        raise ValueError("phone")
    middle = digits[3:-3]
    return f"+{digits[:3]} {middle[:3]} {'*' * len(middle[3:])} {digits[-3:]}"


def problem_text(text: str) -> str:
    """What the client wrote when they said something is wrong, tidied for the staff queue."""
    cleaned = " ".join(text.split())
    if not cleaned or len(cleaned) > MAX_PROBLEM_LENGTH:
        raise ValueError("problem_text")
    return cleaned


@dataclass(frozen=True, slots=True)
class Progress:
    """The four figures on the console's migration screen."""

    invited: int
    claimed_and_confirmed: int
    said_something_is_wrong: int
    not_yet_claimed: int
    cannot_be_invited: int


def progress(counts: dict[ClaimStatus, int]) -> Progress:
    def count(*statuses: ClaimStatus) -> int:
        return sum(counts.get(status, 0) for status in statuses)

    not_invited = count(ClaimStatus.NOT_INVITED)
    return Progress(
        invited=sum(counts.values()) - not_invited,
        claimed_and_confirmed=count(ClaimStatus.CONFIRMED),
        said_something_is_wrong=count(ClaimStatus.PROBLEM_REPORTED),
        # Invited and not finished: no sign-in yet, or part of the way through.
        not_yet_claimed=count(
            ClaimStatus.INVITED,
            ClaimStatus.SIGNED_IN,
            ClaimStatus.IDENTITY_SUBMITTED,
            ClaimStatus.IDENTITY_CONFIRMED,
        ),
        cannot_be_invited=not_invited,
    )
