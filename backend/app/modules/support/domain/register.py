"""The complaints register as a CSV file, for the regulator and the compliance officer. Pure.

One row per complaint, newest first, with its reference, when and how it came, the client's
account number and name, what it is about, its deadline and how it closed. Amounts are whole
numbers in the smallest unit. The complaint's details and the written resolution are left out:
they are free text that can hold anything a client wrote, and the register does not need them.
A cell that a spreadsheet would run as a formula is written with a leading `'`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from app.shared.business_day import KAMPALA

HEADER = (
    "Reference",
    "Received",
    "Received by",
    "Category",
    "Summary",
    "Client account number",
    "Client name",
    "Amount (smallest unit)",
    "Currency",
    "Status",
    "Reply due",
    "Overdue",
    "Closed",
    "Outcome",
    "Client accepted",
)
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class ComplaintRow:
    reference: str
    received_at: datetime
    received_via: str
    category: str
    summary: str
    account_number: str | None
    name: str | None
    amount_minor: int | None
    currency: str | None
    status: str
    due_on: date
    overdue: bool
    closed_at: datetime | None
    outcome: str | None
    client_accepted: bool | None


def _cell(text: str | None) -> str:
    value = (text or "").strip()
    return f"'{value}" if value.startswith(_FORMULA_STARTS) else value


def _moment(value: datetime | None) -> str:
    return value.astimezone(KAMPALA).strftime("%Y-%m-%d %H:%M") if value else ""


def _yes_no(value: bool | None) -> str:
    return "" if value is None else ("Yes" if value else "No")


def complaints_csv(rows: list[ComplaintRow]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(
            (
                _cell(row.reference),
                _moment(row.received_at),
                row.received_via,
                row.category,
                _cell(row.summary),
                _cell(row.account_number),
                _cell(row.name),
                "" if row.amount_minor is None else str(row.amount_minor),
                row.currency or "",
                row.status,
                row.due_on.isoformat(),
                _yes_no(row.overdue),
                _moment(row.closed_at),
                row.outcome or "",
                _yes_no(row.client_accepted),
            )
        )
    return out.getvalue()
