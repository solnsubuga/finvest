from dataclasses import replace
from datetime import date

import pytest

from app.modules.investments.domain.interest import Terms, accrued
from app.modules.investments.domain.products import SEED_PRODUCTS
from app.modules.investments.domain.rates import SEED_RATES
from app.modules.migration.domain.finance import (
    Blocker,
    ControlTotals,
    DifferencePlace,
    Figure,
    InvalidFinanceCheck,
    SignOff,
    check_finances,
    check_investments,
    new_totals,
    opening_for,
    switch_blockers,
    total_lines,
)
from app.modules.migration.domain.trial import TrialReport, run_trial
from app.shared.money import Currency, Money
from tests.unit.test_migration_sheet import clients, investments
from tests.unit.test_migration_trial import DOLLARS, PENDING, client, investment

AS_OF = date(2026, 11, 13)
PRODUCTS = {product.code: product for product in SEED_PRODUCTS}
TAX = {code: tax for code, (_, tax) in SEED_RATES.items()}


def sign(key: str, figures: tuple[int, ...], reason: str = "Explained") -> SignOff:
    return SignOff(key, figures, reason, proposed_by="staff-1", approved_by="staff-2")


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


# UGX 1,000,000 at 12.5% from 10 Oct to 13 Nov is 34 days: 11,643 before tax, 1,746 tax.
# UGX 2,500,000 over the same days: 29,109 before tax, 4,366 tax.
# $600.00 at 7.25% with 5% tax for 34 days: $4.05 before tax, $0.20 tax.
def trial(**second: str) -> TrialReport:
    changes = {"principal": "2,500,000", "interest_before_tax": "29,109", "tax_withheld": "4,366"}
    return run_trial(
        clients(client("1"), client("2")),
        investments(
            investment("A", "1"),
            investment("B", "1", **(changes | second)),
            investment("C", "2", **DOLLARS),
            investment("D", "2", **PENDING),
        ),
        as_of=AS_OF,
    )


OLD = ControlTotals(
    clients=2,
    verified_identities=2,
    running_investments=3,
    pending_orders=1,
    invested=(ugx(3_500_000), Money(600_00, Currency.USD)),
    interest_before_tax=(ugx(40_752), Money(405, Currency.USD)),
)


def test_the_worked_figures_are_what_the_formula_gives() -> None:
    assert accrued(Terms(ugx(1_000_000), 1250, 1500), 34).gross == ugx(11_643)
    assert accrued(Terms(ugx(2_500_000), 1250, 1500), 34).gross == ugx(29_109)
    assert accrued(Terms(Money(600_00, Currency.USD), 725, 500), 34).gross.minor == 405


def test_everything_agrees() -> None:
    report = trial()
    check = check_finances(report, OLD, PRODUCTS, TAX)

    assert [(line.key, line.old, line.new) for line in check.lines] == [
        ("total:clients", 2, 2),
        ("total:verified_identities", 2, 2),
        ("total:running_investments", 3, 3),
        ("total:pending_orders", 1, 1),
        ("total:invested:UGX", 3_500_000, 3_500_000),
        ("total:invested:USD", 600_00, 600_00),
        ("total:interest_before_tax:UGX", 40_752, 40_752),
        ("total:interest_before_tax:USD", 405, 405),
    ]
    assert all(line.agrees for line in check.lines)
    assert check.interest_differences == ()
    assert check.needs_sign_off == ()
    assert switch_blockers(report, check, []) == []


def test_each_running_investment_is_opened_by_the_formula_with_its_original_terms() -> None:
    check = check_finances(trial(), OLD, PRODUCTS, TAX)

    assert [o.legacy_investment_ref for o in check.openings] == ["A", "B", "C"]
    first = check.openings[0]
    assert first.legacy_client_ref == "1"
    assert first.terms == Terms(ugx(1_000_000), 1250, 1500)
    assert (first.start_date, first.opened_on, first.days_earned) == (date(2026, 10, 10), AS_OF, 34)
    assert (first.interest.gross, first.interest.tax, first.interest.net) == (
        ugx(11_643),
        ugx(1_746),
        ugx(9_897),
    )
    # One debit equals the three credits: holding, accrued interest after tax, and tax.
    assert first.opening_balance == first.terms.principal + first.interest.net + first.interest.tax


def test_an_investment_past_maturity_opens_fully_accrued_and_no_more() -> None:
    report = run_trial(
        clients(client("1")),
        investments(
            investment(
                "A",
                "1",
                start_date="2026-08-01",
                maturity_date="2026-10-31",
                interest_before_tax="31,164",
                tax_withheld="4,674",
            )
        ),
        as_of=AS_OF,
    )
    (opening,) = check_finances(report, replace(OLD), PRODUCTS, TAX).openings
    assert opening.days_earned == 91
    assert opening.interest.net == ugx(26_490)  # the worked example in docs/investing.md


def test_an_investment_that_starts_on_the_as_of_date_has_earned_nothing() -> None:
    row = run_trial(
        clients(client("1")),
        investments(
            investment(
                "A",
                "1",
                start_date="2026-11-13",
                maturity_date="2027-02-12",
                interest_before_tax="0",
                tax_withheld="0",
            )
        ),
        as_of=AS_OF,
    ).investments[0]
    opening = opening_for(row, PRODUCTS["TREA-91"], opened_on=AS_OF)
    assert opening.days_earned == 0
    assert opening.interest.gross.is_zero
    assert opening.opening_balance == ugx(1_000_000)


def test_an_interest_figure_that_differs_from_the_formula_is_listed_and_blocks() -> None:
    report = trial(interest_before_tax="29,200")
    check = check_finances(
        report,
        replace(OLD, interest_before_tax=(ugx(40_843), OLD.interest_before_tax[1])),
        PRODUCTS,
        TAX,
    )

    (difference,) = check.interest_differences
    assert (difference.row, difference.key) == (3, "investment:B")
    assert (difference.old_interest, difference.new_interest) == (ugx(29_200), ugx(29_109))
    assert difference.difference == ugx(91)
    line = next(line for line in check.lines if line.key == "total:interest_before_tax:UGX")
    assert (line.old, line.new, line.difference, line.agrees) == (40_843, 40_752, 91, False)
    # The ledger opens with the formula, never with the old figure.
    assert check.openings[1].interest.gross == ugx(29_109)

    assert check.needs_sign_off == ("total:interest_before_tax:UGX", "investment:B")
    assert switch_blockers(report, check, []) == [
        (Blocker.DIFFERENCE_NOT_SIGNED_OFF, "total:interest_before_tax:UGX"),
        (Blocker.DIFFERENCE_NOT_SIGNED_OFF, "investment:B"),
    ]
    signed = [
        sign("total:interest_before_tax:UGX", (40_843, 40_752), "Explained by investment B"),
        sign("investment:B", (29_200, 29_109, 4_366, 4_366), "1.0 counted the start date"),
    ]
    assert switch_blockers(report, check, signed) == []
    assert switch_blockers(report, check, signed[:1]) == [
        (Blocker.DIFFERENCE_NOT_SIGNED_OFF, "investment:B")
    ]


def test_a_tax_figure_that_differs_is_listed_and_a_missing_one_is_not() -> None:
    check = check_finances(trial(tax_withheld="4,367"), OLD, PRODUCTS, TAX)
    (difference,) = check.interest_differences
    assert (difference.old_tax, difference.new_tax) == (ugx(4_367), ugx(4_366))
    assert difference.difference.is_zero

    assert check_finances(trial(tax_withheld=""), OLD, PRODUCTS, TAX).interest_differences == ()


def test_counts_and_money_that_differ_are_lines_to_sign_off() -> None:
    old = replace(OLD, clients=3, running_investments=4, invested=(ugx(4_000_000),))
    check = check_finances(trial(), old, PRODUCTS, TAX)

    assert check.needs_sign_off == (
        "total:clients",
        "total:running_investments",
        "total:invested:UGX",
        "total:invested:USD",  # the old system reported no dollars at all
    )
    by_key = {line.key: line for line in check.lines}
    assert by_key["total:invested:UGX"].difference == 500_000
    assert (by_key["total:invested:USD"].old, by_key["total:invested:USD"].new) == (0, 600_00)
    assert by_key["total:invested:USD"].figure is Figure.INVESTED


def test_an_import_with_errors_blocks_the_switch_whatever_is_signed() -> None:
    report = run_trial(clients(client("1")), investments(investment("A", "404")), as_of=AS_OF)
    old = ControlTotals(1, 1, 0, 0, (), ())
    check = check_finances(report, old, PRODUCTS, TAX)
    assert switch_blockers(report, check, []) == [(Blocker.IMPORT_HAS_ERRORS, None)]


def test_a_sign_off_for_something_that_no_longer_differs_is_refused() -> None:
    report = trial()
    check = check_finances(report, OLD, PRODUCTS, TAX)
    stale = [sign("investment:B", (29_200, 29_109, 4_366, 4_366), "Explained last week")]
    assert switch_blockers(report, check, stale) == [(Blocker.SIGN_OFF_FOR_NOTHING, "investment:B")]


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_a_sign_off_needs_a_reason(reason: str) -> None:
    with pytest.raises(InvalidFinanceCheck):
        sign("total:clients", (3, 2), reason)


def test_control_totals_refuse_nonsense() -> None:
    with pytest.raises(InvalidFinanceCheck):
        replace(OLD, clients=-1)
    with pytest.raises(InvalidFinanceCheck):
        replace(OLD, clients=True)
    with pytest.raises(InvalidFinanceCheck):
        replace(OLD, invested=(ugx(1), ugx(2)))
    with pytest.raises(InvalidFinanceCheck):
        replace(OLD, interest_before_tax=(ugx(-1),))


def test_an_order_awaiting_payment_is_never_opened() -> None:
    pending = next(i for i in trial().investments if i.legacy_investment_ref == "D")
    with pytest.raises(InvalidFinanceCheck):
        opening_for(pending, PRODUCTS["TREA-91"], opened_on=AS_OF)


def test_stated_interest_far_from_the_terms_can_never_reach_the_ledger() -> None:
    report = trial(interest_before_tax="99,999,999,999", tax_withheld="")
    check = check_finances(report, OLD, PRODUCTS, TAX)

    assert check.interest_differences[0].key == "investment:B"
    assert check.openings[1].interest.gross == ugx(29_109)
    assert (Blocker.DIFFERENCE_NOT_SIGNED_OFF, "investment:B") in switch_blockers(report, check, [])


def test_a_tax_rate_that_is_not_the_products_needs_a_sign_off() -> None:
    report = trial(tax_percent="10", tax_withheld="")
    check = check_finances(report, OLD, PRODUCTS, TAX)

    (difference,) = check.tax_rate_differences
    assert (difference.row, difference.stated_tax_bps, difference.product_tax_bps) == (
        3,
        1000,
        1500,
    )
    assert check.needs_sign_off == ("tax_rate:B",)
    assert switch_blockers(report, check, []) == [(Blocker.DIFFERENCE_NOT_SIGNED_OFF, "tax_rate:B")]


def test_each_difference_keeps_its_sheet_row_and_the_currency_of_its_figures() -> None:
    interest = check_investments(trial(interest_before_tax="29,200"), PRODUCTS, TAX)
    assert interest.placed == {"investment:B": DifferencePlace(3, Currency.UGX)}

    # A tax rate is in basis points, so it has no currency.
    rate = check_investments(trial(tax_percent="10", tax_withheld=""), PRODUCTS, TAX)
    assert rate.placed == {"tax_rate:B": DifferencePlace(3, None)}

    assert check_investments(trial(), PRODUCTS, TAX).placed == {}


def test_a_sign_off_counts_only_for_the_figures_it_was_written_for() -> None:
    small = trial(interest_before_tax="29,119", tax_withheld="")
    old = replace(OLD, interest_before_tax=(ugx(40_762), OLD.interest_before_tax[1]))
    check = check_finances(small, old, PRODUCTS, TAX)
    assert check.differing == {
        "total:interest_before_tax:UGX": (40_762, 40_752),
        "investment:B": (29_119, 29_109, -1, 4_366),
    }
    signed = [
        sign("total:interest_before_tax:UGX", (40_762, 40_752)),
        sign("investment:B", (29_119, 29_109, -1, 4_366), "Ten shillings of rounding"),
    ]
    assert switch_blockers(small, check, signed) == []

    # The file is exported again and the same investment is now out by ten million.
    large = trial(interest_before_tax="10,029,109", tax_withheld="")
    old = replace(OLD, interest_before_tax=(ugx(40_762), OLD.interest_before_tax[1]))
    check = check_finances(large, old, PRODUCTS, TAX)
    assert switch_blockers(large, check, signed) == [
        (Blocker.DIFFERENCE_NOT_SIGNED_OFF, "investment:B"),
        (Blocker.SIGN_OFF_OUT_OF_DATE, "investment:B"),
    ]


def test_a_sign_off_is_out_of_date_when_the_new_side_changes_too() -> None:
    report = trial(tax_percent="10", tax_withheld="")
    check = check_finances(report, OLD, PRODUCTS, {**TAX, "TREA-91": 2000})
    signed = [sign(key, figures) for key, figures in check.differing.items()]
    assert switch_blockers(report, check, signed) == []

    again = check_finances(report, OLD, PRODUCTS, TAX)
    assert (Blocker.SIGN_OFF_OUT_OF_DATE, "tax_rate:B") in switch_blockers(report, again, signed)


def test_a_sign_off_needs_a_second_member_of_staff_and_its_figures() -> None:
    with pytest.raises(InvalidFinanceCheck, match="second member"):
        SignOff("total:clients", (3, 2), "Explained", proposed_by="staff-1", approved_by="staff-1")
    with pytest.raises(InvalidFinanceCheck):
        SignOff("total:clients", (), "Explained", proposed_by="staff-1", approved_by="staff-2")
    with pytest.raises(InvalidFinanceCheck):
        SignOff("total:clients", (3, 2), "Explained", proposed_by="staff-1", approved_by="")


def test_the_investments_are_checked_without_the_old_totals() -> None:
    report = trial(interest_before_tax="29,200")
    checked = check_investments(report, PRODUCTS, TAX)

    assert [opening.legacy_investment_ref for opening in checked.openings] == ["A", "B", "C"]
    assert checked.differing == {"investment:B": (29_200, 29_109, 4_366, 4_366)}
    # The new side of the interest line is what will be opened, not what the sheet states.
    assert new_totals(report, checked) == {
        "total:clients": 2,
        "total:verified_identities": 2,
        "total:running_investments": 3,
        "total:pending_orders": 1,
        "total:invested:UGX": 3_500_000,
        "total:invested:USD": 600_00,
        "total:interest_before_tax:UGX": 40_752,
        "total:interest_before_tax:USD": 405,
    }


def test_a_product_with_no_tax_rate_yet_is_compared_with_nothing() -> None:
    assert check_investments(trial(), PRODUCTS, {}).tax_rate_differences == ()


def test_stored_totals_give_the_same_lines_as_the_trial_itself() -> None:
    report = trial()
    checked = check_investments(report, PRODUCTS, TAX)

    assert total_lines(OLD, new_totals(report, checked)) == (
        check_finances(report, OLD, PRODUCTS, TAX).lines
    )
    # A currency only one side has still gets a line.
    lines = total_lines(replace(OLD, invested=(ugx(3_500_000),)), {"total:invested:USD": 5})
    assert [(line.key, line.old, line.new) for line in lines if line.new or "USD" in line.key] == [
        ("total:invested:USD", 0, 5),
        ("total:interest_before_tax:USD", 405, 0),
    ]
