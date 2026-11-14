"""A month's withholding as a CSV file for URA's return. Pure.

One row per client, with the figures URA's withholding tax schedule asks for: the payee's name,
their TIN if they gave one, the account number Finvest knows them by, interest before tax and
tax withheld. Amounts are written in the major unit with exactly the currency's decimals, from
integers, never through a float.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from app.modules.tax.domain.periods import month_label
from app.shared.money import Money

HEADER = (
    "Month",
    "Payee name",
    "Payee TIN",
    "Account number",
    "Currency",
    "Interest before tax",
    "Tax withheld",
)
# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class PayeeRow:
    name: str
    tin: str | None
    account_number: str | None
    gross: Money
    tax: Money


def amount(money: Money) -> str:
    """'1234.50' for USD 1,234.50 and '26490' for UGX 26,490, with a minus sign if negative."""
    digits = money.currency.minor_digits
    sign = "-" if money.is_negative else ""
    whole, part = divmod(abs(money.minor), 10**digits)
    return f"{sign}{whole}" if digits == 0 else f"{sign}{whole}.{part:0{digits}d}"


def _cell(text: str | None) -> str:
    value = (text or "").strip()
    return f"'{value}" if value.startswith(_FORMULA_STARTS) else value


def payee_csv(month: date, rows: list[PayeeRow]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    label = month_label(month)
    for row in rows:
        writer.writerow(
            (
                label,
                _cell(row.name),
                _cell(row.tin),
                _cell(row.account_number),
                row.gross.currency.value,
                amount(row.gross),
                amount(row.tax),
            )
        )
    return out.getvalue()
