from datetime import UTC, date, datetime

import pytest

from app.shared.business_day import business_date


@pytest.mark.parametrize(
    ("moment", "day"),
    [
        (datetime(2026, 11, 13, 20, 59, 59, tzinfo=UTC), date(2026, 11, 13)),
        (datetime(2026, 11, 13, 21, 0, 0, tzinfo=UTC), date(2026, 11, 14)),  # midnight in Kampala
        (datetime(2026, 12, 31, 21, 0, 0, tzinfo=UTC), date(2027, 1, 1)),
        (datetime(2026, 11, 13, 0, 0, 0, tzinfo=UTC), date(2026, 11, 13)),
    ],
)
def test_the_day_turns_at_midnight_in_kampala(moment: datetime, day: date) -> None:
    assert business_date(moment) == day


def test_the_business_day_needs_a_zone() -> None:
    with pytest.raises(ValueError):
        business_date(datetime(2026, 11, 13, 9, 0))
