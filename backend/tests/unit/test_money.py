from fractions import Fraction
from itertools import product

import pytest

from app.shared.money import MAX_MINOR, Currency, CurrencyMismatch, Money, MoneyError, Rounding

UGX = Currency.UGX
USD = Currency.USD


def ugx(minor: int) -> Money:
    return Money(minor, UGX)


@pytest.mark.parametrize("bad", [1.0, 1.5, True, False, "100", None, 10**30, -(2**63)])
def test_only_integers_in_range_are_money(bad: object) -> None:
    with pytest.raises(MoneyError):
        Money(bad, UGX)  # type: ignore[arg-type]


def test_the_largest_amount_fits_a_database_bigint() -> None:
    assert Money(MAX_MINOR, UGX).minor == 2**63 - 1
    assert Money(-MAX_MINOR, UGX).is_negative
    with pytest.raises(MoneyError):
        Money(MAX_MINOR, UGX) + ugx(1)


def test_an_unknown_currency_is_refused() -> None:
    with pytest.raises(MoneyError):
        Money(1, "KES")  # type: ignore[arg-type]


def test_arithmetic_stays_exact() -> None:
    assert ugx(1_500) + ugx(250) == ugx(1_750)
    assert ugx(1_500) - ugx(2_000) == ugx(-500)
    assert -ugx(7) == ugx(-7)
    assert abs(ugx(-7)) == ugx(7)
    assert Money.zero(USD).is_zero
    assert ugx(1).is_positive and not ugx(1).is_negative


def test_comparisons() -> None:
    assert ugx(1) < ugx(2) <= ugx(2) < ugx(3)
    assert ugx(3) > ugx(2) >= ugx(2)
    assert ugx(5) == ugx(5)
    assert ugx(5) != Money(5, USD)


@pytest.mark.parametrize(
    "operation",
    [
        lambda a, b: a + b,
        lambda a, b: a - b,
        lambda a, b: a < b,
        lambda a, b: a <= b,
        lambda a, b: a > b,
        lambda a, b: a >= b,
    ],
)
def test_currencies_never_mix(operation: object) -> None:
    with pytest.raises(CurrencyMismatch):
        operation(ugx(1), Money(1, USD))  # type: ignore[operator]


def test_money_combines_only_with_money() -> None:
    with pytest.raises(MoneyError):
        ugx(1) + 1  # type: ignore[operator]


@pytest.mark.parametrize(
    ("text", "currency", "minor"),
    [
        ("1500", UGX, 1500),
        ("0", UGX, 0),
        ("-20", UGX, -20),
        ("12.34", USD, 1234),
        ("12.3", USD, 1230),
        ("12", USD, 1200),
        ("0.05", USD, 5),
        ("-0.05", USD, -5),
    ],
)
def test_parsing_and_printing_round_trip(text: str, currency: Currency, minor: int) -> None:
    amount = Money.from_major(text, currency)
    assert amount == Money(minor, currency)
    assert Money.from_major(amount.to_major(), currency) == amount


def test_printing() -> None:
    assert ugx(1500).to_major() == "1500"
    assert Money(1230, USD).to_major() == "12.30"
    assert Money(-5, USD).to_major() == "-0.05"


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("1500.5", UGX),  # shillings have no cents
        ("12.345", USD),  # never rounded on the way in
        ("1,500", UGX),
        ("1e3", UGX),
        (" 15", UGX),
        ("", UGX),
        ("+15", UGX),
        (".5", USD),
        ("NaN", USD),
        ("\u0661\u0665", UGX),  # digits, but not ASCII digits
    ],
)
def test_parsing_refuses_anything_inexact(text: str, currency: Currency) -> None:
    with pytest.raises(MoneyError):
        Money.from_major(text, currency)


def test_parsing_refuses_a_float() -> None:
    with pytest.raises(MoneyError):
        Money.from_major(12.34, USD)  # type: ignore[arg-type]


def reference(amount: int, numerator: int, denominator: int, rounding: Rounding) -> int:
    """Rounding worked out from first principles with exact fractions."""
    exact = Fraction(abs(amount * numerator), denominator)
    floor = exact.numerator // exact.denominator
    remainder = exact - floor
    if remainder == 0 or rounding is Rounding.DOWN:
        magnitude = floor
    elif rounding is Rounding.UP:
        magnitude = floor + 1
    elif remainder != Fraction(1, 2):
        magnitude = floor + (1 if remainder > Fraction(1, 2) else 0)
    elif rounding is Rounding.HALF_UP:
        magnitude = floor + 1
    else:
        magnitude = floor + (floor % 2)
    return -magnitude if amount * numerator < 0 else magnitude


def test_ratios_round_exactly_in_every_mode() -> None:
    amounts = [*range(-25, 26), 999, 1_000, 1_001, 123_456_789]
    for amount, numerator, denominator, rounding in product(
        amounts, range(-3, 12), (1, 2, 3, 4, 7, 8, 365), Rounding
    ):
        result = ugx(amount).mul_ratio(numerator, denominator, rounding)
        assert result.minor == reference(amount, numerator, denominator, rounding)


def test_interest_for_a_treasury_bill() -> None:
    # 12.5% a year for 91 days on 1,000,000 shillings is 31,164.38...
    principal = ugx(1_000_000)
    assert principal.mul_ratio(125 * 91, 1000 * 365, Rounding.DOWN) == ugx(31_164)
    assert principal.mul_ratio(125 * 91, 1000 * 365, Rounding.HALF_EVEN) == ugx(31_164)
    assert principal.mul_ratio(125 * 91, 1000 * 365, Rounding.UP) == ugx(31_165)


def test_ties() -> None:
    assert ugx(5).mul_ratio(1, 2, Rounding.HALF_EVEN) == ugx(2)
    assert ugx(7).mul_ratio(1, 2, Rounding.HALF_EVEN) == ugx(4)
    assert ugx(5).mul_ratio(1, 2, Rounding.HALF_UP) == ugx(3)
    assert ugx(-5).mul_ratio(1, 2, Rounding.HALF_UP) == ugx(-3)
    assert ugx(-5).mul_ratio(1, 2, Rounding.DOWN) == ugx(-2)


@pytest.mark.parametrize(
    ("numerator", "denominator"), [(0.125, 1), (1, 8.0), (True, 2), (1, 0), (1, -2)]
)
def test_a_ratio_is_two_integers_with_a_positive_denominator(
    numerator: object, denominator: object
) -> None:
    with pytest.raises(MoneyError):
        ugx(100).mul_ratio(numerator, denominator, Rounding.DOWN)  # type: ignore[arg-type]


def test_rounding_must_be_chosen() -> None:
    with pytest.raises(MoneyError):
        ugx(100).mul_ratio(1, 3, "down")  # type: ignore[arg-type]


def test_money_is_immutable_and_hashable() -> None:
    amount = ugx(5)
    with pytest.raises(AttributeError):
        amount.minor = 6  # type: ignore[misc]
    assert {amount, ugx(5)} == {amount}
