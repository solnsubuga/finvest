from datetime import date, timedelta

import pytest

from app.modules.investments.domain.interest import (
    Accrual,
    ClientReturns,
    DayEarnings,
    InvalidTerms,
    Terms,
    accrued,
    client_returns,
    day_accrual,
    day_earnings,
    days_earned,
    earned_on,
    maturity_date,
    posting_due,
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


# What a daily run posts -------------------------------------------------------------------------

START = date(2026, 11, 13)
NOTHING = Accrual(ugx(0), ugx(0))


def due(
    run_date: date,
    posted: Accrual = NOTHING,
    posted_through: date | None = None,
    terms: Terms = BILL_91,
    term_days: int | None = 91,
) -> Accrual | None:
    posting = posting_due(
        terms,
        start=START,
        term_days=term_days,
        posted=posted,
        posted_through=posted_through,
        run_date=run_date,
    )
    return posting.amount if posting else None


def test_nothing_is_posted_on_or_before_the_start_date() -> None:
    assert due(START) is None
    assert due(START - timedelta(days=3)) is None


def test_the_first_run_after_the_start_posts_one_day() -> None:
    posting = posting_due(
        BILL_91,
        start=START,
        term_days=91,
        posted=NOTHING,
        posted_through=None,
        run_date=START + timedelta(days=1),
    )
    assert posting is not None
    assert (posting.days, posting.through) == (1, date(2026, 11, 14))
    assert posting.amount == posting.total == Accrual(ugx(342), ugx(51))
    assert posting.amount.net == ugx(291)


def test_a_run_after_missed_days_posts_them_all_at_once() -> None:
    one_day = accrued(BILL_91, 1)
    amount = due(START + timedelta(days=4), posted=one_day, posted_through=date(2026, 11, 14))
    four_days = accrued(BILL_91, 4)
    assert amount == Accrual(four_days.gross - one_day.gross, four_days.tax - one_day.tax)


def test_a_day_already_paid_is_not_paid_again() -> None:
    two_days = accrued(BILL_91, 2)
    paid_through = START + timedelta(days=2)
    assert due(paid_through, posted=two_days, posted_through=paid_through) is None
    assert (
        due(paid_through - timedelta(days=1), posted=two_days, posted_through=paid_through) is None
    )


def test_runs_over_a_whole_term_post_exactly_the_formula_and_then_stop() -> None:
    posted, through = NOTHING, None
    for offset in range(1, 120):
        posting = posting_due(
            BILL_91,
            start=START,
            term_days=91,
            posted=posted,
            posted_through=through,
            run_date=START + timedelta(days=offset),
        )
        assert (posting is None) == (offset > 91)
        if posting is not None:
            posted, through = posting.total, posting.through
    assert posted == accrued(BILL_91, 91)
    assert posted.net == ugx(26_490)
    assert through == maturity_date(START, 91)


def test_a_run_long_after_maturity_pays_only_to_maturity() -> None:
    posting = posting_due(
        BILL_91,
        start=START,
        term_days=91,
        posted=NOTHING,
        posted_through=None,
        run_date=START + timedelta(days=400),
    )
    assert posting is not None
    assert (posting.days, posting.through) == (91, date(2027, 2, 12))


def test_an_open_term_is_paid_for_as_long_as_it_runs() -> None:
    bond = Terms(usd(50_000), annual_rate_bps=725, tax_bps=500)
    posting = posting_due(
        bond,
        start=START,
        term_days=None,
        posted=Accrual(usd(0), usd(0)),
        posted_through=None,
        run_date=START + timedelta(days=400),
    )
    assert posting is not None
    assert posting.days == 400
    assert posting.total == accrued(bond, 400)


def test_a_day_that_earns_nothing_is_still_marked_as_paid() -> None:
    tiny = Terms(ugx(100), annual_rate_bps=1250, tax_bps=1500)
    posting = posting_due(
        tiny,
        start=START,
        term_days=91,
        posted=NOTHING,
        posted_through=None,
        run_date=START + timedelta(days=1),
    )
    assert posting is not None
    assert posting.amount == NOTHING
    assert posting.through == date(2026, 11, 14)


def test_interest_is_never_taken_back() -> None:
    too_much = Accrual(ugx(5_000), ugx(750))
    with pytest.raises(InvalidTerms):
        due(START + timedelta(days=2), posted=too_much, posted_through=date(2026, 11, 14))


# One day's earnings, as the daily notice counts them ---------------------------------------------

START = date(2026, 11, 13)


def test_a_day_earns_what_the_app_shows_for_that_day() -> None:
    # 1,000,000 x 12.5% / 365 = 342 before tax, 291 after.
    assert earned_on(BILL_91, START, START + timedelta(days=1), 91) == ugx(291)
    assert all(
        earned_on(BILL_91, START, START + timedelta(days=n), 91) == day_accrual(BILL_91, n).net
        for n in range(1, 92)
    )


def test_nothing_is_earned_on_the_start_date_before_it_or_after_maturity() -> None:
    for on in (START - timedelta(days=1), START, START + timedelta(days=92)):
        assert earned_on(BILL_91, START, on, 91) == ugx(0)
    assert earned_on(BILL_91, START, START + timedelta(days=500), None) > ugx(0)


def test_the_days_add_up_to_the_interest_at_maturity() -> None:
    days = [earned_on(BILL_91, START, START + timedelta(days=n), 91) for n in range(0, 100)]
    assert sum((d.minor for d in days), 0) == client_returns(BILL_91, 91).interest.minor


def test_a_client_is_told_the_sum_across_investments_that_earned() -> None:
    two_bills = [(BILL_91, START, 91), (BILL_91, START, 91)]
    assert day_earnings(two_bills, START + timedelta(days=1), Currency.UGX) == DayEarnings(
        count=2, net=ugx(582)
    )
    # One that has matured, or starts today, earned nothing and is not counted.
    mixed = [(BILL_91, START, 91), (BILL_91, START - timedelta(days=91), 91)]
    assert day_earnings(mixed, START + timedelta(days=1), Currency.UGX) == DayEarnings(
        count=1, net=ugx(291)
    )
    assert day_earnings([], START, Currency.UGX) == DayEarnings(count=0, net=ugx(0))
