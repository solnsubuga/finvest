"""Finance rules: Finvest's income and costs from the books, and the daily journal file."""

from datetime import date

import pytest

from app.modules.finance.api import next_export
from app.modules.finance.domain.books import (
    BadMonth,
    CodeLine,
    income_and_costs,
    month_end,
    month_label,
    parse_month,
)
from app.modules.finance.domain.journal import (
    MAX_DAYS,
    ExportRefused,
    JournalLine,
    balanced,
    check_days,
    debit_total,
    journal_csv,
    journal_reference,
)
from app.shared.money import Currency, Money

UGX = Currency.UGX
USD = Currency.USD


def ugx(minor: int) -> Money:
    return Money(minor, UGX)


def line(code: str, kind: str, opening: int, debits: int, credits: int) -> CodeLine:
    sign = 1 if kind in ("asset", "expense") else -1
    return CodeLine(
        code,
        kind,
        code.title(),
        ugx(opening),
        ugx(debits),
        ugx(credits),
        ugx(opening + sign * (debits - credits)),
    )


def test_months_are_written_year_dash_month() -> None:
    assert parse_month("2026-11") == date(2026, 11, 1)
    assert month_label(date(2026, 2, 1)) == "2026-02"
    assert month_end(date(2026, 2, 1)) == date(2026, 2, 28)
    assert month_end(date(2028, 2, 1)) == date(2028, 2, 29)
    assert month_end(date(2026, 12, 1)) == date(2026, 12, 31)
    for bad in ("2026-13", "2026-00", "1999-12", "2026-1", "26-11", "2026-11-01"):
        with pytest.raises(BadMonth):
            parse_month(bad)


def test_income_and_costs_come_only_from_income_and_expense_codes() -> None:
    books = [
        line("bank.trust", "asset", 1_000_000, 500_000, 200_000),
        line("client.holdings", "liability", 900_000, 100_000, 400_000),
        line("dealing.bill_income", "income", 40_000, 0, 12_000),
        line("dealing.sale_gains", "income", 0, 0, 0),
        line("investments.interest_expense", "expense", 30_000, 9_000, 0),
        line("fees.provider.iotec", "expense", 0, 1_500, 0),
    ]
    found = income_and_costs(UGX, books)
    assert [(i.code, i.amount) for i in found.income] == [("dealing.bill_income", ugx(12_000))]
    assert [(c.code, c.amount) for c in found.costs] == [
        ("investments.interest_expense", ugx(9_000)),
        ("fees.provider.iotec", ugx(1_500)),
    ]
    assert (found.income_total, found.cost_total, found.net) == (
        ugx(12_000),
        ugx(10_500),
        ugx(1_500),
    )


def test_a_reversed_income_shows_as_negative_and_a_loss_month_nets_below_zero() -> None:
    found = income_and_costs(
        UGX,
        [
            line("dealing.bill_income", "income", 40_000, 5_000, 0),
            line("investments.interest_expense", "expense", 0, 9_000, 0),
        ],
    )
    assert found.income[0].amount == ugx(-5_000)
    assert found.net == ugx(-14_000)


def test_nothing_moved_means_zero_totals_in_the_currency() -> None:
    found = income_and_costs(USD, [])
    assert (found.income, found.costs) == ((), ())
    assert found.net == Money(0, USD)


def test_only_closed_days_up_to_a_month_at_a_time_are_exported() -> None:
    closed = date(2026, 11, 12)
    check_days(date(2026, 11, 12), closed, closed)
    check_days(date(2026, 10, 13), closed, closed)
    cases = [
        (date(2026, 11, 13), date(2026, 11, 13), closed, "day_not_closed"),
        (date(2026, 11, 1), date(2026, 11, 1), None, "day_not_closed"),
        (date(2026, 11, 12), date(2026, 11, 11), closed, "days_backwards"),
        (date(2026, 10, 12), closed, closed, "too_many_days"),
    ]
    for date_from, date_to, through, code in cases:
        with pytest.raises(ExportRefused) as refused:
            check_days(date_from, date_to, through)
        assert refused.value.code == code
    assert MAX_DAYS == 31


def test_the_next_export_starts_after_the_last_day_sent() -> None:
    assert next_export(None, None) is None
    first = next_export(date(2026, 11, 12), None)
    assert first is not None and (first.date_from, first.date_to) == (
        date(2026, 11, 12),
        date(2026, 11, 12),
    )
    after = next_export(date(2026, 11, 12), date(2026, 11, 10))
    assert after is not None and (after.date_from, after.date_to) == (
        date(2026, 11, 11),
        date(2026, 11, 12),
    )
    assert next_export(date(2026, 11, 12), date(2026, 11, 12)) is None
    far = next_export(date(2026, 12, 31), date(2026, 10, 1))
    assert far is not None and (far.date_from, far.date_to) == (
        date(2026, 10, 2),
        date(2026, 11, 1),
    )


def journal_line(day: date, code: str, debits: int, credits: int, *, name: str = "") -> JournalLine:
    kind = "asset" if code.startswith("bank") else "liability"
    return JournalLine(day, code, name or code, kind, ugx(debits), ugx(credits))


def test_the_journal_file_has_one_line_per_code_and_day_in_major_units() -> None:
    day = date(2026, 11, 12)
    lines = [
        journal_line(day, "bank.trust", 250_000, 0, name="Trust bank account"),
        journal_line(day, "client.awaiting_start", 0, 250_000, name="Paid in, awaiting start"),
    ]
    content = journal_csv(UGX, lines)
    rows = content.split("\r\n")
    assert rows[0] == "Date,Journal,Account code,Account name,Kind,Currency,Debit,Credit"
    assert (
        rows[1]
        == "2026-11-12,FINVEST-UGX-20261112,bank.trust,Trust bank account,asset,UGX,250000,0"
    )
    assert rows[2] == (
        '2026-11-12,FINVEST-UGX-20261112,client.awaiting_start,"Paid in, awaiting start",'
        "liability,UGX,0,250000"
    )
    assert rows[3] == ""
    assert debit_total(lines) == 250_000
    assert balanced(lines)


def test_dollar_amounts_keep_their_cents_and_names_cannot_run_as_formulas() -> None:
    day = date(2026, 11, 12)
    lines = [
        JournalLine(day, "bank.trust", "=HYPERLINK()", "asset", Money(12_345, USD), Money(5, USD)),
    ]
    row = journal_csv(USD, lines).split("\r\n")[1]
    assert row == "2026-11-12,FINVEST-USD-20261112,bank.trust,'=HYPERLINK(),asset,USD,123.45,0.05"
    assert journal_reference(USD, day) == "FINVEST-USD-20261112"


def test_a_day_whose_debits_and_credits_differ_is_caught() -> None:
    day, next_day = date(2026, 11, 12), date(2026, 11, 13)
    assert not balanced([journal_line(day, "bank.trust", 100, 0)])
    assert not balanced(
        [journal_line(day, "bank.trust", 100, 0), journal_line(next_day, "client.x", 0, 100)]
    )
    assert balanced([])
