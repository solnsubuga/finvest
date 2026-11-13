from datetime import date

import pytest

from app.modules.documents.domain.building import (
    STATUS_LABELS,
    EntryChange,
    InvestmentFacts,
    Names,
    activity_line,
    holding_status,
    method_name,
    totals_by_currency,
)
from app.modules.documents.domain.statements import ActivityKind, Period
from app.modules.investments.domain.investments import InvestmentStatus
from app.shared.money import Currency, Money

ON = date(2026, 11, 13)
NAMES = Names(
    products={"inv-1": "91 Day Treasury Bill"},
    deposit_methods={"dep-1": "mtn"},
    payout_kinds={"pay-1": "bank"},
)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def change(
    entry_type: str, owed: int, source: str | None = None, refs: tuple[str, ...] = ()
) -> EntryChange:
    return EntryChange("entry-1", ON, entry_type, None, source, ugx(owed), refs)


def test_a_move_between_the_clients_own_accounts_is_not_a_line() -> None:
    assert activity_line(change("investment.started", 0, refs=("inv-1",)), NAMES) is None


def test_a_confirmed_payment_is_money_in_named_by_its_method() -> None:
    line = activity_line(change("payment.deposit_confirmed", 1_000, "dep-1"), NAMES)
    assert line is not None
    assert (line.kind, line.description, line.reference) == (
        ActivityKind.PAID_IN,
        "Payment received, MTN Mobile Money",
        "dep-1",
    )
    unnamed = activity_line(change("payment.deposit_confirmed", 1_000, "dep-2"), NAMES)
    assert unnamed is not None and unnamed.description == "Payment received"


def test_interest_is_earned_on_the_investment_it_was_posted_to() -> None:
    line = activity_line(change("investment.interest_accrued", 7, "run-1", ("inv-1",)), NAMES)
    assert line is not None
    assert (line.kind, line.description, line.reference, line.amount) == (
        ActivityKind.EARNED,
        "Interest, 91 Day Treasury Bill",
        "inv-1",
        ugx(7),
    )


def test_a_payout_is_money_out_and_a_returned_one_is_a_correction() -> None:
    out = activity_line(change("payout.reserve", -500, "pay-1"), NAMES)
    back = activity_line(change("payout.release", 500, "pay-1"), NAMES)
    assert out is not None and back is not None
    assert (out.kind, out.description) == (ActivityKind.PAID_OUT, "Paid out to your bank account")
    assert (back.kind, back.description) == (ActivityKind.CORRECTION, "Payout returned")


def test_anything_else_that_changes_what_the_client_is_owed_is_a_correction() -> None:
    for entry_type, owed in (("ledger.correction", -3), ("payment.deposit_confirmed", -3)):
        line = activity_line(change(entry_type, owed, "x"), NAMES)
        assert line is not None and line.kind is ActivityKind.CORRECTION


def test_ways_of_paying_have_names_a_client_knows() -> None:
    assert method_name("mtn") == "MTN Mobile Money"
    assert method_name("airtel") == "Airtel Money"
    assert method_name("bank_transfer") == "Bank transfer"


def test_every_investment_status_has_a_label() -> None:
    assert set(STATUS_LABELS) == set(InvestmentStatus)


@pytest.mark.parametrize(
    ("status", "matures", "value", "label"),
    [
        (InvestmentStatus.ACTIVE, date(2027, 1, 1), 5, "Running"),
        (InvestmentStatus.ACTIVE, date(2026, 11, 20), 5, "Matured"),
        (InvestmentStatus.ACTIVE, date(2026, 11, 20), 0, "Paid out"),
        (InvestmentStatus.AWAITING_PAYMENT, None, 5, "Payment received, starting"),
    ],
)
def test_a_holding_is_labelled_from_its_status_dates_and_value(
    status: InvestmentStatus, matures: date | None, value: int, label: str
) -> None:
    facts = InvestmentFacts("inv-1", "91 Day Treasury Bill", ugx(5), status, ON, matures)
    period = Period(date(2026, 11, 1), date(2026, 11, 30))
    assert holding_status(facts, period=period, value_at_end=ugx(value)) == label


def test_totals_keep_currencies_apart() -> None:
    totals = totals_by_currency([ugx(5), Money(3, Currency.USD), ugx(-2)])
    assert totals == {Currency.UGX: ugx(3), Currency.USD: Money(3, Currency.USD)}
