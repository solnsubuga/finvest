"""Whole-number shares for the Portfolio product split."""

from itertools import product

import pytest

from app.modules.insights.domain.shares import whole_percentages


def test_shares_follow_the_values() -> None:
    assert whole_percentages([5_049_486, 3_115_274]) == [62, 38]


def test_one_part_is_all_of_it() -> None:
    assert whole_percentages([1]) == [100]


def test_thirds_add_up_to_a_hundred_and_the_first_takes_the_spare_point() -> None:
    assert whole_percentages([1, 1, 1]) == [34, 33, 33]


def test_the_spare_points_go_to_the_largest_remainders() -> None:
    # 1/7 = 14.28, 2/7 = 28.57, 4/7 = 57.14: the 28.57 is the one rounded up.
    assert whole_percentages([1, 2, 4]) == [14, 29, 57]


def test_a_tiny_part_can_show_as_zero() -> None:
    assert whole_percentages([1, 10_000_000]) == [0, 100]


def test_nothing_has_no_shares() -> None:
    assert whole_percentages([]) == []
    assert whole_percentages([0, 0]) == [0, 0]


@pytest.mark.parametrize("values", [[-1, 5], [1.5, 2], [True, 1]])
def test_only_whole_non_negative_amounts(values: list[int]) -> None:
    with pytest.raises(ValueError):
        whole_percentages(values)


def test_every_small_split_adds_up_to_a_hundred_and_no_share_is_off_by_a_point() -> None:
    for values in product(range(0, 9), repeat=3):
        shares = whole_percentages(list(values))
        total = sum(values)
        if total == 0:
            assert shares == [0, 0, 0]
            continue
        assert sum(shares) == 100
        for value, share in zip(values, shares, strict=True):
            exact = value * 100 / total
            assert exact - 1 < share < exact + 1
