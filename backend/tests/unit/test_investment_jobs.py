import pytest

from app.infrastructure.jobs import PermanentJobError
from app.modules.investments.jobs import _run_date


def test_a_run_is_for_the_kampala_day_it_was_scheduled_in() -> None:
    # 00:30 in Kampala on 14 November is still 13 November in UTC.
    assert _run_date({"scheduled_for": "2026-11-13T21:30:00+00:00"}).isoformat() == "2026-11-14"
    assert _run_date({"scheduled_for": "2026-11-14T20:59:59+00:00"}).isoformat() == "2026-11-14"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, "scheduled_for_missing"),
        ({"scheduled_for": 5}, "scheduled_for_missing"),
        ({"scheduled_for": "yesterday"}, "scheduled_for_invalid"),
        ({"scheduled_for": "2026-11-13T21:30:00"}, "scheduled_for_invalid"),
    ],
)
def test_a_run_with_no_usable_day_fails_for_good(payload: dict[str, object], code: str) -> None:
    with pytest.raises(PermanentJobError) as error:
        _run_date(payload)
    assert error.value.code == code
