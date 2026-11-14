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


def test_a_transfer_moved_to_the_right_client_is_their_payment_and_the_others_correction() -> None:
    arrived = activity_line(change("payment.deposit_reassigned", 1_000, "move-1"), NAMES)
    left = activity_line(change("payment.deposit_reassigned", -1_000, "move-1"), NAMES)
    assert arrived is not None and left is not None
    assert (arrived.kind, arrived.description) == (
        ActivityKind.PAID_IN,
        "Payment received, Bank transfer",
    )
    assert (left.kind, left.description) == (ActivityKind.CORRECTION, "Correction")


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


M = InvestmentStatus.MATURED


@pytest.mark.parametrize(
    ("matures", "rolled_into", "payout", "value", "label"),
    [
        (date(2026, 11, 20), "inv-2", None, 0, "Matured and reinvested"),
        (date(2026, 11, 20), None, "pay-1", 0, "Matured and paid out"),
        (date(2026, 11, 20), None, "pay-1", 5, "Matured, being paid out"),
        (date(2026, 11, 20), None, None, 5, "Matured, waiting to be paid out"),
        # Matured after the period ended: it was still running at the end of the period.
        (date(2026, 12, 5), "inv-2", None, 5, "Running"),
    ],
)
def test_a_matured_investment_reads_as_it_stood_at_the_end_of_the_period(
    matures: date, rolled_into: str | None, payout: str | None, value: int, label: str
) -> None:
    facts = InvestmentFacts(
        "inv-1", "91 Day Treasury Bill", ugx(5), M, ON, matures, rolled_into, payout
    )
    period = Period(date(2026, 11, 1), date(2026, 11, 30))
    assert holding_status(facts, period=period, value_at_end=ugx(value)) == label


W = InvestmentStatus.WITHDRAWN


@pytest.mark.parametrize(
    ("withdrawn_on", "payout", "value", "label"),
    [
        (date(2026, 11, 10), None, 5, "Withdrawn early, waiting to be paid out"),
        (date(2026, 11, 10), "pay-1", 5, "Withdrawn early, being paid out"),
        (date(2026, 11, 10), "pay-1", 0, "Withdrawn early and paid out"),
        (date(2026, 11, 30), None, 5, "Withdrawn early, waiting to be paid out"),
        # Withdrawn after the period ended: it was still running at the end of the period.
        (date(2026, 12, 2), "pay-1", 5, "Running"),
    ],
)
def test_an_early_withdrawal_reads_as_it_stood_at_the_end_of_the_period(
    withdrawn_on: date, payout: str | None, value: int, label: str
) -> None:
    facts = InvestmentFacts(
        "inv-1",
        "91 Day Treasury Bill",
        ugx(5),
        W,
        ON,
        date(2027, 2, 1),
        withdrawn_on=withdrawn_on,
        withdrawal_payout_id=payout,
    )
    period = Period(date(2026, 11, 1), date(2026, 11, 30))
    assert holding_status(facts, period=period, value_at_end=ugx(value)) == label
