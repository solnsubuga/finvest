"""The pure rules of bond maturity: what a holding is repaid, and how what it cost goes back."""

from datetime import date

import pytest

from app.modules.bonds.domain.maturity import has_matured, redemption
from app.shared.money import Currency, Money
from tests.unit.test_bond_coupons import issue


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def test_a_holding_is_repaid_its_face_value_and_the_last_coupon_after_tax() -> None:
    # Bought at 98.5 with 883 of accrued interest, as in docs/bonds.md.
    repaid = redemption(issue(), ugx(1_000_000), ugx(985_883), ugx(883))
    assert (repaid.coupon.gross, repaid.coupon.tax, repaid.paid) == (
        ugx(80_000),
        ugx(8_000),
        ugx(1_072_000),
    )
    assert (repaid.clean, repaid.below_par, repaid.above_par) == (
        ugx(985_000),
        ugx(15_000),
        ugx(0),
    )


def test_a_bond_bought_above_par_gives_the_difference_back() -> None:
    repaid = redemption(issue(), ugx(1_000_000), ugx(1_030_000), ugx(10_000))
    assert (repaid.below_par, repaid.above_par) == (ugx(0), ugx(20_000))


def test_a_bond_bought_at_par_has_no_price_difference() -> None:
    repaid = redemption(issue(), ugx(500_000), ugx(512_000), ugx(12_000))
    assert (repaid.below_par, repaid.above_par, repaid.paid) == (ugx(0), ugx(0), ugx(536_000))


@pytest.mark.parametrize(
    ("face", "cost", "accrued"),
    [
        (1_000_000, 985_883, 883),
        (1_000_000, 1_051_000, 66_000),
        (100_000, 120_000, 0),
        (2_500_000, 2_300_001, 79_999),
    ],
)
def test_what_comes_out_of_the_holding_always_balances(face: int, cost: int, accrued: int) -> None:
    """The entry at maturity: the cost and the coupon in, the repayment, tax, accrued interest
    and price difference out. It sums to zero whatever the price was."""
    repaid = redemption(issue(), ugx(face), ugx(cost), ugx(accrued))
    debits = ugx(cost) + repaid.coupon.gross + repaid.below_par
    credits = repaid.paid + repaid.coupon.tax + repaid.accrued + repaid.above_par
    assert debits == credits
    assert not (repaid.below_par.is_positive and repaid.above_par.is_positive)


def test_an_issue_is_repaid_on_its_maturity_date_or_any_day_after() -> None:
    bond = issue()
    assert not has_matured(bond, date(2037, 5, 13))
    assert has_matured(bond, date(2037, 5, 14))
    assert has_matured(bond, date(2037, 6, 1))
