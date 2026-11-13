"""Closing a day: which days can be closed, and what a closed day refuses."""

from datetime import UTC, date, datetime

import pytest

from app.modules.ledger.domain.closing import check_can_close, closing_staff_id, may_post_on
from app.modules.ledger.domain.entries import InvalidEntry
from app.modules.ledger.domain.errors import DayNotEnded
from app.shared.actors import SYSTEM, Actor

# 22:30 UTC on the 12th is already 01:30 on the 13th in Kampala.
LATE_EVENING_UTC = datetime(2026, 11, 12, 22, 30, tzinfo=UTC)


def test_a_day_that_has_ended_in_kampala_can_be_closed() -> None:
    check_can_close(date(2026, 11, 12), LATE_EVENING_UTC)
    check_can_close(date(2026, 1, 1), LATE_EVENING_UTC)


@pytest.mark.parametrize("day", [date(2026, 11, 13), date(2026, 11, 14), date(2027, 1, 1)])
def test_today_and_future_days_stay_open(day: date) -> None:
    with pytest.raises(DayNotEnded):
        check_can_close(day, LATE_EVENING_UTC)


def test_the_day_is_the_kampala_day_not_the_utc_day() -> None:
    just_before_midnight_kampala = datetime(2026, 11, 12, 20, 59, tzinfo=UTC)
    with pytest.raises(DayNotEnded):
        check_can_close(date(2026, 11, 12), just_before_midnight_kampala)
    check_can_close(date(2026, 11, 11), just_before_midnight_kampala)


def test_a_time_without_a_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        check_can_close(date(2026, 11, 12), datetime(2026, 11, 13, 9, 0))


@pytest.mark.parametrize(
    ("effective", "closed", "allowed"),
    [
        (date(2026, 11, 13), None, True),
        (date(2026, 11, 13), date(2026, 11, 12), True),
        (date(2026, 11, 12), date(2026, 11, 12), False),
        (date(2026, 11, 1), date(2026, 11, 12), False),
        (date(2027, 1, 1), date(2026, 11, 12), True),
    ],
)
def test_an_entry_may_be_dated_only_after_the_closed_day(
    effective: date, closed: date | None, allowed: bool
) -> None:
    assert may_post_on(effective, closed) is allowed


def test_only_staff_close_a_day() -> None:
    assert closing_staff_id(Actor("staff", "-Nstaff0000000000001")) == "-Nstaff0000000000001"
    for actor in (Actor("client", "-Nclient000000000001"), SYSTEM):
        with pytest.raises(InvalidEntry):
            closing_staff_id(actor)
