"""The pure rules of selling a bond early: when a sale can be recorded, and what it pays."""

from datetime import date

import pytest

from app.modules.bonds.domain.sales import (
    MAX_TEXT_LENGTH,
    SaleRefusal,
    check_request,
    check_sold_on,
    check_text,
    settlement,
)
from app.shared.money import Currency, Money
from tests.unit.test_bond_coupons import issue


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


STARTED = date(2026, 11, 16)


def test_a_sale_pays_the_price_and_accrued_interest_less_tax_on_the_interest() -> None:
    # The holding in docs/bonds.md, bought at 98.5 with 883 of accrued interest, sold on
    # 14 February 2027 at 97.25: 92 of the 181 days since 14 November.
    sold = settlement(issue(), ugx(1_000_000), ugx(985_883), ugx(883), 972_500, date(2027, 2, 14))
    assert (sold.clean, sold.accrued, sold.tax, sold.paid) == (
        ugx(972_500),
        ugx(40_662),
        ugx(4_066),
        ugx(1_009_096),
    )
    assert (sold.bought_clean, sold.below_cost, sold.above_cost) == (
        ugx(985_000),
        ugx(12_500),
        ugx(0),
    )


def test_a_bond_sold_for_more_than_it_cost_pays_the_difference() -> None:
    sold = settlement(issue(), ugx(1_000_000), ugx(985_883), ugx(883), 1_010_000, date(2026, 12, 1))
    assert (sold.above_cost, sold.below_cost) == (ugx(25_000), ugx(0))


def test_the_price_is_paid_rounded_down() -> None:
    sold = settlement(issue(), ugx(100_000), ugx(98_501), ugx(1), 987_654, date(2026, 11, 14))
    assert sold.clean == ugx(98_765)
    assert sold.accrued == ugx(0)


@pytest.mark.parametrize(
    ("face", "cost", "bought_accrued", "price", "sold_on"),
    [
        (1_000_000, 985_883, 883, 972_500, date(2027, 2, 14)),
        (1_000_000, 985_883, 883, 1_100_000, date(2026, 11, 17)),
        (100_000, 120_000, 0, 500_000, date(2027, 5, 13)),
        (2_500_000, 2_300_001, 79_999, 1_499_999, date(2030, 1, 1)),
        (300_000, 312_345, 12_345, 1_000_000, date(2036, 11, 14)),
    ],
)
def test_what_comes_out_of_the_holding_always_balances(
    face: int, cost: int, bought_accrued: int, price: int, sold_on: date
) -> None:
    """The entry for a sale: the cost and the accrued interest in, the proceeds, tax, accrued
    interest bought and price difference out. It sums to zero whatever the price was."""
    sold = settlement(issue(), ugx(face), ugx(cost), ugx(bought_accrued), price, sold_on)
    debits = ugx(cost) + sold.accrued + sold.above_cost
    credits = sold.paid + sold.tax + sold.bought_accrued + sold.below_cost
    assert debits == credits
    assert not (sold.above_cost.is_positive and sold.below_cost.is_positive)


def test_a_client_asks_to_sell_an_active_holding_before_maturity() -> None:
    assert check_request(issue(), STARTED, date(2037, 5, 13)) is None
    assert check_request(issue(), STARTED, date(2037, 5, 14)) is SaleRefusal.MATURED
    assert check_request(issue(), None, date(2027, 1, 1)) is SaleRefusal.NOT_ACTIVE


@pytest.mark.parametrize(
    ("sold_on", "today", "refusal"),
    [
        (date(2026, 12, 1), date(2026, 12, 1), None),
        (date(2026, 11, 16), date(2027, 5, 13), None),
        # A coupon date is in the sale's own period: accrued interest starts again from zero.
        (date(2027, 5, 14), date(2027, 5, 20), None),
        (date(2026, 12, 2), date(2026, 12, 1), SaleRefusal.SOLD_ON_IN_FUTURE),
        (date(2026, 11, 15), date(2026, 12, 1), SaleRefusal.SOLD_ON_BEFORE_START),
        # 14 May 2027 passed since: the holding was paid that coupon while the sale waited.
        (date(2027, 5, 13), date(2027, 5, 14), SaleRefusal.COUPON_SINCE),
        (date(2027, 1, 1), date(2027, 6, 1), SaleRefusal.COUPON_SINCE),
        (date(2037, 5, 14), date(2037, 5, 14), SaleRefusal.SOLD_ON_AT_MATURITY),
    ],
)
def test_a_sale_settles_after_the_holding_started_inside_the_current_period(
    sold_on: date, today: date, refusal: SaleRefusal | None
) -> None:
    assert check_sold_on(issue(), STARTED, sold_on, today) is refusal


def test_a_holding_that_never_started_cannot_be_sold() -> None:
    found = check_sold_on(issue(), None, date(2026, 12, 1), date(2026, 12, 1))
    assert found is SaleRefusal.SOLD_ON_BEFORE_START


def test_the_desk_reference_and_a_note_are_required_and_short() -> None:
    assert check_text("Trade 4471 with Stanbic") is None
    assert check_text("  ") is SaleRefusal.TEXT_REQUIRED
    assert check_text(None) is SaleRefusal.TEXT_REQUIRED
    assert check_text("x" * (MAX_TEXT_LENGTH + 1)) is SaleRefusal.TEXT_TOO_LONG
