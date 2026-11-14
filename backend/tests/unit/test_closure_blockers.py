"""What stops an account closing: each blocker alone, all together, and none."""

import pytest

from app.modules.closures.domain.blockers import Blocker, Standing, blockers
from app.shared.money import Currency, Money

CLEAR = Standing(
    running_investments=0, owed=(), open_deposits=0, open_payouts=0, open_goals=0, live_plans=0
)


def test_nothing_left_means_nothing_blocks() -> None:
    assert blockers(CLEAR) == []


def test_an_amount_of_zero_owed_does_not_block() -> None:
    assert blockers(Standing(0, (Money(0, Currency.UGX),), 0, 0, 0, 0)) == []


@pytest.mark.parametrize(
    ("standing", "blocker"),
    [
        (Standing(1, (), 0, 0, 0, 0), Blocker.INVESTMENTS_RUNNING),
        (Standing(0, (Money(1, Currency.UGX),), 0, 0, 0, 0), Blocker.MONEY_OWED),
        (Standing(0, (Money(-5, Currency.USD),), 0, 0, 0, 0), Blocker.MONEY_OWED),
        (Standing(0, (), 1, 0, 0, 0), Blocker.DEPOSITS_IN_PROGRESS),
        (Standing(0, (), 0, 2, 0, 0), Blocker.PAYOUTS_IN_PROGRESS),
        (Standing(0, (), 0, 0, 1, 0), Blocker.GOALS_OPEN),
        (Standing(0, (), 0, 0, 0, 1), Blocker.PLANS_LIVE),
    ],
)
def test_each_blocker_alone(standing: Standing, blocker: Blocker) -> None:
    assert blockers(standing) == [blocker]


def test_every_blocker_at_once_in_the_order_to_clear_them() -> None:
    everything = Standing(2, (Money(10, Currency.UGX),), 1, 1, 1, 1)
    assert blockers(everything) == list(Blocker)
