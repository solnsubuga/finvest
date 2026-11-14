"""The reports staff can download, who may download each, and the periods each one takes.
Pure.

A report is a CSV file. It names clients by their Finvest ID only: no names, phone numbers or
account details, so a file passed around inside the team carries as little as it can.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.modules.identity.domain.permissions import Permission
from app.shared.money import Money

ReportKey = Literal[
    "daily_position",
    "deposits_and_withdrawals",
    "interest_and_tax",
    "maturity_ladder",
    "identity_and_risk",
    "client_statements",
    "complaints_quarterly",
]
# `day`: one day. `days`: up to MAX_DAYS days that have begun. `month`: one calendar month.
# `quarter`: one calendar quarter. `ahead`: up to MAX_AHEAD_DAYS days, which may be in the
# future. `now`: today only, for how things stand, which cannot be read for a day gone by.
PeriodKind = Literal["day", "days", "month", "quarter", "ahead", "now"]

MAX_DAYS = 31
MAX_AHEAD_DAYS = 92
# A file longer than this is refused: ask for a shorter period.
MAX_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class Report:
    key: ReportKey
    name: str
    about: str
    period: PeriodKind
    # Needed beside `reports.view`. The owning screen's own permission, so a report never shows
    # a role what its screens would not.
    permission: Permission


REPORTS: dict[ReportKey, Report] = {
    report.key: report
    for report in (
        Report(
            "daily_position",
            "Daily position",
            "Client money by account and currency at the end of a day, with the cash in the "
            "trust account and the ioTec wallet.",
            "day",
            "finance.view",
        ),
        Report(
            "deposits_and_withdrawals",
            "Deposits and withdrawals",
            "Every deposit and payout asked for in the period, by channel, with how it ended "
            "and how long it took.",
            "days",
            "money_movement.view",
        ),
        Report(
            "interest_and_tax",
            "Interest and tax withheld",
            "Interest each investment earned in the month and the tax withheld from it, which "
            "is the tax to pay URA.",
            "month",
            "finance.view",
        ),
        Report(
            "maturity_ladder",
            "Maturity ladder",
            "What matures each day, how much is set to pay out, and how much rolls over.",
            "ahead",
            "maturities.view",
        ),
        Report(
            "identity_and_risk",
            "Identity and risk",
            "Clients by identity check and account state, and the screening and monitoring "
            "cases, as they stand now.",
            "now",
            "compliance.view",
        ),
        Report(
            "client_statements",
            "Client statements",
            "Every client's monthly statement for a month: made, nothing to show, or failed.",
            "month",
            "clients.view",
        ),
        Report(
            "complaints_quarterly",
            "Complaints for the regulator",
            "The quarter's complaints by category: received, resolved, upheld, past their "
            "deadline, and the average time to resolve.",
            "quarter",
            "complaints.view",
        ),
    )
}


def may_download(report: Report, permissions: Iterable[Permission]) -> bool:
    held = set(permissions)
    return "reports.view" in held and report.permission in held


class BadPeriod(Exception):
    """The period does not fit the report. `code` is safe to show."""

    def __init__(self, field: Literal["date_from", "date_to"], code: str) -> None:
        super().__init__(code)
        self.field = field
        self.code = code


def quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def month_end(first: date) -> date:
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return following - timedelta(days=1)


def quarter_end(first: date) -> date:
    month = first.month + 2
    return month_end(date(first.year, month, 1))


def check_period(kind: PeriodKind, date_from: date, date_to: date, *, today: date) -> None:
    """Refuses a period the report cannot be made for."""
    if date_to < date_from:
        raise BadPeriod("date_to", "days_backwards")
    if kind == "ahead":
        if (date_to - date_from).days + 1 > MAX_AHEAD_DAYS:
            raise BadPeriod("date_to", "too_many_days")
        return
    if kind == "now":
        if date_from != today or date_to != today:
            raise BadPeriod("date_from", "today_only")
        return
    if date_from > today:
        raise BadPeriod("date_from", "period_in_future")
    if kind == "day" and date_from != date_to:
        raise BadPeriod("date_to", "one_day_only")
    if kind == "days" and (date_to - date_from).days + 1 > MAX_DAYS:
        raise BadPeriod("date_to", "too_many_days")
    if kind == "month" and (date_from.day != 1 or date_to != month_end(date_from)):
        raise BadPeriod("date_from", "whole_month_only")
    if kind == "quarter" and (
        date_from != quarter_start(date_from) or date_to != quarter_end(date_from)
    ):
        raise BadPeriod("date_from", "whole_quarter_only")


def default_period(kind: PeriodKind, today: date) -> tuple[date, date]:
    """What the screen offers first: the last whole period that has ended, or for days, the
    last seven days; for the ladder, the next four weeks."""
    yesterday = today - timedelta(days=1)
    if kind == "now":
        return today, today
    if kind == "day":
        return yesterday, yesterday
    if kind == "days":
        return today - timedelta(days=6), today
    if kind == "month":
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        return first, month_end(first)
    if kind == "quarter":
        first = quarter_start(quarter_start(today) - timedelta(days=1))
        return first, quarter_end(first)
    return today, today + timedelta(days=27)


_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def cell(value: object) -> str:
    """A safe CSV cell. Text that a spreadsheet would run as a formula starts with `'`. Counts
    and amounts are written as they are, so a negative amount stays a number."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Money):
        # The exact amount in the major unit, from the integer: never through a float.
        return value.to_major()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


@dataclass(frozen=True, slots=True)
class Table:
    header: tuple[str, ...]
    rows: list[tuple[object, ...]]

    def __post_init__(self) -> None:
        if any(len(row) != len(self.header) for row in self.rows):
            raise ValueError("Every row has a cell for each column")


class TooManyRows(Exception):
    pass


def to_csv(table: Table) -> str:
    if len(table.rows) > MAX_ROWS:
        raise TooManyRows
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(table.header)
    for row in table.rows:
        writer.writerow([cell(value) for value in row])
    return out.getvalue()


def file_name(key: ReportKey, currency: str | None, date_from: date, date_to: date) -> str:
    parts: Sequence[str] = ["finvest", key.replace("_", "-")]
    if currency:
        parts = [*parts, currency.lower()]
    period = date_from.isoformat() + ("" if date_from == date_to else f"-to-{date_to.isoformat()}")
    return "-".join([*parts, period]) + ".csv"
