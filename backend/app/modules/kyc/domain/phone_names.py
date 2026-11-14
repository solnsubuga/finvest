"""The name the mobile network holds for a client's own phone number, beside their ID. Pure.

When a client hands in their identity check, the worker asks ioTec Verify for the name the
network holds for the client's phone number, with the same lookup that proves a mobile money
payout number (`payouts/domain/name_lookups.py`). The name is compared with the name the client
confirmed from their document by the same rule (`compare_names`).

A different name on the client's current number blocks approval, by the owner's decision: staff
may still reject, or ask the client to change to a number registered in their own name and hand
the check in again, which looks the new number up. It never rejects anyone on its own. Every
other answer is a hint for the person who decides. With approval with no person involved turned
on, a check is approved automatically only when the network's name matches.

A lookup that got no answer is asked again at the next hand-in, up to `MAX_LOOKUPS` for each
number on one check. One that found a name, or found none, is not repeated for that number.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from app.modules.payouts.domain.destinations import NameMatch
from app.modules.payouts.domain.name_lookups import STALE_AFTER, LookupStatus

# Lookups one number may have on an identity check, counting the first. Each costs a fee at ioTec.
MAX_LOOKUPS = 3

# Only a Ugandan number has a Ugandan network to ask.
UGANDA = "+256"


class PhoneName(StrEnum):
    """Where the lookup of the client's phone number stands, as staff see it."""

    CHECKING = "checking"  # asked, no answer yet
    MATCHES = "matches"  # the network's name is the name on the ID
    PARTLY_MATCHES = "partly_matches"  # the same names with one left out, such as a middle name
    DIFFERENT = "different"  # the network holds someone else's name
    NO_NAME = "no_name"  # the network holds no name for the number, or barred it
    NOT_ANSWERED = "not_answered"  # no answer came; asked again at the next hand-in


def msisdn(phone: str) -> str | None:
    """The number as the lookup takes it (12 digits, 256...), or None if it is not Ugandan."""
    if not phone.startswith(UGANDA) or len(phone) != 13 or not phone[1:].isdigit():
        return None
    return phone[1:]


def ask_again(
    latest: LookupStatus | None, asked_at: datetime | None, count: int, now: datetime
) -> bool:
    """Whether to ask for a lookup when the check is handed in.

    Yes when none was asked yet, or the last one is over without an answer (or has waited so
    long that its job has stopped), and fewer than `MAX_LOOKUPS` were asked. Never while one is
    on its way, and never once the network has answered.
    """
    if count >= MAX_LOOKUPS:
        return False
    if latest is None or latest is LookupStatus.UNANSWERED:
        return True
    if latest in (LookupStatus.QUEUED, LookupStatus.SENT):
        return asked_at is not None and now - asked_at >= STALE_AFTER
    return False


def phone_name(status: LookupStatus, match: NameMatch | None) -> PhoneName:
    """What staff see for a lookup."""
    if status in (LookupStatus.QUEUED, LookupStatus.SENT):
        return PhoneName.CHECKING
    if status is LookupStatus.NO_NAME:
        return PhoneName.NO_NAME
    if status is LookupStatus.UNANSWERED or match is None:
        return PhoneName.NOT_ANSWERED
    return {
        NameMatch.EXACT: PhoneName.MATCHES,
        NameMatch.PARTIAL: PhoneName.PARTLY_MATCHES,
        NameMatch.DIFFERENT: PhoneName.DIFFERENT,
    }[match]
