"""The cells of the bank transfer file: amounts in the currency's own decimals, and names a
spreadsheet will not run as a formula."""

import pytest

from app.modules.payouts.bank_payouts import _amount, _cell
from app.shared.money import Currency, Money


@pytest.mark.parametrize(
    ("money", "cell"),
    [
        (Money(2_500_000, Currency.UGX), "2500000"),
        (Money(0, Currency.UGX), "0"),
        (Money(123_456, Currency.USD), "1234.56"),
        (Money(5, Currency.USD), "0.05"),
        (Money(100, Currency.USD), "1.00"),
    ],
)
def test_amounts_have_the_currencys_decimal_places(money: Money, cell: str) -> None:
    assert _amount(money) == cell


@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_a_name_that_starts_like_a_formula_is_quoted(start: str) -> None:
    assert _cell(start + "SUM(A1)") == "'" + start + "SUM(A1)"


@pytest.mark.parametrize("name", ["Nakato Sarah", "", "O'Brien", "Ssemwanga-Kato"])
def test_other_names_are_left_alone(name: str) -> None:
    assert _cell(name) == name
