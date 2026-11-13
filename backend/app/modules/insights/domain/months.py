"""Calendar months for insights. Pure: no I/O.

A month is named by its first day. Figures are grouped by the effective date of their ledger
entries, which is the Kampala business day, so a month here is a Kampala calendar month.
"""

from datetime import date

MAX_MONTHS = 24
DEFAULT_MONTHS = 6


def month_start(day: date) -> date:
    return day.replace(day=1)


def months_ending(today: date, count: int) -> list[date]:
    """The `count` months up to and including the one `today` is in, oldest first."""
    if type(count) is not int or not 1 <= count <= MAX_MONTHS:
        raise ValueError(f"count is 1 to {MAX_MONTHS}")
    year, month = today.year, today.month
    months: list[date] = []
    for _ in range(count):
        months.append(date(year, month, 1))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return months[::-1]


def month_end(start: date) -> date:
    """The last day of the month that begins on `start`."""
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return date.fromordinal(date(year, month, 1).toordinal() - 1)
