"""The register of compliance cases as a CSV file, for the compliance officer. Pure.

One row per case, newest first, with what raised it, the client's account number and name,
and how it closed. Amounts are whole shillings from integers. A cell that a spreadsheet would
run as a formula is written with a leading `'`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime

from app.shared.business_day import KAMPALA

HEADER = (
    "Case ID",
    "Opened",
    "Source",
    "Raised by",
    "Amount (UGX)",
    "Client account number",
    "Client name",
    "Status",
    "Outcome",
    "Closed",
)
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class RegisterRow:
    case_id: str
    opened_at: datetime
    source: str
    code: str
    amount_minor: int | None
    account_number: str | None
    name: str | None
    status: str
    outcome: str | None
    closed_at: datetime | None


def _cell(text: str | None) -> str:
    value = (text or "").strip()
    return f"'{value}" if value.startswith(_FORMULA_STARTS) else value


def _moment(value: datetime | None) -> str:
    return value.astimezone(KAMPALA).strftime("%Y-%m-%d %H:%M") if value else ""


def register_csv(rows: list[RegisterRow]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(
            (
                row.case_id,
                _moment(row.opened_at),
                row.source,
                row.code,
                "" if row.amount_minor is None else str(row.amount_minor),
                _cell(row.account_number),
                _cell(row.name),
                row.status,
                row.outcome or "",
                _moment(row.closed_at),
            )
        )
    return out.getvalue()
