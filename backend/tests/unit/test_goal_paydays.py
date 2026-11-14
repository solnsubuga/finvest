"""The payday nudge: every case of `payday_to_remind` in `app/modules/goals/domain/reminders.py`."""

from datetime import date

import pytest

from app.modules.goals.domain.reminders import latest_payday, payday_in, payday_to_remind

GOAL_DATE = date(2027, 11, 14)


def nudge(today: date, **changes: object) -> date | None:
    values: dict[str, object] = {
        "payday": 25,
        "target_date": GOAL_DATE,
        "today": today,
        "reached": False,
        "saved_on": [date(2026, 11, 1)],
    }
    return payday_to_remind(**{**values, **changes})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("year", "month", "payday", "day"),
    [(2027, 2, 31, 28), (2028, 2, 30, 29), (2026, 4, 31, 30), (2026, 12, 31, 31), (2026, 6, 1, 1)],
)
def test_a_payday_past_a_short_months_end_falls_on_its_last_day(
    year: int, month: int, payday: int, day: int
) -> None:
    assert payday_in(year, month, payday) == date(year, month, day)


def test_the_latest_payday_is_this_months_once_it_has_come() -> None:
    assert latest_payday(25, date(2026, 11, 25)) == date(2026, 11, 25)
    assert latest_payday(25, date(2026, 11, 24)) == date(2026, 10, 25)
    assert latest_payday(31, date(2027, 3, 1)) == date(2027, 2, 28)
    assert latest_payday(15, date(2027, 1, 3)) == date(2026, 12, 15)


def test_the_client_is_nudged_on_payday_and_for_two_mornings_after() -> None:
    assert nudge(date(2026, 11, 24)) is None
    assert nudge(date(2026, 11, 25)) == date(2026, 11, 25)
    assert nudge(date(2026, 11, 27)) == date(2026, 11, 25)
    assert nudge(date(2026, 11, 28)) is None


def test_no_nudge_once_saved_for_this_payday() -> None:
    assert nudge(date(2026, 11, 25), saved_on=[date(2026, 11, 22)]) is None
    assert nudge(date(2026, 11, 26), saved_on=[date(2026, 11, 26)]) is None
    assert nudge(date(2026, 11, 25), saved_on=[date(2026, 11, 21)]) == date(2026, 11, 25)


def test_no_nudge_once_reached_or_on_or_after_the_goals_date() -> None:
    assert nudge(date(2026, 11, 25), reached=True) is None
    assert nudge(date(2026, 11, 25), target_date=date(2026, 11, 25)) is None
    assert nudge(date(2026, 11, 26), target_date=date(2026, 11, 26)) is None


def test_a_goal_with_no_date_is_still_nudged() -> None:
    assert nudge(date(2026, 11, 25), target_date=None) == date(2026, 11, 25)
