"""Tax withheld from interest, per investment, per client and per month. Pure.

Every figure comes from the ledger: interest before tax is what `interest.expense` took, and tax
is what `tax.withholding_payable` was given, from the entries of each investment. Interest after
tax is the difference, which is exactly what the client's accrued-interest account was given,
because each interest entry balances. Reversals and unwinds are signed, so they net out in the
month they are posted in.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.modules.tax.domain.periods import TaxYear, due_on
from app.shared.money import Currency, Money

INTEREST_EXPENSE = "interest.expense"
TAX_PAYABLE = "tax.withholding_payable"
INVESTMENT_SOURCE = "investment"


class DoesNotAddUp(ValueError):
    """Interest before tax less tax withheld is not interest after tax. Nothing is issued."""


@dataclass(frozen=True, slots=True)
class InvestmentTax:
    """What one investment earned and had withheld over a period."""

    investment_id: str
    client_id: str
    product_name: str
    start_date: date | None
    maturity_date: date | None
    gross: Money  # interest before tax
    tax: Money  # tax withheld

    @property
    def net(self) -> Money:
        return self.gross - self.tax


def from_ledger(expense_debits: Money, payable_debits: Money) -> tuple[Money, Money]:
    """Interest before tax and tax withheld, from the signed sums on `interest.expense` (an
    expense: interest is a debit) and `tax.withholding_payable` (a liability: tax is a credit)."""
    return expense_debits, -payable_debits


@dataclass(frozen=True, slots=True)
class Payee:
    """One client in a month's return: what they earned and what was withheld from it."""

    client_id: str
    gross: Money
    tax: Money

    @property
    def net(self) -> Money:
        return self.gross - self.tax


def payees(items: Iterable[InvestmentTax], currency: Currency) -> list[Payee]:
    """One line per client, in client order. A client whose interest and tax both net to zero,
    such as one whose only investment was undone, is left out."""
    gross: dict[str, Money] = {}
    tax: dict[str, Money] = {}
    zero = Money.zero(currency)
    for item in items:
        gross[item.client_id] = gross.get(item.client_id, zero) + item.gross
        tax[item.client_id] = tax.get(item.client_id, zero) + item.tax
    return [
        Payee(client_id, gross[client_id], tax[client_id])
        for client_id in sorted(gross)
        if not (gross[client_id].is_zero and tax[client_id].is_zero)
    ]


@dataclass(frozen=True, slots=True)
class MonthSummary:
    """One month's withholding against what has been paid to URA for it."""

    month: date
    withheld: Money
    # Approved remittances for the month, and those still waiting for a second person.
    remitted: Money
    waiting: Money

    @property
    def outstanding(self) -> Money:
        """Still to pay. Negative when the month owes URA less than was paid, such as when an
        investment withheld from was later undone: a credit against the next payment."""
        return self.withheld - self.remitted - self.waiting

    @property
    def due_on(self) -> date:
        return due_on(self.month)


def summaries(
    months: list[date],
    withheld: dict[date, Money],
    remitted: dict[date, Money],
    waiting: dict[date, Money],
    currency: Currency,
) -> list[MonthSummary]:
    """A summary for each month asked for, newest first, with zeros for a quiet month."""
    zero = Money.zero(currency)
    return [
        MonthSummary(
            month,
            withheld.get(month, zero),
            remitted.get(month, zero),
            waiting.get(month, zero),
        )
        for month in sorted(set(months), reverse=True)
    ]


# The yearly certificate -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificateLine:
    """One investment on a certificate. `investment_id` is its stable reference."""

    investment_id: str
    product_name: str
    start_date: date | None
    maturity_date: date | None
    gross: Money
    tax: Money
    net: Money


@dataclass(frozen=True, slots=True)
class CertificateTotal:
    currency: Currency
    gross: Money
    tax: Money
    net: Money


@dataclass(frozen=True, slots=True)
class Certificate:
    """A client's withholding tax certificate for one tax year. Shows tax amounts, never a rate.

    Every investment that earned interest in the year is on it, including one that matured, was
    rolled over or was withdrawn during the year. Lines are in currency, then start date, then ID
    order, and there is a total for each currency that has a line.
    """

    client_id: str
    holder_name: str  # given names then surname, as on statements
    account_number: str
    tin: str | None  # None when the client gave none
    tax_year: TaxYear
    lines: tuple[CertificateLine, ...]
    totals: tuple[CertificateTotal, ...]

    @property
    def is_empty(self) -> bool:
        return not self.lines


def certificate(
    *,
    client_id: str,
    holder_name: str,
    account_number: str,
    tin: str | None,
    tax_year: TaxYear,
    items: Iterable[InvestmentTax],
) -> Certificate:
    """Build a certificate and check it adds up. Raises `DoesNotAddUp` when it does not."""
    kept = [
        item
        for item in items
        if item.client_id == client_id and not (item.gross.is_zero and item.tax.is_zero)
    ]
    if len({item.investment_id for item in kept}) != len(kept):
        raise DoesNotAddUp("An investment appears twice")
    kept.sort(
        key=lambda item: (
            item.gross.currency.value,
            item.start_date or date.max,
            item.investment_id,
        )
    )
    lines = tuple(
        CertificateLine(
            investment_id=item.investment_id,
            product_name=item.product_name,
            start_date=item.start_date,
            maturity_date=item.maturity_date,
            gross=item.gross,
            tax=item.tax,
            net=item.net,
        )
        for item in kept
    )
    totals: list[CertificateTotal] = []
    for currency in sorted({line.gross.currency for line in lines}, key=lambda c: c.value):
        mine = [line for line in lines if line.gross.currency is currency]
        zero = Money.zero(currency)
        gross = sum((line.gross for line in mine), zero)
        tax = sum((line.tax for line in mine), zero)
        net = sum((line.net for line in mine), zero)
        totals.append(CertificateTotal(currency, gross, tax, net))
    made = Certificate(
        client_id=client_id,
        holder_name=holder_name,
        account_number=account_number,
        tin=tin,
        tax_year=tax_year,
        lines=lines,
        totals=tuple(totals),
    )
    check(made)
    return made


def check(made: Certificate) -> None:
    """Every line and every total: before tax less tax is after tax, and the totals are the sums
    of the lines. A line can be negative: an investment undone in this tax year after earning in
    the last one gives back what it was credited then."""
    for line in made.lines:
        if line.gross - line.tax != line.net:
            raise DoesNotAddUp("A line does not add up")
    currencies = {line.gross.currency for line in made.lines}
    if {total.currency for total in made.totals} != currencies or len(made.totals) != len(
        currencies
    ):
        raise DoesNotAddUp("The totals do not match the lines")
    for total in made.totals:
        mine = [line for line in made.lines if line.gross.currency is total.currency]
        zero = Money.zero(total.currency)
        if (
            sum((line.gross for line in mine), zero) != total.gross
            or sum((line.tax for line in mine), zero) != total.tax
            or sum((line.net for line in mine), zero) != total.net
            or total.gross - total.tax != total.net
        ):
            raise DoesNotAddUp("A total does not add up")
