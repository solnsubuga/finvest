from datetime import date

import pytest

from app.modules.investments.domain.interest import (
    Accrual,
    ClientReturns,
    InvalidTerms,
    Terms,
    accrued,
    client_returns,
    day_accrual,
    days_earned,
    maturity_date,
)
from app.shared.money import Currency, Money


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def usd(cents: int) -> Money:
    return Money(cents, Currency.USD)


BILL_91 = Terms(ugx(1_000_000), annual_rate_bps=1250, tax_bps=1500)


def test_the_formula_is_principal_times_rate_times_days_over_365() -> None:
    # 1,000,000 x 12.5% x 91 / 365 = 31,164.38, and 15% of 31,164 is 4,674.60.
    result = accrued(BILL_91, 91)
    assert result == Accrual(gross=ugx(31_164), tax=ugx(4_674))
    assert result.net == ugx(26_490)


def test_the_client_sees_the_figure_on_the_design() -> None:
    # The product screen: UGX 1,000,000 for 91 days earns UGX 26,490.
    assert client_returns(BILL_91, 91) == ClientReturns(interest=ugx(26_490), total=ugx(1_026_490))


def test_client_returns_carry_no_tax_and_no_gross_figure() -> None:
    assert ClientReturns.__slots__ == ("interest", "total")


@pytest.mark.parametrize(
    ("principal", "rate", "tax", "days", "gross", "tax_due"),
    [
        (5_000_000, 1250, 1500, 91, 155_821, 23_373),
        (3_000_000, 1375, 1500, 182, 205_684, 30_852),
        (100_000, 1250, 1500, 1, 34, 5),
        (100_000, 1250, 1500, 0, 0, 0),
        (1, 1250, 1500, 91, 0, 0),
        (1_000_000, 1250, 0, 365, 125_000, 0),
    ],
)
def test_worked_examples(
    principal: int, rate: int, tax: int, days: int, gross: int, tax_due: int
) -> None:
    assert accrued(Terms(ugx(principal), rate, tax), days) == Accrual(ugx(gross), ugx(tax_due))


def test_a_year_is_365_days_even_in_a_leap_year() -> None:
    # 2028 is a leap year. A full 366 days earns one day more than the yearly rate.
    assert accrued(Terms(ugx(3_650_000), 1000, 0), 366).gross == ugx(366_000)


def test_dollars_are_worked_in_cents() -> None:
    # $1,000 at 7.25% for 30 days is $5.958..., and 5% tax on $5.95 is $0.2975.
    result = accrued(Terms(usd(100_000), 725, 500), 30)
    assert result == Accrual(gross=usd(595), tax=usd(29))
    assert result.net == usd(566)


@pytest.mark.parametrize(
    "terms",
    [
        BILL_91,
        Terms(ugx(100_000), 1250, 1500),
        Terms(ugx(333_333), 1375, 1500),
        Terms(ugx(7), 1250, 1500),
        Terms(usd(50_000), 725, 500),
        Terms(ugx(999_999_999_999), 4999, 9999),
    ],
)
def test_daily_postings_always_sum_to_the_formula_and_are_never_negative(terms: Terms) -> None:
    currency = terms.principal.currency
    gross, tax = Money.zero(currency), Money.zero(currency)
    for day in range(1, 400):
        today = day_accrual(terms, day)
        assert not today.gross.is_negative
        assert not today.tax.is_negative
        assert not today.net.is_negative
        gross, tax = gross + today.gross, tax + today.tax
        assert Accrual(gross, tax) == accrued(terms, day)


def test_a_small_investment_can_earn_nothing_on_some_days() -> None:
    terms = Terms(ugx(1_000), 1250, 1500)
    daily = [day_accrual(terms, day).gross.minor for day in range(1, 8)]
    assert daily == [0, 0, 1, 0, 0, 1, 0]


@pytest.mark.parametrize("principal", [0, -1])
def test_the_principal_must_be_positive(principal: int) -> None:
    with pytest.raises(InvalidTerms):
        Terms(ugx(principal), 1250, 1500)


@pytest.mark.parametrize(
    ("rate", "tax"),
    [(0, 1500), (-1, 1500), (5001, 1500), (1250, -1), (1250, 10_000), (12.5, 1500), (1250, 15.0)],
)
def test_rates_outside_the_allowed_range_or_as_floats_are_refused(rate: int, tax: int) -> None:
    with pytest.raises(InvalidTerms):
        Terms(ugx(1_000_000), rate, tax)


@pytest.mark.parametrize("days", [-1, 1.5, True])
def test_days_are_whole_and_not_negative(days: int) -> None:
    with pytest.raises(InvalidTerms):
        accrued(BILL_91, days)


@pytest.mark.parametrize("day", [0, -1, 2.0])
def test_daily_postings_are_counted_from_day_one(day: int) -> None:
    with pytest.raises(InvalidTerms):
        day_accrual(BILL_91, day)


def test_the_maturity_date_is_the_start_plus_the_term() -> None:
    assert maturity_date(date(2026, 11, 13), 91) == date(2027, 2, 12)
    assert maturity_date(date(2026, 10, 10), 91) == date(2027, 1, 9)
    with pytest.raises(InvalidTerms):
        maturity_date(date(2026, 11, 13), 0)


@pytest.mark.parametrize(
    ("on", "earned"),
    [
        (date(2026, 11, 12), 0),  # before it started
        (date(2026, 11, 13), 0),  # the start date earns nothing
        (date(2026, 11, 14), 1),
        (date(2027, 2, 11), 90),
        (date(2027, 2, 12), 91),  # maturity
        (date(2027, 2, 13), 91),  # nothing is earned after maturity
        (date(2028, 1, 1), 91),
    ],
)
def test_days_earned_stops_at_maturity(on: date, earned: int) -> None:
    assert days_earned(date(2026, 11, 13), on, 91) == earned


def test_an_open_term_keeps_earning() -> None:
    assert days_earned(date(2026, 11, 13), date(2028, 11, 13), None) == 731
    with pytest.raises(InvalidTerms):
        days_earned(date(2026, 11, 13), date(2026, 11, 14), 0)
