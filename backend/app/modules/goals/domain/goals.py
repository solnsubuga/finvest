"""A goal: a target amount a client saves towards, by a date or with none. Pure: no I/O.

There is no wallet, so a goal holds no money of its own. It is a target attached to the
client's investments. Each saving towards it is a new investment, paid for like any other, and
the goal's value is the value of those investments. Nothing waits in a pot.

    active ──► closed

* `active`: savings can be added and the goal shows its progress.
* `closed`: the client stopped it. Final. Its investments carry on as ordinary investments,
  because they are the client's own and closing a goal moves no money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from app.shared.money import Currency, Money, Rounding

MAX_NAME_LENGTH = 60
# Goals one client may have open at once. Enough for real life, small enough to show on a page.
MAX_ACTIVE_GOALS = 20
# The furthest a goal's date may be: the longest horizon a plan is worked out for.
MAX_HORIZON = timedelta(days=366 * 10)
# Rounded up to a whole thousand shillings, so a plan reads as an amount people actually send.
SAVING_STEP_UGX = 1_000


class GoalStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class GoalCategory(StrEnum):
    """What the goal is for. It only picks the picture and the words in the app."""

    SCHOOL_FEES = "school_fees"
    EMERGENCY = "emergency"
    LAND_OR_HOME = "land_or_home"
    BUSINESS = "business"
    WEDDING = "wedding"
    RETIREMENT = "retirement"
    OTHER = "other"


class GoalRefusal(StrEnum):
    NAME_EMPTY = "name_empty"
    NAME_TOO_LONG = "name_too_long"
    TARGET_NOT_POSITIVE = "target_not_positive"
    # The date must be after today, and no further than the longest horizon.
    DATE_NOT_IN_FUTURE = "date_not_in_future"
    DATE_TOO_FAR = "date_too_far"


class Standing(StrEnum):
    """How a goal is doing, as the app labels it."""

    # Value at or above the target.
    REACHED = "reached"
    # At or ahead of a straight line from the day it was set up to the target date.
    ON_TRACK = "on_track"
    BEHIND = "behind"
    # No date, so there is no line to be ahead of or behind.
    NO_DATE = "no_date"


@dataclass(frozen=True, slots=True)
class Progress:
    """What a goal is worth today, from its investments. Every figure is after tax."""

    # Started investments: what was paid in plus interest earned after tax.
    value: Money
    # Of that value, the interest.
    interest: Money
    # Savings the client confirmed and has not paid for yet. Not counted in the value.
    awaiting_payment: Money
    # Whole percent of the target reached, 0 to 100.
    percent: int
    standing: Standing


def clean_name(name: str) -> str:
    return " ".join(name.split())


def check_goal(
    name: str, target: Money, target_date: date | None, today: date
) -> GoalRefusal | None:
    """Why a goal as set up or changed cannot be saved, or `None` when it can."""
    cleaned = clean_name(name)
    if not cleaned:
        return GoalRefusal.NAME_EMPTY
    if len(cleaned) > MAX_NAME_LENGTH:
        return GoalRefusal.NAME_TOO_LONG
    if not target.is_positive:
        return GoalRefusal.TARGET_NOT_POSITIVE
    if target_date is not None:
        if target_date <= today:
            return GoalRefusal.DATE_NOT_IN_FUTURE
        if target_date - today > MAX_HORIZON:
            return GoalRefusal.DATE_TOO_FAR
    return None


def progress(
    *,
    target: Money,
    value: Money,
    interest: Money,
    awaiting_payment: Money,
    set_up_on: date,
    target_date: date | None,
    today: date,
) -> Progress:
    """Where a goal stands today.

    On track means the value is at least the share of the target that a straight line from the
    day the goal was set up to its date would have reached by today, rounded up. It is a simple
    measure on purpose: a client can check it in their head.
    """
    percent = min(100, value.minor * 100 // target.minor) if target.is_positive else 0
    if value >= target:
        standing = Standing.REACHED
    elif target_date is None:
        standing = Standing.NO_DATE
    else:
        span = max((target_date - set_up_on).days, 1)
        elapsed = min(max((today - set_up_on).days, 0), span)
        expected = target.mul_ratio(elapsed, span, Rounding.UP)
        standing = Standing.ON_TRACK if value >= expected else Standing.BEHIND
    return Progress(
        value=value,
        interest=interest,
        awaiting_payment=awaiting_payment,
        percent=percent,
        standing=standing,
    )


def saving_step(currency: Currency) -> Money:
    """The unit a suggested saving is rounded up to."""
    if currency is Currency.UGX:
        return Money(SAVING_STEP_UGX, currency)
    # One dollar.
    return Money(100, currency)
