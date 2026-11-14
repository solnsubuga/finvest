"""When a goal's client is reminded to save. Every case of the rule in
`app/modules/goals/domain/reminders.py`."""

from datetime import date

import pytest

from app.modules.goals.domain.reminders import next_due, previous_due, saving_day, top_up_to_remind

SET_UP = date(2026, 11, 14)
GOAL_DATE = date(2027, 11, 14)


def remind(today: date, **changes: object) -> date | None:
    values: dict[str, object] = {
        "set_up_on": SET_UP,
        "target_date": GOAL_DATE,
        "today": today,
        "reached": False,
        "saved_on": [SET_UP],
    }
    return top_up_to_remind(**{**values, **changes})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("set_up", "day"), [(date(2026, 1, 1), 1), (date(2026, 1, 28), 28), (date(2026, 1, 31), 28)]
)
def test_savings_fall_on_the_day_set_up_but_never_after_the_28th(set_up: date, day: int) -> None:
    assert saving_day(set_up) == day


def test_the_next_saving_is_after_today_and_never_the_day_set_up() -> None:
    assert next_due(SET_UP, SET_UP) == date(2026, 12, 14)
    assert next_due(SET_UP, date(2026, 12, 13)) == date(2026, 12, 14)
    assert next_due(SET_UP, date(2026, 12, 14)) == date(2027, 1, 14)
    assert next_due(date(2026, 1, 31), date(2026, 2, 1)) == date(2026, 2, 28)
    assert next_due(SET_UP, date(2026, 12, 20)) == date(2027, 1, 14)
    assert previous_due(date(2027, 1, 14)) == date(2026, 12, 14)


@pytest.mark.parametrize(
    ("today", "due"),
    [
        (date(2026, 12, 10), None),
        (date(2026, 12, 11), date(2026, 12, 14)),
        (date(2026, 12, 12), date(2026, 12, 14)),
        (date(2026, 12, 13), date(2026, 12, 14)),
        # On the day itself the next reminder is next month's, so none today.
        (date(2026, 12, 14), None),
    ],
)
def test_a_reminder_comes_in_the_three_days_before_a_saving(today: date, due: date | None) -> None:
    assert remind(today) == due


def test_a_saving_on_the_day_set_up_does_not_cover_the_next_month() -> None:
    assert remind(date(2026, 12, 11), saved_on=[SET_UP]) == date(2026, 12, 14)
    assert remind(date(2026, 12, 11), saved_on=[]) == date(2026, 12, 14)


@pytest.mark.parametrize("saved", [date(2026, 11, 15), date(2026, 12, 1), date(2026, 12, 11)])
def test_a_saving_since_the_last_due_date_means_no_reminder(saved: date) -> None:
    assert remind(date(2026, 12, 11), saved_on=[SET_UP, saved]) is None


def test_a_saving_before_the_last_due_date_does_not_count() -> None:
    assert remind(date(2027, 1, 11), saved_on=[date(2026, 12, 14)]) == date(2027, 1, 14)
    assert remind(date(2027, 1, 11), saved_on=[date(2026, 12, 15)]) is None


def test_no_reminder_without_a_date_once_reached_or_on_or_after_the_goals_date() -> None:
    assert remind(date(2026, 12, 11), target_date=None) is None
    assert remind(date(2026, 12, 11), reached=True) is None
    assert remind(date(2026, 12, 11), target_date=date(2026, 12, 14)) is None
    assert remind(date(2026, 12, 11), target_date=date(2026, 12, 15)) == date(2026, 12, 14)


def test_a_year_end_saving_is_reminded_in_december() -> None:
    set_up = date(2026, 1, 3)
    assert remind(date(2026, 12, 31), set_up_on=set_up, saved_on=[]) == date(2027, 1, 3)
