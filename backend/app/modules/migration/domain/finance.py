"""The finance check: do the imported figures agree with the old system? Pure.

Finance enters the old system's own totals, taken from its reports and the custodian statement
on the "as of" date. They are compared with what the trial import holds. Every line either
agrees or is signed off with a reason, and the switch is refused until then.

Interest is checked investment by investment. The new system opens each investment with what
its own formula says was earned by the "as of" date, so that the daily postings afterwards
still sum to the formula on every day. Where the old system's figure differs, the difference
is listed here for finance to explain. It is never posted to the ledger.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.investments.domain.interest import Accrual, Terms, accrued, days_earned
from app.modules.investments.domain.products import Product
from app.modules.migration.domain.sheet import InvestmentRow, InvestmentStatus
from app.modules.migration.domain.trial import TrialReport
from app.shared.money import Currency, Money

MAX_REASON_LENGTH = 500


class InvalidFinanceCheck(ValueError):
    """Totals or a sign-off that cannot be used."""


class Figure(StrEnum):
    CLIENTS = "clients"
    VERIFIED_IDENTITIES = "verified_identities"
    RUNNING_INVESTMENTS = "running_investments"
    PENDING_ORDERS = "pending_orders"
    INVESTED = "invested"
    INTEREST_BEFORE_TAX = "interest_before_tax"


_COUNTS = (
    Figure.CLIENTS,
    Figure.VERIFIED_IDENTITIES,
    Figure.RUNNING_INVESTMENTS,
    Figure.PENDING_ORDERS,
)


@dataclass(frozen=True, slots=True)
class ControlTotals:
    """What Finvest 1.0 says it holds on the "as of" date, entered by finance."""

    clients: int
    verified_identities: int
    running_investments: int
    pending_orders: int
    # One amount per currency. A currency left out means the old system holds none of it.
    invested: tuple[Money, ...]
    interest_before_tax: tuple[Money, ...]

    def __post_init__(self) -> None:
        for figure in _COUNTS:
            count = getattr(self, figure.value)
            if type(count) is not int or count < 0:
                raise InvalidFinanceCheck("Counts are whole numbers, zero or more")
        for amounts in (self.invested, self.interest_before_tax):
            if any(not isinstance(a, Money) or a.is_negative for a in amounts):
                raise InvalidFinanceCheck("Totals are amounts of money, zero or more")
            if len({amount.currency for amount in amounts}) != len(amounts):
                raise InvalidFinanceCheck("One total per currency")


@dataclass(frozen=True, slots=True)
class Opening:
    """What one running investment is opened with at the switch.

    The investments module posts it: principal to the client's holding, interest after tax to
    the client's accrued interest, tax to the tax payable account, and the three together
    against the migration opening balances account.
    """

    legacy_investment_ref: str
    legacy_client_ref: str
    product_code: str
    terms: Terms
    start_date: date
    opened_on: date
    days_earned: int
    interest: Accrual

    @property
    def opening_balance(self) -> Money:
        """The one debit: everything the client is owed plus the tax held for them."""
        return self.terms.principal + self.interest.gross


@dataclass(frozen=True, slots=True)
class InterestDifference:
    """A running investment whose interest in the old system is not what the formula gives."""

    row: int
    legacy_investment_ref: str
    old_interest: Money
    new_interest: Money
    # `None` when the old records do not say how much tax was held.
    old_tax: Money | None
    new_tax: Money

    @property
    def difference(self) -> Money:
        """Old minus new, before tax. Positive when the old system shows more."""
        return self.old_interest - self.new_interest

    @property
    def key(self) -> str:
        return f"investment:{self.legacy_investment_ref}"


@dataclass(frozen=True, slots=True)
class TaxRateDifference:
    """A running investment whose tax on interest is not the product's rate."""

    row: int
    legacy_investment_ref: str
    stated_tax_bps: int
    product_tax_bps: int

    @property
    def key(self) -> str:
        return f"tax_rate:{self.legacy_investment_ref}"


@dataclass(frozen=True, slots=True)
class TotalLine:
    """One row of the comparison. Counts and amounts are both plain integers here."""

    figure: Figure
    currency: Currency | None  # `None` for a count
    old: int
    new: int

    @property
    def difference(self) -> int:
        return self.old - self.new

    @property
    def agrees(self) -> bool:
        return self.old == self.new

    @property
    def key(self) -> str:
        suffix = "" if self.currency is None else f":{self.currency.value}"
        return f"total:{self.figure.value}{suffix}"


@dataclass(frozen=True, slots=True)
class SignOff:
    """Finance accepting one difference, with the reason. `key` names the line or investment."""

    key: str
    reason: str
    signed_by: str

    def __post_init__(self) -> None:
        if not self.reason.strip() or len(self.reason) > MAX_REASON_LENGTH:
            raise InvalidFinanceCheck(f"A reason is 1 to {MAX_REASON_LENGTH} characters")
        if not self.key or not self.signed_by:
            raise InvalidFinanceCheck("A sign-off names what it covers and who signed")


@dataclass(frozen=True, slots=True)
class FinanceCheck:
    lines: tuple[TotalLine, ...]
    interest_differences: tuple[InterestDifference, ...]
    tax_rate_differences: tuple[TaxRateDifference, ...]
    openings: tuple[Opening, ...]

    @property
    def needs_sign_off(self) -> tuple[str, ...]:
        """The key of every total that differs, then of every investment that differs."""
        return tuple(
            [line.key for line in self.lines if not line.agrees]
            + [difference.key for difference in self.interest_differences]
            + [difference.key for difference in self.tax_rate_differences]
        )


def opening_for(investment: InvestmentRow, product: Product, *, opened_on: date) -> Opening:
    """What a running investment is opened with on `opened_on`, by the new system's formula."""
    if (
        investment.status is not InvestmentStatus.RUNNING
        or investment.start_date is None
        or investment.annual_rate_bps is None
        or investment.tax_bps is None
    ):
        raise InvalidFinanceCheck("Only a running investment is opened")
    if product.code != investment.product_code:
        raise InvalidFinanceCheck("The product does not match the investment")
    terms = Terms(investment.principal, investment.annual_rate_bps, investment.tax_bps)
    days = days_earned(investment.start_date, opened_on, product.term_days)
    return Opening(
        legacy_investment_ref=investment.legacy_investment_ref,
        legacy_client_ref=investment.legacy_client_ref,
        product_code=product.code,
        terms=terms,
        start_date=investment.start_date,
        opened_on=opened_on,
        days_earned=days,
        interest=accrued(terms, days),
    )


def check_finances(
    report: TrialReport,
    old: ControlTotals,
    products: Mapping[str, Product],
    tax_bps: Mapping[str, int],
) -> FinanceCheck:
    """Compare a trial import with the old system's totals, and its interest with the formula.

    The interest line compares the old system's total with what the new system will open
    with, so a difference on that line is explained by the investments listed under it.

    `tax_bps` is the tax on interest each product withholds, by product code. An investment
    stated with another tax rate is opened with the stated one only once finance signs it off,
    because the tax held for every later day follows from it.
    """
    openings: list[Opening] = []
    differences: list[InterestDifference] = []
    tax_rates: list[TaxRateDifference] = []
    new_interest: dict[Currency, int] = {}
    for investment in report.investments:
        if investment.status is not InvestmentStatus.RUNNING:
            continue
        opening = opening_for(investment, products[investment.product_code], opened_on=report.as_of)
        openings.append(opening)
        product_tax = tax_bps[investment.product_code]
        if opening.terms.tax_bps != product_tax:
            tax_rates.append(
                TaxRateDifference(
                    investment.row,
                    investment.legacy_investment_ref,
                    opening.terms.tax_bps,
                    product_tax,
                )
            )
        gross, tax = opening.interest.gross, opening.interest.tax
        new_interest[gross.currency] = new_interest.get(gross.currency, 0) + gross.minor
        old_gross, old_tax = investment.interest_before_tax, investment.tax_withheld
        if old_gross is None:
            raise InvalidFinanceCheck("A running investment has an interest figure")
        if old_gross != gross or (old_tax is not None and old_tax != tax):
            differences.append(
                InterestDifference(
                    investment.row,
                    investment.legacy_investment_ref,
                    old_gross,
                    gross,
                    old_tax,
                    tax,
                )
            )

    summary = report.summary
    lines = [
        TotalLine(figure, None, getattr(old, figure.value), getattr(summary, figure.value))
        for figure in _COUNTS
    ]
    lines += _money_lines(Figure.INVESTED, old.invested, _by_currency(summary.invested))
    lines += _money_lines(Figure.INTEREST_BEFORE_TAX, old.interest_before_tax, new_interest)
    return FinanceCheck(tuple(lines), tuple(differences), tuple(tax_rates), tuple(openings))


def _by_currency(amounts: Iterable[Money]) -> dict[Currency, int]:
    return {amount.currency: amount.minor for amount in amounts}


def _money_lines(
    figure: Figure, old: tuple[Money, ...], new: Mapping[Currency, int]
) -> list[TotalLine]:
    before = _by_currency(old)
    return [
        TotalLine(figure, currency, before.get(currency, 0), new.get(currency, 0))
        for currency in Currency
        if currency in before or currency in new
    ]


class Blocker(StrEnum):
    IMPORT_HAS_ERRORS = "import_has_errors"
    DIFFERENCE_NOT_SIGNED_OFF = "difference_not_signed_off"
    SIGN_OFF_FOR_NOTHING = "sign_off_for_nothing"


def switch_blockers(
    report: TrialReport, check: FinanceCheck, sign_offs: Iterable[SignOff]
) -> list[tuple[Blocker, str | None]]:
    """Why the switch cannot be scheduled yet. An empty list means it can.

    Every error in the import is fixed, and every difference agrees or carries a sign-off. A
    sign-off for something that no longer differs is refused too: it was written for an
    earlier file, and its reason may explain a difference that has since changed.
    """
    blockers: list[tuple[Blocker, str | None]] = []
    if not report.clean:
        blockers.append((Blocker.IMPORT_HAS_ERRORS, None))
    needed = check.needs_sign_off
    signed = {sign_off.key for sign_off in sign_offs}
    blockers += [(Blocker.DIFFERENCE_NOT_SIGNED_OFF, key) for key in needed if key not in signed]
    blockers += [(Blocker.SIGN_OFF_FOR_NOTHING, key) for key in sorted(signed - set(needed))]
    return blockers
