"""When a goal's client is reminded to save, and when they are told it is reached. Pure: no I/O.

A goal with a date has a plan: a saving each month, on the day of the month the goal was set up
(the 28th at the latest, as in `plans.py`). Three days before a saving is due, the client is
reminded, unless they already saved towards the goal since the last one was due. A goal with
no date has no plan, so it has no reminders.

A goal is reached when its value, after tax, is at its target. The client is told once.
"""

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
