"""When a goal's client is reminded to save, and when they are told it is reached. Pure: no I/O.

A goal with a date has a plan: a saving each month, on the day of the month the goal was set up
(the 28th at the latest, as in `plans.py`). Three days before a saving is due, the client is
reminded, unless they already saved towards the goal since the last one was due. A goal with
no date has no plan, so it has no reminders.

A client who names their payday is nudged on that morning instead, whether or not the goal has
a date: payday is when there is money to save. A day past the end of a shorter month falls on
its last day. The nudge is skipped if they already saved towards the goal in the days just
before it, and a missed morning is caught up for two more days.

A goal is reached when its value, after tax, is at its target. The client is told once.
"""

import calendar
from datetime import date, timedelta

from app.modules.goals.domain.plans import LAST_SAVING_DAY

# How long before a saving is due the client is reminded.
REMIND_BEFORE = timedelta(days=3)


def saving_day(set_up_on: date) -> int:
    """The day of the month a goal's savings are due."""
    return min(set_up_on.day, LAST_SAVING_DAY)


def _month_after(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _month_before(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def next_due(set_up_on: date, today: date) -> date:
    """The first saving date after `today`. Never the day the goal was set up: the client saved
    or chose not to when they made it."""
    day = saving_day(set_up_on)
    due = date(today.year, today.month, day)
    if due <= today:
        due = date(*_month_after(today.year, today.month), day)
    return due


def previous_due(due: date) -> date:
    """The saving date a month before `due`."""
    return date(*_month_before(due.year, due.month), due.day)


def top_up_to_remind(
    *,
    set_up_on: date,
    target_date: date | None,
    today: date,
    reached: bool,
    saved_on: list[date],
) -> date | None:
    """The saving date to remind the client of today, or `None`.

    `saved_on` are the days the client saved towards the goal themselves (not rollovers). A
    reminder is due when the next saving date is at most three days away, it is before the
    goal's date, the goal is not reached, and nothing was saved since the last saving date.
    Each saving date is reminded once, under its own key, so a missed day still catches up.
    """
    if target_date is None or reached:
        return None
    due = next_due(set_up_on, today)
    if due - today > REMIND_BEFORE or due >= target_date:
        return None
    since = max(previous_due(due), set_up_on)
    if any(since < day <= today for day in saved_on):
        return None
    return due


# A payday nudge missed on the day (the worker was down) is still sent this many days after.
PAYDAY_CATCH_UP = timedelta(days=2)
# Savings this close before payday count as the client having saved for it already.
SAVED_FOR_PAYDAY = timedelta(days=3)


def payday_in(year: int, month: int, payday: int) -> date:
    """The payday in that month: the day itself, or the month's last day if it is shorter."""
    return date(year, month, min(payday, calendar.monthrange(year, month)[1]))


def latest_payday(payday: int, today: date) -> date:
    """The most recent payday on or before `today`."""
    this_month = payday_in(today.year, today.month, payday)
    if this_month <= today:
        return this_month
    return payday_in(*_month_before(today.year, today.month), payday)


def payday_to_remind(
    *,
    payday: int,
    target_date: date | None,
    today: date,
    reached: bool,
    saved_on: list[date],
) -> date | None:
    """The payday to nudge the client about today, or `None`.

    Nudged on the payday itself, or up to two days after if a morning was missed, while the goal
    is not reached and its date (if any) is still ahead. Not if the client saved towards the
    goal in the three days before that payday or since. Each payday is keyed on its own, so a
    client is nudged once for it.
    """
    if reached:
        return None
    due = latest_payday(payday, today)
    if today - due > PAYDAY_CATCH_UP:
        return None
    if target_date is not None and today >= target_date:
        return None
    if any(due - SAVED_FOR_PAYDAY <= day <= today for day in saved_on):
        return None
    return due
