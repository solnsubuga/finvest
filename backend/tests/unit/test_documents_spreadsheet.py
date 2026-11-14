"""A statement's Excel copy: the same figures as its PDF, as numbers and dates in cells."""

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from app.modules.documents.domain.statements import (
    Holding,
    Period,
    Statement,
    StatementKind,
    summarise,
)
from app.modules.documents.spreadsheet import statement_workbook
from app.shared.money import Currency, Money
from tests.unit.test_documents_rendering import statement, ugx


def cells(workbook: bytes, sheet: str) -> list[tuple[object, ...]]:
    book = load_workbook(BytesIO(workbook))
    return [tuple(row) for row in book[sheet].iter_rows(values_only=True)]


def test_the_workbook_has_a_summary_the_investments_and_what_happened() -> None:
    drawn = dataclasses.replace(statement(3), check_code="K7Q29XMA4TPD")
    book = load_workbook(BytesIO(statement_workbook(drawn)))
    assert book.sheetnames == ["Summary", "Your investments", "What happened"]

    summary = cells(statement_workbook(drawn), "Summary")
    facts = {row[0]: row[1] for row in summary if row[0] is not None}
    assert facts["Prepared for"] == "Amara Nakato"
    assert facts["Statement"] == "ST-8fK2xQ7m"
    assert facts["Check code"] == "K7Q2-9XMA-4TPD"
    assert facts["Paid in"] == 3_003
    assert facts["Value on 31 Dec 2026"] == 3_003

    holdings = cells(statement_workbook(drawn), "Your investments")
    # Excel keeps a day as a moment at midnight, which is how it reads back.
    assert holdings[1] == (
        "91 Day Treasury Bill",
        "IN-1",
        "Running",
        datetime(2026, 1, 1),
        datetime(2026, 4, 2),
        "UGX",
        1_000_000,
        26_490,
        1_026_490,
    )

    activity = cells(statement_workbook(drawn), "What happened")
    assert activity[0] == ("Date", "Type", "Description", "Reference", "Currency", "Amount")
    assert [row[5] for row in activity[1:]] == [1_000, 1_001, 1_002]
    assert all(row[1] == "Paid in" for row in activity[1:])


def test_the_lines_add_up_to_the_summary() -> None:
    drawn = statement(40)
    activity = cells(statement_workbook(drawn), "What happened")[1:]
    summary = {row[0]: row[1] for row in cells(statement_workbook(drawn), "Summary") if row[0]}
    assert sum(row[5] for row in activity) == summary["Paid in"]  # type: ignore[misc]


def test_cents_are_exact_decimals() -> None:
    usd = Money(1234, Currency.USD)
    drawn = dataclasses.replace(
        statement(0),
        summaries=(summarise(Currency.USD, opening=usd, closing=usd, activity=()),),
        holdings=(
            Holding(
                "Dollar note", "IN-2", None, None, "Running", usd, Money.zero(Currency.USD), usd
            ),
        ),
    )
    holdings = cells(statement_workbook(drawn), "Your investments")
    assert Decimal(str(holdings[1][6])) == Decimal("12.34")
    assert holdings[1][3] is None and holdings[1][4] is None


def test_text_that_looks_like_a_formula_stays_text() -> None:
    drawn = dataclasses.replace(
        statement(0),
        holdings=(
            Holding('=HYPERLINK("x")', "IN-3", None, None, "Running", ugx(1), ugx(0), ugx(1)),
        ),
    )
    book = load_workbook(BytesIO(statement_workbook(drawn)))
    cell = book["Your investments"]["A2"]
    assert cell.value == '=HYPERLINK("x")' and cell.data_type == "s"


def test_an_empty_period_says_so_on_each_sheet() -> None:
    empty = Statement(
        reference="ST-1",
        kind=StatementKind.REQUESTED,
        client_name="Amara Nakato",
        account_number="1001004827",
        period=Period(date(2026, 1, 1), date(2026, 1, 31)),
        issued_on=date(2026, 2, 1),
        summaries=(summarise(Currency.UGX, opening=ugx(0), closing=ugx(0), activity=()),),
        holdings=(),
        activity=(),
    )
    workbook = statement_workbook(empty)
    assert cells(workbook, "Your investments")[1][0] == "You held no investments in this period."
    assert cells(workbook, "What happened")[1][0] == (
        "Nothing was paid in, earned or paid out in this period."
    )
    summary = [row[0] for row in cells(workbook, "Summary")]
    assert "Check code" not in summary and "Not final" not in summary


def test_days_that_are_not_final_are_named() -> None:
    drawn = dataclasses.replace(statement(1), not_final_from=date(2026, 12, 30))
    facts = {row[0]: row[1] for row in cells(statement_workbook(drawn), "Summary") if row[0]}
    assert facts["Not final"] == (
        "Figures from 30 Dec 2026 may still change until our books for those days are closed."
    )
