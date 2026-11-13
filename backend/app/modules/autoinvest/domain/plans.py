"""A plan to invest the same amount on a schedule, and the runs it makes. Pure: no I/O.

There is no wallet, so a plan never draws on a balance. On each due day the worker saves an
investment awaiting payment and asks the client's mobile money for exactly that amount. The
client approves the prompt on their phone, as for any other payment.

    active ◄──► paused
      │           │
      └──► stopped ◄┘

* `active`: runs on each due day.
* `paused`: makes no runs. Resuming picks the next due day from the day it resumes. Days that
  passed while paused are not made up.
* `stopped`: final. A stopped plan is kept as a record and never runs again.

A run is one due day of one plan:

    due ──► requested ──► paid
     │          │
     │          └──► failed ──► due (once, the next day)
     └──► missed

* `due`: saved, and a job asks for the investment and the payment.
* `requested`: the investment is saved and the prompt is on its way to the phone.
* `paid`: the payment was confirmed, so the investment has started. Final.
* `failed`: the payment failed or was not approved. A first failure is tried once more the
  next day. A second is final.
* `missed`: the day passed without a request, because the plan was paused or stopped first,
  or the system was down for longer than a day. Final. Nothing is ever collected late.

A plan has at most one run open at a time and at most one run per due day, so nothing is ever
collected twice.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from app.shared.business_day import KAMPALA
from app.shared.money import Currency, Money

# Plans one client may keep, active and paused together.
MAX_OPEN_PLANS = 10
# The most one run may ask for. A mobile money prompt for more is likely to be refused by the
# network, and the client can invest a larger amount by bank transfer by hand.
MAX_RUN_AMOUNT = Money(5_000_000, Currency.UGX)
# A failed payment is tried once more, the day after.
MAX_ATTEMPTS = 2
# A run that is overdue by more than this when the system comes back is missed, not collected.
LATE_TOLERANCE = timedelta(days=1)
# Runs are opened, and so prompts sent, only in these Kampala hours, so no phone rings at night.
PROMPT_FROM = time(8)
PROMPT_UNTIL = time(20)


class Frequency(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class PlanStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


class RunStatus(StrEnum):
    DUE = "due"
    REQUESTED = "requested"
    PAID = "paid"
    FAILED = "failed"
    MISSED = "missed"


OPEN_RUNS = frozenset({RunStatus.DUE, RunStatus.REQUESTED})
FINAL_RUNS = frozenset({RunStatus.PAID, RunStatus.MISSED})

PLAN_MOVES: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.ACTIVE: frozenset({PlanStatus.PAUSED, PlanStatus.STOPPED}),
    PlanStatus.PAUSED: frozenset({PlanStatus.ACTIVE, PlanStatus.STOPPED}),
    PlanStatus.STOPPED: frozenset(),
}

RUN_MOVES: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.DUE: frozenset({RunStatus.REQUESTED, RunStatus.FAILED, RunStatus.MISSED}),
    RunStatus.REQUESTED: frozenset({RunStatus.PAID, RunStatus.FAILED}),
    # A first failure goes back to due for the retry. A second stays failed.
    RunStatus.FAILED: frozenset({RunStatus.DUE}),
    RunStatus.PAID: frozenset(),
    RunStatus.MISSED: frozenset(),
}


class PlanRefusal(StrEnum):
    INVALID_DAY = "invalid_day"
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    AMOUNT_TOO_LARGE = "amount_too_large"
    INVALID_AMOUNT = "invalid_amount"


@dataclass(frozen=True, slots=True)
class Schedule:
    """When a plan runs.

    Weekly: `day` is the weekday, 1 for Monday to 7 for Sunday. Monthly: `day` is the day of
    the month, 1 to 31. In a month without that day the run is on its last day, so a plan for
    the 31st runs on 30 April and 28 February.
    """

    frequency: Frequency
    day: int

    def __post_init__(self) -> None:
        if refusal := check_day(self.frequency, self.day):
            raise ValueError(refusal.value)

    def falls_on(self, day: date) -> bool:
        if self.frequency is Frequency.WEEKLY:
            return day.isoweekday() == self.day
        return day.day == _day_in_month(day.year, day.month, self.day)

    def first_on_or_after(self, day: date) -> date:
        """The first due day on or after `day`."""
        if self.frequency is Frequency.WEEKLY:
            return day + timedelta(days=(self.day - day.isoweekday()) % 7)
        this_month = date(day.year, day.month, _day_in_month(day.year, day.month, self.day))
        if this_month >= day:
            return this_month
        year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
        return date(year, month, _day_in_month(year, month, self.day))

    def next_after(self, day: date) -> date:
        """The first due day strictly after `day`."""
        return self.first_on_or_after(day + timedelta(days=1))


def check_day(frequency: Frequency, day: int) -> PlanRefusal | None:
    if type(day) is not int:
        return PlanRefusal.INVALID_DAY
    top = 7 if frequency is Frequency.WEEKLY else 31
    return None if 1 <= day <= top else PlanRefusal.INVALID_DAY


def check_amount(amount: Money) -> PlanRefusal | None:
    """What a plan may ask for in one run. The product's own minimum is checked separately."""
    if amount.currency is not Currency.UGX:
        # Mobile money collects shillings only.
        return PlanRefusal.UNSUPPORTED_CURRENCY
    if not amount.is_positive:
        return PlanRefusal.INVALID_AMOUNT
    if amount > MAX_RUN_AMOUNT:
        return PlanRefusal.AMOUNT_TOO_LARGE
    return None


def first_run(schedule: Schedule, today: date) -> date:
    """The first due day of a plan set up or resumed today.

    Never today itself: a prompt arriving minutes after setting a plan up would surprise the
    client, who can invest today by hand if they want to.
    """
    return schedule.next_after(today)


@dataclass(frozen=True, slots=True)
class DueRun:
    """What the daily run does for one plan whose next due day has come."""

    # The due day to collect for now, or None if the plan's due day was missed.
    collect: date | None
    # A due day that passed without collection, recorded as missed.
    missed: date | None
    # The plan's next due day after this run.
    next_run_on: date


def due_run(schedule: Schedule, next_run_on: date, today: date) -> DueRun | None:
    """Decide what today's run does for a plan due on `next_run_on`.

    None if the plan is not due yet. A due day today or yesterday is collected today: the daily
    run may have fired late or been down overnight. An older one is missed, so the system never
    collects a week's or a month's saving late, and the plan moves on to its next due day.
    """
    if next_run_on > today:
        return None
    following = schedule.next_after(today if next_run_on < today else next_run_on)
    if today - next_run_on <= LATE_TOLERANCE:
        return DueRun(collect=next_run_on, missed=None, next_run_on=following)
    return DueRun(collect=None, missed=next_run_on, next_run_on=following)


def may_prompt_at(moment: datetime) -> bool:
    """Whether a payment prompt may reach a client's phone at this moment."""
    if moment.utcoffset() is None:
        raise ValueError("Times must be timezone-aware")
    return PROMPT_FROM <= moment.astimezone(KAMPALA).time() < PROMPT_UNTIL


def can_move_plan(current: PlanStatus, to: PlanStatus) -> bool:
    return to in PLAN_MOVES[current]


def after_failure(attempt: int, today: date) -> date | None:
    """When a failed run is tried again, or None if it has had its last try."""
    return today + timedelta(days=1) if attempt < MAX_ATTEMPTS else None


def _day_in_month(year: int, month: int, day: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])
