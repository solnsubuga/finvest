"""A statement as an Excel workbook: the same figures as its PDF, in cells a client can sort.

The worker writes it from the same drawn `Statement` as the PDF, in the same transaction, so
the two never disagree. It is a convenience for the client's own records. It is not signed and
carries no check: the PDF is the statement a bank or embassy accepts, and the workbook says so.

Amounts are numbers in the currency's major unit, written from `Money.to_major` as exact
decimals, never through a float. Text from elsewhere (product names, references) is always
written as text, so a name that starts with "=" can never become a formula.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from app.modules.documents.domain import formatting
from app.modules.documents.domain.checks import shown as shown_code
from app.modules.documents.domain.statements import ActivityKind, Statement
from app.shared.money import Currency, Money

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_BOLD = Font(bold=True)
_TITLE = Font(bold=True, size=14)
_FORMATS = {Currency.UGX: "#,##0", Currency.USD: "#,##0.00"}
_DAY = "d mmm yyyy"

ACTIVITY_NAMES = {
    ActivityKind.PAID_IN: "Paid in",
    ActivityKind.EARNED: "Interest, after tax",
    ActivityKind.PAID_OUT: "Paid out",
    ActivityKind.CORRECTION: "Correction",
}


def statement_workbook(statement: Statement) -> bytes:
    """The workbook: a Summary sheet, then Your investments, then What happened."""
    book = Workbook()
    summary = book.active
    assert summary is not None
    summary.title = "Summary"
    _summary(summary, statement)
    _holdings(book.create_sheet("Your investments"), statement)
    _activity(book.create_sheet("What happened"), statement)
    book.properties.title = f"Finvest statement, {statement.period.label}"
    book.properties.creator = "Finvest"
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def _text(cell: Cell | MergedCell, value: str) -> Cell | MergedCell:
    cell.value = value
    cell.data_type = "s"  # never a formula, whatever the text starts with
    return cell


def _money(cell: Cell | MergedCell, value: Money) -> Cell | MergedCell:
    cell.value = Decimal(value.to_major())
    cell.number_format = _FORMATS[value.currency]
    return cell


class _Rows:
    """Writes one row after another, counting blank rows too."""

    def __init__(self, sheet: Worksheet) -> None:
        self.sheet = sheet
        self.at = 0

    def add(self, *values: str | Money | date | None, bold: bool = False) -> None:
        self.at += 1
        for column, value in enumerate(values, start=1):
            cell = self.sheet.cell(row=self.at, column=column)
            if isinstance(value, Money):
                _money(cell, value)
            elif isinstance(value, date):
                cell.value = value
                cell.number_format = _DAY
            elif value is not None:
                _text(cell, value)
            if bold:
                cell.font = _BOLD


def _summary(sheet: Worksheet, statement: Statement) -> None:
    rows = _Rows(sheet)
    rows.add(f"Finvest statement, {statement.period.label}")
    sheet["A1"].font = _TITLE
    rows.add()
    rows.add("Prepared for", statement.client_name)
    rows.add("Account number", formatting.account_number(statement.account_number))
    rows.add("Statement", statement.reference)
    if statement.check_code is not None:
        rows.add("Check code", shown_code(statement.check_code))
    rows.add(
        "Period",
        f"{formatting.day(statement.period.start)} to {formatting.day(statement.period.end)}",
    )
    rows.add("Issued", formatting.day(statement.issued_on))
    if statement.not_final_from is not None:
        rows.add(
            "Not final",
            f"Figures from {formatting.day(statement.not_final_from)} may still change until "
            "our books for those days are closed.",
        )
    for summary in statement.summaries:
        rows.add()
        rows.add(f"In {summary.currency.value}", bold=True)
        rows.add(f"Value on {formatting.day(statement.period.start)}", summary.opening)
        rows.add("Paid in", summary.paid_in)
        rows.add("Interest earned, after tax", summary.earned)
        rows.add("Paid out", -summary.paid_out)
        if not summary.corrections.is_zero:
            rows.add("Corrections", summary.corrections)
        rows.add(f"Value on {formatting.day(statement.period.end)}", summary.closing, bold=True)
    rows.add()
    rows.add(
        "All figures are after tax. This workbook is a copy of your statement for your own "
        "records. Banks and embassies accept the signed PDF, which carries the check code."
    )
    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions["B"].width = 24


def _holdings(sheet: Worksheet, statement: Statement) -> None:
    rows = _Rows(sheet)
    rows.add(
        "Investment",
        "Reference",
        "Status",
        "Started",
        "Matures",
        "Currency",
        "Invested",
        "Earned in period",
        "Value at end",
        bold=True,
    )
    for holding in statement.holdings:
        rows.add(
            holding.product,
            holding.reference,
            holding.status,
            holding.started_on,
            holding.matures_on,
            holding.invested.currency.value,
            holding.invested,
            holding.earned_in_period,
            holding.value_at_end,
        )
    if not statement.holdings:
        rows.add("You held no investments in this period.")
    _layout(sheet, (28, 18, 20, 13, 13, 10, 15, 17, 15))


def _activity(sheet: Worksheet, statement: Statement) -> None:
    rows = _Rows(sheet)
    rows.add("Date", "Type", "Description", "Reference", "Currency", "Amount", bold=True)
    for line in statement.activity:
        rows.add(
            line.on,
            ACTIVITY_NAMES[line.kind],
            line.description,
            line.reference,
            line.amount.currency.value,
            line.amount,
        )
    if not statement.activity:
        rows.add("Nothing was paid in, earned or paid out in this period.")
    _layout(sheet, (13, 20, 40, 18, 10, 15))


def _layout(sheet: Worksheet, widths: tuple[int, ...]) -> None:
    for index, width in enumerate(widths):
        sheet.column_dimensions[chr(ord("A") + index)].width = width
    sheet.freeze_panes = "A2"
