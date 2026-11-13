"""Automatic investing schedules and runs: when a plan is due, and what a late run does."""

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest

from app.modules.autoinvest.domain.plans import (
    LATE_TOLERANCE,
    MAX_ATTEMPTS,
    MAX_RUN_AMOUNT,
    PLAN_MOVES,
    RUN_MOVES,
    DueRun,
    Frequency,
    PlanRefusal,
    PlanStatus,
    RunStatus,
    Schedule,
    after_failure,
    can_move_plan,
    check_amount,
    check_day,
    due_run,
    first_run,
    may_prompt_at,
)
from app.shared.money import Currency, Money

WEEKLY, MONTHLY = Frequency.WEEKLY, Frequency.MONTHLY
# 13 November 2026 is a Friday.
FRIDAY = date(2026, 11, 13)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(count)]


# Days -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frequency", "day", "ok"),
    [
        (WEEKLY, 1, True),
        (WEEKLY, 7, True),
        (WEEKLY, 0, False),
        (WEEKLY, 8, False),
        (MONTHLY, 1, True),
        (MONTHLY, 31, True),
        (MONTHLY, 0, False),
        (MONTHLY, 32, False),
    ],
)
def test_the_day_must_exist_in_the_frequency(frequency: Frequency, day: int, ok: bool) -> None:
    assert (check_day(frequency, day) is None) is ok
    if not ok:
        with pytest.raises(ValueError, match="invalid_day"):
            Schedule(frequency, day)


@pytest.mark.parametrize("day", [True, 1.0, "1"])
def test_a_day_is_a_whole_number(day: object) -> None:
    assert check_day(MONTHLY, day) is PlanRefusal.INVALID_DAY  # type: ignore[arg-type]


def test_a_weekly_plan_runs_on_its_weekday_every_week() -> None:
    monday = Schedule(WEEKLY, 1)
    year = days(date(2026, 1, 1), 730)
    runs = [day for day in year if monday.falls_on(day)]
    assert all(day.isoweekday() == 1 for day in runs)
    assert all(later - earlier == timedelta(days=7) for earlier, later in pairwise(runs))
    assert len(runs) == 104


@pytest.mark.parametrize("day", range(1, 32))
def test_a_monthly_plan_runs_once_in_every_month(day: int) -> None:
    schedule = Schedule(MONTHLY, day)
    runs = [d for d in days(date(2026, 1, 1), 1096) if schedule.falls_on(d)]
    assert len(runs) == 36
    assert len({(d.year, d.month) for d in runs}) == 36


def test_a_day_a_month_lacks_falls_on_its_last_day() -> None:
    thirty_first = Schedule(MONTHLY, 31)
    assert thirty_first.falls_on(date(2026, 4, 30))
    assert thirty_first.falls_on(date(2027, 2, 28))
    assert thirty_first.falls_on(date(2028, 2, 29))
    assert not thirty_first.falls_on(date(2028, 2, 28))
    assert Schedule(MONTHLY, 30).falls_on(date(2027, 2, 28))


@pytest.mark.parametrize(
    "schedule",
    [Schedule(WEEKLY, d) for d in range(1, 8)] + [Schedule(MONTHLY, d) for d in range(1, 32)],
)
def test_next_after_is_the_first_later_day_the_plan_falls_on(schedule: Schedule) -> None:
    for day in days(date(2026, 1, 1), 800):
        expected = next(d for d in days(day + timedelta(days=1), 40) if schedule.falls_on(d))
        assert schedule.next_after(day) == expected
        first = schedule.first_on_or_after(day)
        assert first == (day if schedule.falls_on(day) else expected)


def test_a_new_plan_never_runs_on_the_day_it_is_set_up() -> None:
    assert first_run(Schedule(WEEKLY, 5), FRIDAY) == date(2026, 11, 20)
    assert first_run(Schedule(WEEKLY, 6), FRIDAY) == date(2026, 11, 14)
    assert first_run(Schedule(MONTHLY, 13), FRIDAY) == date(2026, 12, 13)
    assert first_run(Schedule(MONTHLY, 28), FRIDAY) == date(2026, 11, 28)
    assert first_run(Schedule(MONTHLY, 31), date(2026, 12, 31)) == date(2027, 1, 31)


# Amounts ----------------------------------------------------------------------------------------


def test_a_plan_collects_shillings_up_to_the_most_one_prompt_may_ask_for() -> None:
    assert check_amount(ugx(100_000)) is None
    assert check_amount(MAX_RUN_AMOUNT) is None
    assert check_amount(ugx(MAX_RUN_AMOUNT.minor + 1)) is PlanRefusal.AMOUNT_TOO_LARGE
    assert check_amount(ugx(0)) is PlanRefusal.INVALID_AMOUNT
    assert check_amount(ugx(-1)) is PlanRefusal.INVALID_AMOUNT
    assert check_amount(Money(50_000, Currency.USD)) is PlanRefusal.UNSUPPORTED_CURRENCY


# The daily run ----------------------------------------------------------------------------------


def test_a_plan_not_yet_due_does_nothing() -> None:
    assert due_run(Schedule(WEEKLY, 1), date(2026, 11, 16), FRIDAY) is None


def test_a_plan_due_today_collects_today_and_moves_to_its_next_day() -> None:
    assert due_run(Schedule(WEEKLY, 5), FRIDAY, FRIDAY) == DueRun(
        collect=FRIDAY, missed=None, next_run_on=date(2026, 11, 20)
    )
    assert due_run(Schedule(MONTHLY, 13), FRIDAY, FRIDAY) == DueRun(
        collect=FRIDAY, missed=None, next_run_on=date(2026, 12, 13)
    )


def test_a_run_a_day_late_is_still_collected_for_its_own_day() -> None:
    thursday = FRIDAY - LATE_TOLERANCE
    assert due_run(Schedule(WEEKLY, 4), thursday, FRIDAY) == DueRun(
        collect=thursday, missed=None, next_run_on=date(2026, 11, 19)
    )


def test_a_run_more_than_a_day_late_is_missed_and_never_collected_late() -> None:
    monday = date(2026, 11, 9)
    assert due_run(Schedule(WEEKLY, 1), monday, FRIDAY) == DueRun(
        collect=None, missed=monday, next_run_on=date(2026, 11, 16)
    )
    # Down for months: only the one due day is recorded, and the plan moves past today.
    assert due_run(Schedule(MONTHLY, 1), date(2026, 6, 1), FRIDAY) == DueRun(
        collect=None, missed=date(2026, 6, 1), next_run_on=date(2026, 12, 1)
    )


@pytest.mark.parametrize(
    "schedule",
    [Schedule(WEEKLY, d) for d in range(1, 8)] + [Schedule(MONTHLY, d) for d in (1, 15, 28, 31)],
)
def test_every_run_moves_the_plan_past_today(schedule: Schedule) -> None:
    for today in days(date(2026, 1, 1), 400):
        for lateness in range(0, 40):
            next_run_on = today - timedelta(days=lateness)
            decision = due_run(schedule, next_run_on, today)
            assert decision is not None
            assert decision.next_run_on > today
            assert schedule.falls_on(decision.next_run_on)
            assert (decision.collect is None) != (decision.missed is None)


# Prompts ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour_utc", "minute", "allowed"),
    [(4, 59, False), (5, 0, True), (12, 0, True), (16, 59, True), (17, 0, False), (23, 0, False)],
)
def test_prompts_reach_phones_only_by_day_in_kampala(
    hour_utc: int, minute: int, allowed: bool
) -> None:
    assert may_prompt_at(datetime(2026, 11, 13, hour_utc, minute, tzinfo=UTC)) is allowed


def test_a_moment_without_a_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        may_prompt_at(datetime(2026, 11, 13, 9))


# Moves ------------------------------------------------------------------------------------------


def test_a_plan_pauses_resumes_and_stops_and_a_stopped_plan_is_final() -> None:
    assert can_move_plan(PlanStatus.ACTIVE, PlanStatus.PAUSED)
    assert can_move_plan(PlanStatus.PAUSED, PlanStatus.ACTIVE)
    assert can_move_plan(PlanStatus.ACTIVE, PlanStatus.STOPPED)
    assert can_move_plan(PlanStatus.PAUSED, PlanStatus.STOPPED)
    assert PLAN_MOVES[PlanStatus.STOPPED] == frozenset()
    for status in PlanStatus:
        assert not can_move_plan(status, status)


def test_only_a_failure_reopens_a_run_and_paid_and_missed_are_final() -> None:
    assert RUN_MOVES[RunStatus.PAID] == frozenset()
    assert RUN_MOVES[RunStatus.MISSED] == frozenset()
    assert RUN_MOVES[RunStatus.FAILED] == frozenset({RunStatus.DUE})
    assert RunStatus.DUE not in RUN_MOVES[RunStatus.REQUESTED]


def test_a_failed_payment_is_tried_once_more_the_next_day() -> None:
    assert MAX_ATTEMPTS == 2
    assert after_failure(1, FRIDAY) == date(2026, 11, 14)
    assert after_failure(2, FRIDAY) is None
