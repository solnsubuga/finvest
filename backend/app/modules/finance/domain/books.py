"""What the Finance screen shows from the books, worked out from the ledger's totals. Pure.

Finvest charges clients no fee to open an account, to invest or to withdraw early. Its income
is what it earns on the bills it holds, and its costs are the interest it pays clients, the
charges providers keep and any loss on a bill. Both come straight from the ledger's income and
expense accounts, so nothing here is estimated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.shared.money import Currency, Money

_MONTH = re.compile(r"([0-9]{4})-([0-9]{2})")


class BadMonth(ValueError):
    """Text that does not name a month."""


def parse_month(text: str) -> date:
    """'2026-11' as the first day of that month."""
    found = _MONTH.fullmatch(text)
    if found is None or not 1 <= int(found.group(2)) <= 12 or int(found.group(1)) < 2000:
        raise BadMonth("A month is written YYYY-MM")
    return date(int(found.group(1)), int(found.group(2)), 1)


def month_label(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def month_end(month: date) -> date:
    following = (
        date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)
    )
    return following - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class CodeLine:
    """One account code over a period, in the shape the ledger reports it."""

    code: str
    kind: str
    name: str
    opening: Money
    debits: Money
    credits: Money
    closing: Money

    @property
    def change(self) -> Money:
        """How far the balance moved over the period, in the code's normal direction."""
        return self.closing - self.opening


@dataclass(frozen=True, slots=True)
class IncomeLine:
    code: str
    name: str
    amount: Money


@dataclass(frozen=True, slots=True)
class IncomeAndCosts:
    """Finvest's own income and costs over a period, account by account, and what is left."""

    income: tuple[IncomeLine, ...]
    costs: tuple[IncomeLine, ...]
    income_total: Money
    cost_total: Money

    @property
    def net(self) -> Money:
        return self.income_total - self.cost_total


def income_and_costs(currency: Currency, lines: list[CodeLine]) -> IncomeAndCosts:
    """The income and expense codes from the books, each with how much it moved.

    A code that did not move in the period is left out. A reversal can make a code's movement
    negative; it is shown as it is, never hidden.
    """
    income = tuple(
        IncomeLine(line.code, line.name, line.change)
        for line in lines
        if line.kind == "income" and not line.change.is_zero
    )
    costs = tuple(
        IncomeLine(line.code, line.name, line.change)
        for line in lines
        if line.kind == "expense" and not line.change.is_zero
    )
    zero = Money.zero(currency)
    return IncomeAndCosts(
        income=income,
        costs=costs,
        income_total=sum((line.amount for line in income), zero),
        cost_total=sum((line.amount for line in costs), zero),
    )
