"""The daily journal for the accounting system, as a CSV file. Pure.

One journal per day, one line per account code that moved that day, with that day's debits and
credits to it. A client's accounts are added into their code, so the file names no client.
Only days the ledger is closed through are exported, so a journal never changes after it is
sent. Amounts are written in the major unit with exactly the currency's decimals, from
integers, never through a float.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from app.shared.money import Currency, Money

MAX_DAYS = 31
HEADER = (
    "Date",
    "Journal",
    "Account code",
    "Account name",
    "Kind",
    "Currency",
    "Debit",
    "Credit",
)
# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


class ExportRefused(Exception):
    """Days that cannot be exported, with a code the API passes on."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class JournalLine:
    effective_date: date
    code: str
    name: str
    kind: str
    debits: Money
    credits: Money


def check_days(date_from: date, date_to: date, closed_through: date | None) -> None:
    """Refuse days that are not all closed, run backwards, or span more than MAX_DAYS."""
    if date_to < date_from:
        raise ExportRefused("days_backwards")
    if (date_to - date_from).days >= MAX_DAYS:
        raise ExportRefused("too_many_days")
    if closed_through is None or date_to > closed_through:
        raise ExportRefused("day_not_closed")


def journal_reference(currency: Currency, day: date) -> str:
    """The same day always has the same reference, so the accounting system can refuse a
    journal it was already given."""
    return f"FINVEST-{currency.value}-{day.strftime('%Y%m%d')}"


def debit_total(lines: list[JournalLine]) -> int:
    return sum(line.debits.minor for line in lines)


def balanced(lines: list[JournalLine]) -> bool:
    """Every day's debits equal its credits. The ledger guarantees it; the export checks."""
    days: dict[date, int] = {}
    for line in lines:
        days[line.effective_date] = (
            days.get(line.effective_date, 0) + line.debits.minor - line.credits.minor
        )
    return all(total == 0 for total in days.values())


def _amount(money: Money) -> str:
    return money.to_major()


def safe_cell(text: str) -> str:
    """The text, with a leading quote when a spreadsheet would run it as a formula."""
    value = text.strip()
    return f"'{value}" if value.startswith(_FORMULA_STARTS) else value


def journal_csv(currency: Currency, lines: list[JournalLine]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for line in lines:
        writer.writerow(
            (
                line.effective_date.isoformat(),
                journal_reference(currency, line.effective_date),
                safe_cell(line.code),
                safe_cell(line.name),
                line.kind,
                currency.value,
                _amount(line.debits),
                _amount(line.credits),
            )
        )
    return out.getvalue()
