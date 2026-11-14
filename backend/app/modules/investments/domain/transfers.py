"""Sending an investment to another client. Pure: no I/O.

A client sends part or all of a running investment to another approved client. Nothing is
cashed out and no money leaves Finvest: the ledger moves the holding from one client to the
other. The investment the client sent from closes as `transferred`, and its value at that
moment, principal plus the interest earned after tax so far, is split between two new
investments in the same product on the same rate, tax and maturity date:

* the recipient's, for exactly the amount sent, and
* the sender's, for whatever is left, unless the whole value was sent.

Both start on the day of the transfer and earn from the next day, so no day is paid twice and
none is missed: the closed investment has earned the transfer day already, or is paid it first.
The interest the sender had earned becomes part of what they keep, as at a rollover. Tax on it
was held when it was earned and stays held.

    waiting ──► done
       │  └───► failed
    held ──► release_proposed ──► waiting
       │            │
       └────────────┴──► declined, or cancelled by the sender

A transfer is `held` for two members of staff when it, with everything else the client sent
that day, is above the size at which a payout is held for review, or when its currency has no
such size set. See docs/investing.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.shared.money import Money

# The longest message a sender may add for the recipient.
MAX_MESSAGE_LENGTH = 140
# The longest few words a sender gives when the reason is `other`.
MAX_PURPOSE_NOTE_LENGTH = 140


class TransferPurpose(StrEnum):
    """Why the sender sends it, picked from a short list, kept for anti money laundering."""

    FAMILY_SUPPORT = "family_support"
    GIFT = "gift"
    LOAN_REPAYMENT = "loan_repayment"
    BUSINESS = "business"
    # With a few words of the sender's own.
    OTHER = "other"


class TransferStatus(StrEnum):
    # Saved and queued: the worker moves the money.
    WAITING = "waiting"
    # Large: waiting for two members of staff.
    HELD = "held"
    # One member of staff proposed letting it through. A different one decides.
    RELEASE_PROPOSED = "release_proposed"
    DONE = "done"
    # The worker found it could not be done, for example the investment matured first.
    FAILED = "failed"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class HeldFor(StrEnum):
    """Why a transfer waits for two members of staff. Staff see it; the client never does."""

    SIZE = "size"
    # The sender is on a screening hold: a screen under way, an open case or a sanctions match.
    SCREENING = "screening"


# A transfer the worker could not make because a client is held by screening. Deliberately the
# same words to the sender as any other change, so nobody is tipped off.
NOT_POSSIBLE = "not_possible"


OPEN = frozenset({TransferStatus.WAITING, TransferStatus.HELD, TransferStatus.RELEASE_PROPOSED})
# What counts towards the day's total for the review threshold: anything not refused.
COUNTED = OPEN | {TransferStatus.DONE}


class TransferRefusal(StrEnum):
    NOT_RUNNING = "investment_not_running"
    WITHDRAWAL_REQUESTED = "withdrawal_requested"
    # On its maturity date the maturity run rolls it over or pays it out.
    MATURES_TODAY = "matures_today"
    TO_YOURSELF = "to_yourself"
    CURRENCY = "currency_not_the_investments"
    BELOW_MINIMUM = "below_product_minimum"
    MORE_THAN_VALUE = "more_than_investment_value"
    # What the sender would keep is above nothing but below the product's minimum.
    REMAINDER_BELOW_MINIMUM = "remainder_below_product_minimum"


@dataclass(frozen=True, slots=True)
class Split:
    sent: Money
    # None when the whole value is sent.
    kept: Money | None


def check_transfer(
    *,
    running: bool,
    withdrawal_requested: bool,
    maturity: date | None,
    today: date,
    value: Money,
    amount: Money,
    minimum: Money,
) -> TransferRefusal | None:
    """Why a client may not send `amount` from an investment worth `value` today, or None.

    The recipient gets at least the product's minimum, and the sender keeps either nothing or
    at least the minimum, so no investment is ever smaller than the product allows.
    """
    if not running:
        return TransferRefusal.NOT_RUNNING
    if withdrawal_requested:
        return TransferRefusal.WITHDRAWAL_REQUESTED
    if maturity is not None and today >= maturity:
        return TransferRefusal.MATURES_TODAY
    if amount.currency is not value.currency:
        return TransferRefusal.CURRENCY
    if amount < minimum:
        return TransferRefusal.BELOW_MINIMUM
    if amount > value:
        return TransferRefusal.MORE_THAN_VALUE
    kept = value - amount
    if kept.is_positive and kept < minimum:
        return TransferRefusal.REMAINDER_BELOW_MINIMUM
    return None


def split(value: Money, amount: Money, *, whole: bool) -> Split:
    """How the value of the investment is shared when the worker moves it.

    `whole` sends everything, including interest earned since the request. Otherwise the
    recipient gets exactly `amount` and the sender keeps the rest, which only grows after the
    request, as interest is added.
    """
    if whole:
        return Split(sent=value, kept=None)
    if amount > value:
        raise ValueError("A transfer cannot send more than the investment is worth")
    kept = value - amount
    return Split(sent=amount, kept=kept if kept.is_positive else None)


def remaining_term(maturity: date | None, today: date) -> int | None:
    """Days from the transfer to the maturity date: the term of the two new investments."""
    if maturity is None:
        return None
    days = (maturity - today).days
    if days < 1:
        raise ValueError("A transfer is made before the maturity date")
    return days


def needs_review(amount: Money, sent_today: Money, review_above: Money | None) -> bool:
    """Whether two members of staff must let the transfer through.

    Counted with everything else the client sent that day, so splitting one large transfer
    into several small ones does not avoid the review. With no threshold for the currency,
    every transfer is reviewed.
    """
    if review_above is None:
        return True
    return amount + sent_today > review_above


def display_name(surname: str, given_names: str) -> str:
    """How one client is shown to another: the first given name and the surname's initial.

    Enough to confirm the right person, without telling a stranger a full name from a phone
    number. "KATO", "Brian Ssali" is "Brian K.".
    """
    first = given_names.strip().split()[0] if given_names.strip() else ""
    initial = surname.strip()[:1].upper()
    first = first[:1].upper() + first[1:].lower() if first else ""
    if first and initial:
        return f"{first} {initial}."
    return first or (f"{initial}." if initial else "")
