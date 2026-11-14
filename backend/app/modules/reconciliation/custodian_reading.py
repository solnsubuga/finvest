"""Turning an uploaded statement into something a reader can read, and the reader that needs no
account.

A PDF or a photo goes to the reader as it is. A CSV or a spreadsheet is turned into plain text
here, one line per row with its row number, so the reader can say exactly where each holding
came from. Nothing here interprets an amount: cells are copied as text.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import load_workbook

from app.modules.reconciliation.domain.custodian import (
    XLSX,
    AmountScale,
    ReaderRefused,
    Reading,
    ReadRow,
    StatementFile,
)

# A sheet longer than this is not a holdings statement. The reader refuses rather than cut it.
MAX_SHEET_ROWS = 5_000
MAX_COLUMNS = 40
MAX_TEXT_CHARACTERS = 1_500_000
# Integers above this cannot be held exactly by a spreadsheet's floating point cells.
_EXACT_LIMIT = 2**53


def as_text(file: StatementFile) -> str:
    """The statement as numbered lines of text, for a CSV or a spreadsheet.

    Refused with `statement_not_text`, `statement_not_a_spreadsheet` or `statement_too_long`.
    """
    if file.content_type == "text/csv":
        lines = _csv_lines(file.data)
    elif file.content_type == XLSX:
        lines = _sheet_lines(file.data)
    else:
        raise ValueError("Only a CSV or a spreadsheet is turned into text")
    text = "\n".join(lines)
    if len(text) > MAX_TEXT_CHARACTERS:
        raise ReaderRefused("statement_too_long")
    return text


def _csv_lines(data: bytes) -> list[str]:
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            # Excel on Windows saves CSV in its own code page.
            decoded = data.decode("cp1252")
        except UnicodeDecodeError:
            raise ReaderRefused("statement_not_text") from None
    lines = []
    for number, row in enumerate(csv.reader(io.StringIO(decoded)), start=1):
        if number > MAX_SHEET_ROWS:
            raise ReaderRefused("statement_too_long")
        cells = [cell.strip() for cell in row[:MAX_COLUMNS]]
        if any(cells):
            lines.append(f"Row {number}: " + " | ".join(cells))
    return lines


def _sheet_lines(data: bytes) -> list[str]:
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        # openpyxl raises many kinds of error for a damaged or disguised file.
        raise ReaderRefused("statement_not_a_spreadsheet") from None
    lines: list[str] = []
    try:
        for sheet in book.worksheets:
            for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if number > MAX_SHEET_ROWS:
                    raise ReaderRefused("statement_too_long")
                cells = [_cell(value) for value in row[:MAX_COLUMNS]]
                if any(cells):
                    lines.append(f"Sheet {sheet.title} row {number}: " + " | ".join(cells))
    finally:
        book.close()
    return lines


def _cell(value: object) -> str:
    """A cell as text. Whole numbers are written out in full; a number a spreadsheet cannot hold
    exactly is marked, so it is shown to staff as unreadable instead of being rounded."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < _EXACT_LIMIT:
            return str(int(value))
        return "[number not exact]"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else str(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


@dataclass(frozen=True)
class DevelopmentStubReader:
    """Reads a CSV laid out for it, so the whole check can be tried without an API key.

    The CSV has a header with `instrument`, `code`, `face_value`, `currency` and
    `maturity_date`, and may have `statement_date`. Anything else is refused. It reads nothing
    real, so it may not run in production.
    """

    name: str = "development-stub"

    async def read(self, file: StatementFile, *, key: str) -> Reading:
        if file.content_type != "text/csv":
            raise ReaderRefused("stub_reads_csv_only")
        try:
            decoded = file.data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ReaderRefused("statement_not_text") from None
        table = csv.DictReader(io.StringIO(decoded))
        needed = {"instrument", "face_value", "maturity_date"}
        if table.fieldnames is None or not needed <= set(table.fieldnames):
            raise ReaderRefused("stub_layout_unknown")
        rows = []
        statement_date: str | None = None
        for number, line in enumerate(table, start=2):
            statement_date = statement_date or (line.get("statement_date") or "").strip() or None
            text = " | ".join((value or "").strip() for value in line.values() if value)
            rows.append(
                ReadRow(
                    instrument=(line.get("instrument") or "").strip(),
                    code=(line.get("code") or "").strip() or None,
                    face_value=(line.get("face_value") or "").strip(),
                    currency=(line.get("currency") or "").strip() or None,
                    matures_on=(line.get("maturity_date") or "").strip() or None,
                    location=f"Row {number}",
                    source_text=text[:300],
                )
            )
        return Reading(
            statement_date=statement_date,
            custodian_name="Development stub",
            account_reference=None,
            amount_scale=AmountScale.ONE,
            rows=tuple(rows),
            reader=self.name,
        )
