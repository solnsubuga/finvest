"""Tax periods. Pure.

URA takes withholding tax monthly: what was withheld in a calendar month is paid and returned by
the 15th of the next month. A client's tax certificate covers Uganda's tax year, 1 July to
30 June. Both are working defaults recorded in docs/decisions.md, to confirm with the owner's
tax adviser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# The day of the following month by which a month's tax is paid and returned.
DUE_DAY = 15
TAX_YEAR_STARTS = 7  # July
_MONTH = re.compile(r"([0-9]{4})-([0-9]{2})")
_TAX_YEAR = re.compile(r"([0-9]{4})-([0-9]{2})")


class BadPeriod(ValueError):
    """Text that does not name a month or a tax year."""


def month_of(day: date) -> date:
    """The month a day falls in, as its first day."""
    return day.replace(day=1)


def next_month(month: date) -> date:
    return date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


def month_end(month: date) -> date:
    return next_month(month) - timedelta(days=1)


def months_back(month: date, count: int) -> date:
    """The month `count` months before `month`."""
    index = month.year * 12 + month.month - 1 - count
    return date(index // 12, index % 12 + 1, 1)


def due_on(month: date) -> date:
    """The day by which a month's tax is paid to URA and its return filed."""
    return next_month(month).replace(day=DUE_DAY)


def parse_month(text: str) -> date:
    """'2026-11' as the first day of that month."""
    found = _MONTH.fullmatch(text)
    if found is None or not 1 <= int(found.group(2)) <= 12 or int(found.group(1)) < 2000:
        raise BadPeriod("A month is written YYYY-MM")
    return date(int(found.group(1)), int(found.group(2)), 1)


def month_label(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


@dataclass(frozen=True, slots=True, order=True)
class TaxYear:
    """Uganda's tax year from 1 July of `starts` to 30 June of the year after."""

    starts: int

    @property
    def first_day(self) -> date:
        return date(self.starts, TAX_YEAR_STARTS, 1)

    @property
    def last_day(self) -> date:
        return date(self.starts + 1, TAX_YEAR_STARTS, 1) - timedelta(days=1)

    @property
    def label(self) -> str:
        """'2026-27', as URA writes it."""
        return f"{self.starts}-{(self.starts + 1) % 100:02d}"

    def contains(self, day: date) -> bool:
        return self.first_day <= day <= self.last_day


def tax_year_of(day: date) -> TaxYear:
    return TaxYear(day.year if day.month >= TAX_YEAR_STARTS else day.year - 1)


def parse_tax_year(text: str) -> TaxYear:
    """'2026-27' as the tax year from 1 July 2026."""
    found = _TAX_YEAR.fullmatch(text)
    if found is None or int(found.group(1)) < 2000:
        raise BadPeriod("A tax year is written YYYY-YY")
    year = TaxYear(int(found.group(1)))
    if year.label != text:
        raise BadPeriod("A tax year is written YYYY-YY")
    return year
