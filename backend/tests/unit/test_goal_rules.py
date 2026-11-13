"""A goal's own rules: what it may be set up with, and how its progress is labelled."""

from datetime import date, timedelta

import pytest

from app.modules.goals.domain.goals import (
    MAX_HORIZON,
    GoalRefusal,
    Progress,
    Standing,
    check_goal,
    clean_name,
    progress,
    saving_step,
)
from app.shared.money import Currency, Money, Rounding


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


TODAY = date(2026, 11, 13)


@pytest.mark.parametrize(
    ("name", "target", "target_date", "refusal"),
    [
        ("School fees", 2_000_000, date(2027, 2, 1), None),
        ("Emergency fund", 3_000_000, None, None),
        ("   ", 2_000_000, None, GoalRefusal.NAME_EMPTY),
        ("x" * 61, 2_000_000, None, GoalRefusal.NAME_TOO_LONG),
        ("x" * 60, 2_000_000, None, None),
        ("Land", 0, None, GoalRefusal.TARGET_NOT_POSITIVE),
        ("Land", -5, None, GoalRefusal.TARGET_NOT_POSITIVE),
        ("Land", 1, TODAY, GoalRefusal.DATE_NOT_IN_FUTURE),
        ("Land", 1, TODAY - timedelta(days=1), GoalRefusal.DATE_NOT_IN_FUTURE),
        ("Land", 1, TODAY + MAX_HORIZON, None),
        ("Land", 1, TODAY + MAX_HORIZON + timedelta(days=1), GoalRefusal.DATE_TOO_FAR),
    ],
)
def test_what_a_goal_may_be_set_up_with(
    name: str, target: int, target_date: date | None, refusal: GoalRefusal | None
) -> None:
    assert check_goal(name, ugx(target), target_date, TODAY) is refusal


def test_names_lose_extra_spaces() -> None:
    assert clean_name("  Plot of   land\tin Wakiso ") == "Plot of land in Wakiso"


def standing(
    value: int, *, target_date: date | None = date(2027, 11, 13), today: date = TODAY
) -> Progress:
    return progress(
        target=ugx(1_000_000),
        value=ugx(value),
        interest=ugx(0),
        awaiting_payment=ugx(0),
        set_up_on=date(2026, 11, 13),
        target_date=target_date,
        today=today,
    )


def test_reached_beats_every_other_label() -> None:
    assert standing(1_000_000).standing is Standing.REACHED
    assert standing(1_200_000, target_date=None).percent == 100


def test_a_goal_with_no_date_is_neither_ahead_nor_behind() -> None:
    assert standing(0, target_date=None).standing is Standing.NO_DATE


def test_on_track_follows_a_straight_line_to_the_date() -> None:
    # Half the year gone, so half the target is expected, rounded up.
    halfway = date(2026, 11, 13) + timedelta(days=182)
    expected = ugx(1_000_000).mul_ratio(182, 365, Rounding.UP)
    assert standing(expected.minor, today=halfway).standing is Standing.ON_TRACK
    assert standing(expected.minor - 1, today=halfway).standing is Standing.BEHIND


def test_on_the_first_day_any_goal_is_on_track() -> None:
    assert standing(0).standing is Standing.ON_TRACK


def test_after_the_date_anything_short_of_the_target_is_behind() -> None:
    assert standing(999_999, today=date(2028, 1, 1)).standing is Standing.BEHIND


def test_percent_is_whole_and_capped() -> None:
    assert standing(333_333).percent == 33
    assert standing(999_999).percent == 99
    assert standing(5_000_000).percent == 100


def test_savings_round_to_thousands_of_shillings_or_whole_dollars() -> None:
    assert saving_step(Currency.UGX) == ugx(1_000)
    assert saving_step(Currency.USD) == Money(100, Currency.USD)
