"""A client's own withdrawal limits only ever tighten the shared ones."""

from datetime import date

import pytest

from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.limits import (
    ClientLimits,
    InvalidLimits,
    Refusal,
    WithdrawalLimits,
    check_withdrawal,
    for_client,
)
from app.shared.money import Currency, Money


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


SHARED = WithdrawalLimits(
    effective_from=date(2026, 1, 1),
    mobile_money_per_payout=ugx(5_000_000),
    review_above=ugx(10_000_000),
)


def test_no_client_limits_leave_the_shared_ones() -> None:
    assert for_client(SHARED, None) is SHARED
    assert for_client(SHARED, ClientLimits()) is SHARED


def test_each_sort_takes_the_lower_of_the_two() -> None:
    applied = for_client(
        SHARED,
        ClientLimits(
            mobile_money_per_payout=ugx(1_000_000),
            bank_per_day=ugx(20_000_000),
            review_above=ugx(50_000_000),
        ),
    )
    assert applied.mobile_money_per_payout == ugx(1_000_000)
    assert applied.bank_per_day == ugx(20_000_000)
    # A client's figure above the shared one changes nothing.
    assert applied.review_above == ugx(10_000_000)
    assert applied.mobile_money_per_day is None


def test_a_daily_limit_below_one_payout_lowers_one_payout_too() -> None:
    applied = for_client(
        SHARED, ClientLimits(mobile_money_per_day=ugx(300_000), bank_per_day=ugx(2_000_000))
    )
    assert applied.mobile_money_per_payout == ugx(300_000)
    assert applied.bank_per_payout == ugx(2_000_000)
    decision = check_withdrawal(applied, DestinationKind.MOBILE_MONEY, ugx(200_000), ugx(200_000))
    assert decision.refusal is Refusal.OVER_DAILY_LIMIT
    decision = check_withdrawal(applied, DestinationKind.BANK, ugx(2_000_001), ugx(0))
    assert decision.refusal is Refusal.OVER_BANK_LIMIT


def test_a_lower_review_figure_holds_smaller_payouts() -> None:
    applied = for_client(SHARED, ClientLimits(review_above=ugx(500_000)))
    decision = check_withdrawal(applied, DestinationKind.MOBILE_MONEY, ugx(600_000), ugx(0))
    assert decision.allowed and decision.needs_review


@pytest.mark.parametrize("amount", [0, -1])
def test_a_client_limit_is_positive(amount: int) -> None:
    with pytest.raises(InvalidLimits):
        ClientLimits(bank_per_payout=ugx(amount))


def test_a_client_limit_is_in_shillings() -> None:
    with pytest.raises(InvalidLimits):
        ClientLimits(bank_per_payout=Money(100, Currency.USD))
