"""Interest and tax on an investment. Pure: no I/O, no floats.

The formula is the one Finvest has always used: principal times yearly rate times days, over
365. A year is 365 days in every year, leap years included.

Interest is posted to the ledger once a day. Rounding each day's slice separately would lose a
fraction of a shilling every day, so nothing is rounded per day. Instead the total earned after
`n` days is worked out from the formula and rounded once, and the amount posted on day `n` is
that total minus the total after `n - 1` days. The daily postings therefore always sum to
exactly what the formula gives, on every day of the term and at maturity.

Rounding, decided for now (see docs/decisions.md):

* Interest before tax is rounded down to the smallest unit. Finvest never credits interest
  that has not been earned.
* Tax is the tax rate applied to that total, rounded down. The fraction stays with the client.
* What the client earns is the first minus the second, so the three always add up.

Clients are shown only what they earn after tax. `ClientReturns` carries nothing else, and it
is the only type from this module that a client-facing contract may be built from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.shared.money import Money, Rounding

DAYS_IN_YEAR = 365
BASIS_POINTS = 10_000
# The rounding of interest and of tax is decided here and nowhere else. Both round down:
# interest that has not been earned is never credited, and the fraction of tax stays with the
# client. Changing either is a decision for docs/decisions.md.
INTEREST_ROUNDING = Rounding.DOWN
TAX_ROUNDING = Rounding.DOWN
# A yearly rate above this is a typing mistake, not a product.
MAX_ANNUAL_RATE_BPS = 5_000


class InvalidTerms(ValueError):
    """Terms that cannot be applied to money."""


@dataclass(frozen=True, slots=True)
class Terms:
    """What an investment earns. Fixed when it starts and never changed afterwards.

    Rates are whole basis points: 12.5% a year is 1250, and tax of 15% is 1500.
    """

    principal: Money
    annual_rate_bps: int
    tax_bps: int

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Money) or not self.principal.is_positive:
            raise InvalidTerms("The principal is a positive amount")
        check_rate(self.annual_rate_bps, self.tax_bps)


def check_rate(annual_rate_bps: int, tax_bps: int) -> None:
    if type(annual_rate_bps) is not int or type(tax_bps) is not int:
        raise InvalidTerms("Rates are whole basis points, never floats")
    if not 0 < annual_rate_bps <= MAX_ANNUAL_RATE_BPS:
        raise InvalidTerms("The yearly rate is above 0% and at most 50%")
    if not 0 <= tax_bps < BASIS_POINTS:
        raise InvalidTerms("Tax is from 0% to below 100%")


@dataclass(frozen=True, slots=True)
class Accrual:
    """Interest for staff, the ledger and reports. Never sent to a client: it shows the tax."""

    gross: Money
    tax: Money

    @property
    def net(self) -> Money:
        return self.gross - self.tax


@dataclass(frozen=True, slots=True)
class ClientReturns:
    """What a client is shown: interest after tax, and what they get back in total."""

    interest: Money
    total: Money


def accrued(terms: Terms, days: int) -> Accrual:
    """Everything earned in the first `days` days of an investment."""
    if type(days) is not int or days < 0:
        raise InvalidTerms("Days is a whole number, zero or more")
    gross = terms.principal.mul_ratio(
        terms.annual_rate_bps * days, BASIS_POINTS * DAYS_IN_YEAR, INTEREST_ROUNDING
    )
    tax = gross.mul_ratio(terms.tax_bps, BASIS_POINTS, TAX_ROUNDING)
    return Accrual(gross=gross, tax=tax)


def day_accrual(terms: Terms, day: int) -> Accrual:
    """What is posted for day number `day`, counting the first day earned as 1.

    Any part can be zero on a given day for a small investment, and none is ever negative.
    """
    if type(day) is not int or day < 1:
        raise InvalidTerms("Days are counted from 1")
    today, before = accrued(terms, day), accrued(terms, day - 1)
    return Accrual(gross=today.gross - before.gross, tax=today.tax - before.tax)


def client_returns(terms: Terms, days: int) -> ClientReturns:
    """What the client has earned after `days` days, after tax, and the total they get back."""
    interest = accrued(terms, days).net
    return ClientReturns(interest=interest, total=terms.principal + interest)


def maturity_date(start: date, term_days: int) -> date:
    """The day a fixed term ends. The last day's interest is earned on this date."""
    if type(term_days) is not int or term_days < 1:
        raise InvalidTerms("A term is at least one day")
    return start + timedelta(days=term_days)


def days_earned(start: date, on: date, term_days: int | None) -> int:
    """How many days of interest an investment has earned by the end of the run for `on`.

    Nothing is earned on the start date itself. The first day is earned the day after, and a
    fixed term stops earning at maturity however late the question is asked. An open term
    (`term_days` of `None`) keeps earning.
    """
    if term_days is not None and (type(term_days) is not int or term_days < 1):
        raise InvalidTerms("A term is at least one day")
    elapsed = max((on - start).days, 0)
    return elapsed if term_days is None else min(elapsed, term_days)


@dataclass(frozen=True, slots=True)
class Posting:
    """What one daily run posts for one investment."""

    # The last day this posting pays interest for. Never after maturity.
    through: date
    # Days of interest earned by the end of that day.
    days: int
    amount: Accrual
    total: Accrual


def posting_due(
    terms: Terms,
    *,
    start: date,
    term_days: int | None,
    posted: Accrual,
    posted_through: date | None,
    run_date: date,
) -> Posting | None:
    """What the run for `run_date` must post, given what earlier runs posted. None if nothing.

    The amount is everything earned by `run_date` minus everything already posted. So a run
    that follows a missed day pays for both days at once, a run repeated for a day already
    paid posts nothing, and the postings always sum to what the formula gives.
    """
    days = days_earned(start, run_date, term_days)
    through = start + timedelta(days=days)
    if days == 0 or (posted_through is not None and posted_through >= through):
        return None
    total = accrued(terms, days)
    amount = Accrual(gross=total.gross - posted.gross, tax=total.tax - posted.tax)
    if amount.gross.is_negative or amount.tax.is_negative or amount.net.is_negative:
        # The books hold more interest than the terms can have earned. Never post a reduction.
        raise InvalidTerms("More interest is posted than these terms have earned")
    return Posting(through=through, days=days, amount=amount, total=total)
