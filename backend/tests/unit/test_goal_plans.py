"""The plan for a goal: monthly savings, whole terms, product minimums and the chosen product."""

from datetime import date

import pytest

from app.modules.goals.domain.plans import (
    MAX_SAVINGS,
    PlanProduct,
    add_months,
    best_plan,
    interest_by,
    plan_for,
    product_for_goal,
    reached_with,
    saving_dates,
)
from app.modules.investments.domain.interest import Terms, client_returns
from app.shared.money import Currency, Money


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


TODAY = date(2026, 11, 13)
BILL_91 = PlanProduct("TREA-91", "91 Day Treasury Bill", 91, ugx(100_000), 1250, 1500, "10.63")
BILL_182 = PlanProduct("TREA-182", "182 Day Treasury Bill", 182, ugx(200_000), 1375, 1500, "11.69")
USD_TERM = PlanProduct("USD-1Y", "Dollar note", 365, Money(10_000, Currency.USD), 725, 500, "6.89")


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 11, 13), 0, date(2026, 11, 13)),
        (date(2026, 11, 13), 1, date(2026, 12, 13)),
        (date(2026, 11, 13), 2, date(2027, 1, 13)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2026, 1, 30), 13, date(2027, 2, 28)),
        (date(2026, 12, 1), 25, date(2029, 1, 1)),
    ],
)
def test_months_land_on_the_same_day_or_the_28th(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


def test_a_saving_is_planned_only_if_its_term_ends_by_the_date() -> None:
    dates = saving_dates(TODAY, date(2027, 11, 13), 91)
    assert dates[0] == TODAY
    assert dates[-1] == date(2027, 8, 13)
    assert len(dates) == 10
    assert all((date(2027, 11, 13) - day).days >= 91 for day in dates)
    assert saving_dates(TODAY, TODAY + (date(2027, 1, 1) - TODAY), 91) == []


def test_the_term_ending_on_the_date_itself_counts() -> None:
    assert saving_dates(TODAY, date.fromordinal(TODAY.toordinal() + 91), 91) == [TODAY]
    assert saving_dates(TODAY, date.fromordinal(TODAY.toordinal() + 90), 91) == []


def test_a_plan_has_at_most_ten_years_of_savings() -> None:
    assert len(saving_dates(TODAY, date(2050, 1, 1), 91)) == MAX_SAVINGS


def test_interest_counts_whole_terms_only_and_matches_the_investment_formula() -> None:
    saving = ugx(1_000_000)
    # 200 days hold two whole 91 day terms, 182 days, and the last 18 earn nothing here.
    by = date.fromordinal(TODAY.toordinal() + 200)
    expected = client_returns(Terms(saving, 1250, 1500), 182).interest
    assert interest_by(saving, BILL_91, TODAY, by) == expected
    assert interest_by(saving, BILL_91, TODAY, date.fromordinal(TODAY.toordinal() + 90)) == ugx(0)


def test_one_saving_of_a_million_for_one_term_earns_what_the_product_screen_shows() -> None:
    by = date.fromordinal(TODAY.toordinal() + 91)
    assert interest_by(ugx(1_000_000), BILL_91, TODAY, by) == ugx(26_490)


def test_the_plan_is_the_smallest_whole_thousand_that_reaches_the_target() -> None:
    target_date = date(2027, 11, 13)
    plan = plan_for(BILL_91, ugx(2_000_000), TODAY, target_date)
    assert plan is not None
    assert plan.saving.minor % 1_000 == 0
    dates = saving_dates(TODAY, target_date, 91)
    assert reached_with(plan.saving, BILL_91, dates, target_date) >= ugx(2_000_000)
    assert reached_with(plan.saving - ugx(1_000), BILL_91, dates, target_date) < ugx(2_000_000)
    assert plan.savings == 10
    assert plan.put_in == Money(plan.saving.minor * 10, Currency.UGX)
    assert plan.put_in + plan.interest == reached_with(plan.saving, BILL_91, dates, target_date)
    assert plan.without_investing == ugx(200_000)
    assert plan.saving < plan.without_investing
    assert (plan.first_on, plan.last_on) == (TODAY, date(2027, 8, 13))
    assert not plan.raised_to_minimum


def test_a_target_below_the_minimum_saves_the_minimum_and_says_so() -> None:
    plan = plan_for(BILL_91, ugx(150_000), TODAY, date(2027, 11, 13))
    assert plan is not None
    assert plan.saving == ugx(100_000)
    assert plan.raised_to_minimum


def test_no_plan_when_no_term_fits_or_nothing_is_needed() -> None:
    assert plan_for(BILL_91, ugx(1_000_000), TODAY, date(2027, 1, 1)) is None
    assert plan_for(BILL_91, ugx(0), TODAY, date(2027, 11, 13)) is None
    assert best_plan([BILL_91, BILL_182], ugx(1_000_000), TODAY, date(2027, 1, 1)) is None


def test_the_plan_asking_least_each_month_wins_even_over_a_better_rate() -> None:
    # A year away, ten 91 day savings fit but only seven 182 day ones, so the shorter bill
    # asks for less each month although it pays less.
    plan = best_plan([BILL_182, BILL_91], ugx(2_000_000), TODAY, date(2027, 11, 13))
    assert plan is not None
    assert plan.product == BILL_91
    longer = plan_for(BILL_182, ugx(2_000_000), TODAY, date(2027, 11, 13))
    assert longer is not None
    assert longer.saving > plan.saving


def test_the_longer_bill_wins_when_it_asks_less() -> None:
    # One saving is all that fits a little over half a year away. The 182 day bill earns
    # interest on it by the date and the 91 day bill earns two terms, at a lower rate.
    target_date = date.fromordinal(TODAY.toordinal() + 182)
    plan = best_plan([BILL_91, BILL_182], ugx(5_000_000), TODAY, target_date)
    assert plan is not None
    short = plan_for(BILL_91, ugx(5_000_000), TODAY, target_date)
    assert short is not None
    assert plan.saving <= short.saving


def test_a_minimum_too_high_for_the_need_loses_to_one_that_takes_less() -> None:
    longer = plan_for(BILL_182, ugx(1_200_000), TODAY, date(2027, 11, 13))
    assert longer is not None
    assert longer.raised_to_minimum
    plan = best_plan([BILL_182, BILL_91], ugx(1_200_000), TODAY, date(2027, 11, 13))
    assert plan is not None
    assert plan.product == BILL_91
    assert not plan.raised_to_minimum


def test_only_products_in_the_goals_currency_are_used() -> None:
    assert best_plan([USD_TERM], ugx(2_000_000), TODAY, date(2028, 11, 13)) is None
    dollars = best_plan(
        [BILL_91, USD_TERM], Money(500_000, Currency.USD), TODAY, date(2028, 11, 13)
    )
    assert dollars is not None
    assert dollars.product == USD_TERM
    assert dollars.saving.minor % 100 == 0


def test_a_goal_with_no_date_uses_the_shortest_term() -> None:
    chosen = product_for_goal([BILL_182, BILL_91], ugx(3_000_000), TODAY, None)
    assert chosen == BILL_91


def test_a_dated_goal_uses_its_plan_and_none_when_no_term_fits() -> None:
    assert product_for_goal([BILL_182, BILL_91], ugx(2_000_000), TODAY, date(2027, 11, 13)) == (
        BILL_91
    )
    assert product_for_goal([BILL_182, BILL_91], ugx(2_000_000), TODAY, date(2027, 1, 1)) is None
    assert product_for_goal([USD_TERM], ugx(2_000_000), TODAY, None) is None


@pytest.mark.parametrize("target", [100_000, 999_999, 2_000_000, 50_000_000, 123_456_789])
@pytest.mark.parametrize("months", [4, 7, 12, 25, 60, 119])
def test_every_plan_reaches_its_target_and_one_step_less_does_not(target: int, months: int) -> None:
    target_date = add_months(TODAY, months)
    for product in (BILL_91, BILL_182):
        plan = plan_for(product, ugx(target), TODAY, target_date)
        if plan is None:
            assert (target_date - TODAY).days < product.term_days
            continue
        dates = saving_dates(TODAY, target_date, product.term_days)
        assert reached_with(plan.saving, product, dates, target_date) >= ugx(target)
        if not plan.raised_to_minimum and plan.saving > ugx(1_000):
            less = plan.saving - ugx(1_000)
            assert reached_with(less, product, dates, target_date) < ugx(target)
        assert plan.saving <= plan.without_investing or plan.raised_to_minimum
