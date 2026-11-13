"""Calendar months for insights."""

from datetime import date, timedelta
from itertools import pairwise

import pytest

from app.modules.insights.domain.months import MAX_MONTHS, month_end, month_start, months_ending


def test_the_window_ends_with_the_month_today_is_in() -> None:
    assert months_ending(date(2026, 11, 13), 5) == [
        date(2026, 7, 1),
        date(2026, 8, 1),
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
    ]


def test_the_window_crosses_the_new_year() -> None:
    assert months_ending(date(2027, 2, 28), 4) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]


@pytest.mark.parametrize("count", [0, MAX_MONTHS + 1, -1])
def test_the_window_is_one_to_twenty_four_months(count: int) -> None:
    with pytest.raises(ValueError):
        months_ending(date(2026, 11, 13), count)


def test_every_day_falls_in_exactly_one_month_of_its_window() -> None:
    day = date(2024, 1, 1)
    while day < date(2028, 1, 1):
        window = months_ending(day, MAX_MONTHS)
        assert len(window) == MAX_MONTHS == len(set(window))
        assert window[-1] == month_start(day)
        assert window[-1] <= day <= month_end(window[-1])
        for earlier, later in pairwise(window):
            assert month_end(earlier) + timedelta(days=1) == later
        day += timedelta(days=1)


def test_month_end_knows_leap_years() -> None:
    assert month_end(date(2028, 2, 1)) == date(2028, 2, 29)
    assert month_end(date(2027, 2, 1)) == date(2027, 2, 28)
    assert month_end(date(2026, 12, 1)) == date(2026, 12, 31)
