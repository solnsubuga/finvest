"""The running investments as a CSV file, for staff: what Finvest holds for clients. Pure.

One row per running investment, the soonest to mature first. Amounts are written from integers
in the currency's major unit, never through a float. Rates are in percent from basis points, the
same way. A cell that a spreadsheet would run as a formula is written with a leading `'`.

It is a staff file, so it shows the rate and the interest before tax, the tax and the interest
after tax. The client is named only by their account number: a positions file needs no names.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from app.shared.money import Money

HEADER = (
    "Investment ID",
    "Client account number",
    "Product",
    "Currency",
    "Invested",
    "Rate before tax (% a year)",
    "Tax on interest (%)",
    "Started",
    "Matures",
    "Term (days)",
    "Interest accrued through",
    "Interest before tax",
    "Tax",
    "Interest after tax",
    "Value after tax",
    "At maturity",
    "Interest goal",
)
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class PositionRow:
    investment_id: str
    account_number: str | None
    product: str
    invested: Money
    annual_rate_bps: int
    tax_bps: int
    start_date: date | None
    maturity_date: date | None
    term_days: int | None
    accrued_through: date | None
    interest_gross: Money
    tax: Money
    at_maturity: str | None
    interest_goal: str | None

    @property
    def interest_net(self) -> Money:
        return self.interest_gross - self.tax

    @property
    def value(self) -> Money:
        return self.invested + self.interest_net


def percent(bps: int) -> str:
    """Basis points as a percentage with two decimals: 975 is "9.75". Exact."""
    if bps < 0:
        raise ValueError("A rate is never negative")
    whole, rest = divmod(bps, 100)
    return f"{whole}.{rest:02d}"


def _cell(text: str | None) -> str:
    value = (text or "").strip()
    return f"'{value}" if value.startswith(_FORMULA_STARTS) else value


def _day(value: date | None) -> str:
    return value.isoformat() if value else ""


def positions_csv(rows: list[PositionRow]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(
            (
                row.investment_id,
                _cell(row.account_number),
                _cell(row.product),
                row.invested.currency.value,
                row.invested.to_major(),
                percent(row.annual_rate_bps),
                percent(row.tax_bps),
                _day(row.start_date),
                _day(row.maturity_date),
                "" if row.term_days is None else str(row.term_days),
                _day(row.accrued_through),
                row.interest_gross.to_major(),
                row.tax.to_major(),
                row.interest_net.to_major(),
                row.value.to_major(),
                row.at_maturity or "",
                _cell(row.interest_goal),
            )
        )
    return out.getvalue()
