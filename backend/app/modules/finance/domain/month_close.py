"""Closing the month: what must be true before Finance signs a month off, and the record of
the books it signs. Pure.

The ledger closes day by day. Closing the month is Finance's sign-off that the month is
finished: every day in it is closed, so nothing dated in it can still change, and the books
for each currency add up. One person proposes it with the books as they stand, and a different
person approves it. The books are kept with the close, with a digest, so the month's figures
can be shown and checked later exactly as they were signed.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from app.modules.finance.domain.books import month_end, month_label


@dataclass(frozen=True, slots=True)
class SignedLine:
    """One account code's month in one currency, in minor units."""

    currency: str
    code: str
    kind: str
    opening: int
    debits: int
    credits: int
    closing: int

    def as_json(self) -> dict[str, object]:
        return {
            "currency": self.currency,
            "code": self.code,
            "kind": self.kind,
            "opening": self.opening,
            "debits": self.debits,
            "credits": self.credits,
            "closing": self.closing,
        }


def ordered(lines: Iterable[SignedLine]) -> tuple[SignedLine, ...]:
    return tuple(sorted(lines, key=lambda line: (line.currency, line.code)))


def digest(month: date, lines: Sequence[SignedLine]) -> str:
    """A fingerprint of the month's books, the same for the same figures in any order."""
    body = json.dumps(
        {"month": month_label(month), "lines": [line.as_json() for line in ordered(lines)]},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def balances(lines: Sequence[SignedLine]) -> bool:
    """In each currency the month's debits equal its credits."""
    totals: dict[str, int] = {}
    for line in lines:
        totals[line.currency] = totals.get(line.currency, 0) + line.debits - line.credits
    return all(total == 0 for total in totals.values())


class CannotClose(Exception):
    """The month cannot be closed now. `code` is safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check_can_close(
    month: date,
    *,
    today: date,
    closed_through: date | None,
    last_closed_month: date | None,
    lines: Sequence[SignedLine],
) -> None:
    """Refuses a month that has not ended, is not the next one after the last month closed,
    still has a day open in the ledger, or whose books do not add up."""
    if month.day != 1:
        raise ValueError("A month is named by its first day")
    if month_end(month) >= today:
        raise CannotClose("month_not_ended")
    if last_closed_month is not None:
        if month <= last_closed_month:
            raise CannotClose("month_already_closed")
        following = date(
            last_closed_month.year + last_closed_month.month // 12,
            last_closed_month.month % 12 + 1,
            1,
        )
        if month != following:
            raise CannotClose("close_months_in_order")
    if closed_through is None or closed_through < month_end(month):
        raise CannotClose("days_not_closed")
    if not balances(lines):
        raise CannotClose("books_do_not_balance")
