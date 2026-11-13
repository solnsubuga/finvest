from datetime import UTC, datetime, timedelta

import pytest

from app.modules.dealing.domain.allocation import Held, InvalidAllocation, to_release
from app.shared.money import Currency, Money

START = datetime(2026, 11, 19, 9, tzinfo=UTC)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def held(name: str, minutes: int, amount: int) -> Held:
    return Held(name, START + timedelta(minutes=minutes), ugx(amount))


HELD = [held("old", 0, 300), held("mid", 5, 200), held("new", 10, 100)]


@pytest.mark.parametrize(
    ("limit", "released"),
    [
        (600, []),
        (1_000, []),
        (599, ["new"]),
        (500, ["new"]),
        (499, ["new", "mid"]),
        (300, ["new", "mid"]),
        (299, ["new", "mid", "old"]),
        (0, ["new", "mid", "old"]),
    ],
)
def test_the_newest_allocations_are_released_until_what_is_left_fits(
    limit: int, released: list[str]
) -> None:
    assert to_release(HELD, ugx(limit)) == released


def test_allocations_at_the_same_moment_release_the_higher_id_first() -> None:
    same = [held("a", 0, 100), held("b", 0, 100)]
    assert to_release(same, ugx(100)) == ["b"]


def test_nothing_held_releases_nothing_and_a_negative_limit_is_refused() -> None:
    assert to_release([], ugx(0)) == []
    with pytest.raises(InvalidAllocation):
        to_release(HELD, ugx(-1))
