"""Bond issues: the Uganda government bonds a client can buy a part of. Pure: no I/O, no floats.

Bank of Uganda sells each bond as a specific issue: an ISIN, a coupon rate fixed for its whole
life, an issue date and a maturity date. It pays a coupon every six months on the issue's own
dates, the same dates for every holder, and the face value at maturity. It often reopens an
issue: it sells more of it later, at a price set by the market yield on the day. Finvest holds
the bonds for its clients, and a client owns a face value of one issue.

* **Coupon dates** are every six calendar months back from the maturity date, on the maturity
  date's day of the month (or the month's last day when it is shorter), after the issue date.
  The last coupon is paid on the maturity date with the face value.
* **A coupon** before tax is the face value times the yearly coupon rate, halved, rounded
  down to the shilling.
* **Accrued interest** is the part of the coming coupon earned since the last coupon date:
  the coupon times the days since the last coupon date over the days in the period, rounded
  down (actual/actual, decided for now: see docs/decisions.md). A buyer pays it on top of the
  price, and gets it back in the full coupon. A seller is paid it on top of the sale price.
* **Prices** are per 100 of face value, to four decimal places, held as an integer: 98.5 is
  985,000 (`PRICE_SCALE` is 100.0000).

Clients are never shown a tax rate. They see coupons and returns after tax.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.shared.money import Currency, Money, Rounding

BASIS_POINTS = 10_000
# 100.0000 per 100 of face value: the price of a bond at par.
PRICE_SCALE = 1_000_000
# A price below 50 or above 150 per 100 is a typing mistake, not a market.
MIN_PRICE = 500_000
MAX_PRICE = 1_500_000
MAX_COUPON_BPS = 5_000
# Bank of Uganda sells bonds to the public from UGX 100,000, in steps of UGX 100,000.
DEFAULT_MINIMUM_FACE = 100_000
DEFAULT_FACE_STEP = 100_000
# The longest Uganda government bond is 25 years.
MAX_YEARS = 30
# Withholding tax on coupons (Bank of Uganda's invitation to tender): 20% for bonds of under
# ten years, 10% for ten years or more. Staff may enter another rate, for counsel's answer.
TAX_UNDER_TEN_YEARS_BPS = 2_000
TAX_TEN_YEARS_OR_MORE_BPS = 1_000
_CODE = re.compile(r"[A-Z][A-Z0-9]*(-[A-Z0-9]+)*")
# Two letters, nine letters or digits, and a check digit: UG12K1405378.
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
MAX_CODE_LENGTH = 24
MAX_NAME_LENGTH = 80
INTEREST_ROUNDING = Rounding.DOWN
TAX_ROUNDING = Rounding.DOWN
# What a client pays rounds up and what they are paid rounds down, so Finvest never pays out a
# fraction of a shilling it did not receive.
COST_ROUNDING = Rounding.UP
PROCEEDS_ROUNDING = Rounding.DOWN


class InvalidIssue(ValueError):
    """A bond issue that contradicts itself."""


class BondStatus(StrEnum):
    # Not yet offered. Staff are still setting it up.
    DRAFT = "draft"
    # Clients can buy.
    LIVE = "live"
    # No new buying. Holdings carry on to maturity.
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class BondIssue:
    code: str
    isin: str
    name: str
    currency: Currency
    # The yearly coupon rate before tax, in basis points: 16% is 1600.
    coupon_bps: int
    # Withholding tax on coupons and accrued interest, in basis points.
    tax_bps: int
    issue_date: date
    maturity_date: date
    minimum_face: Money
    face_step: Money
    status: BondStatus = BondStatus.DRAFT

    def __post_init__(self) -> None:
        if len(self.code) > MAX_CODE_LENGTH or _CODE.fullmatch(self.code) is None:
            raise InvalidIssue("A code is upper-case letters, digits and hyphens")
        if _ISIN.fullmatch(self.isin) is None:
            raise InvalidIssue("An ISIN is 12 characters, such as UG12K1405378")
        if (
            not self.name.strip()
            or len(self.name) > MAX_NAME_LENGTH
            or self.name != self.name.strip()
        ):
            raise InvalidIssue(f"A name is 1 to {MAX_NAME_LENGTH} characters")
        if not isinstance(self.currency, Currency) or not isinstance(self.status, BondStatus):
            raise InvalidIssue("Unknown currency or status")
        if type(self.coupon_bps) is not int or not 0 < self.coupon_bps <= MAX_COUPON_BPS:
            raise InvalidIssue("The coupon is above 0% and at most 50% a year")
        if type(self.tax_bps) is not int or not 0 <= self.tax_bps < BASIS_POINTS:
            raise InvalidIssue("Tax is from 0% to below 100%")
        if type(self.issue_date) is not date or type(self.maturity_date) is not date:
            raise InvalidIssue("The issue and maturity dates are calendar dates")
        if self.maturity_date <= self.issue_date:
            raise InvalidIssue("A bond matures after it is issued")
        if self.maturity_date.year - self.issue_date.year > MAX_YEARS:
            raise InvalidIssue(f"A bond runs at most {MAX_YEARS} years")
        if not coupon_dates(self):
            raise InvalidIssue("A bond pays at least its coupon at maturity")
        for amount in (self.minimum_face, self.face_step):
            if not isinstance(amount, Money) or amount.currency is not self.currency:
                raise InvalidIssue("Face amounts are in the bond's currency")
            if not amount.is_positive:
                raise InvalidIssue("Face amounts are positive")
        if self.minimum_face.minor % self.face_step.minor:
            raise InvalidIssue("The minimum is a whole number of steps")

    @property
    def years(self) -> int:
        """Whole years from issue to maturity, as the bond is named: a 10 year bond."""
        return years_between(self.issue_date, self.maturity_date)


def years_between(start: date, end: date) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(years, 0)


def default_tax_bps(issue_date: date, maturity_date: date) -> int:
    """The withholding tax Bank of Uganda states for a bond of this length."""
    # A reopened 10 year bond can be a few days short of ten calendar years from its first
    # issue, so ten years of Bank of Uganda's 364-day years counts.
    if (maturity_date - issue_date).days >= 10 * 364:
        return TAX_TEN_YEARS_OR_MORE_BPS
    return TAX_UNDER_TEN_YEARS_BPS


def _months_back(day: date, months: int, anchor_day: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def coupon_dates(issue: BondIssue) -> tuple[date, ...]:
    """Every coupon date after the issue date, oldest first. The last is the maturity date."""
    found: list[date] = []
    step = 0
    while True:
        on = _months_back(issue.maturity_date, 6 * step, issue.maturity_date.day)
        if on <= issue.issue_date:
            break
        found.append(on)
        step += 1
    return tuple(reversed(found))


def period_around(issue: BondIssue, on: date) -> tuple[date, date]:
    """The coupon period `on` falls in: the last coupon date on or before it (or the date the
    period began, before the first coupon), and the next coupon date after it.

    Refused on or after the maturity date, when no period is running.
    """
    if on >= issue.maturity_date:
        raise InvalidIssue("The bond has matured")
    dates = coupon_dates(issue)
    following = next(d for d in dates if d > on)
    index = dates.index(following)
    previous = (
        dates[index - 1] if index > 0 else _months_back(following, 6, issue.maturity_date.day)
    )
    return previous, following


def coupon_gross(issue: BondIssue, face: Money) -> Money:
    """One coupon before tax on `face`."""
    _check_face(issue, face, minimum=False)
    return face.mul_ratio(issue.coupon_bps, 2 * BASIS_POINTS, INTEREST_ROUNDING)


def tax_on(issue: BondIssue, interest: Money) -> Money:
    return interest.mul_ratio(issue.tax_bps, BASIS_POINTS, TAX_ROUNDING)


def accrued_interest(issue: BondIssue, face: Money, on: date) -> Money:
    """Interest before tax earned on `face` since the last coupon date, by the end of `on`."""
    previous, following = period_around(issue, on)
    days = max((on - previous).days, 0)
    whole = coupon_gross(issue, face)
    return whole.mul_ratio(days, (following - previous).days, INTEREST_ROUNDING)


def clean_amount(face: Money, price: int) -> Money:
    check_price(price)
    return face.mul_ratio(price, PRICE_SCALE, COST_ROUNDING)


def check_price(price: int) -> None:
    if type(price) is not int or not MIN_PRICE <= price <= MAX_PRICE:
        raise InvalidIssue("A price is between 50 and 150 per 100 of face value")


def _check_face(issue: BondIssue, face: Money, *, minimum: bool) -> None:
    if not isinstance(face, Money) or face.currency is not issue.currency or not face.is_positive:
        raise InvalidIssue("A face value is a positive amount in the bond's currency")
    if minimum and face < issue.minimum_face:
        raise InvalidIssue("Below the smallest face value")


class Refusal(StrEnum):
    ISSUE_NOT_OPEN = "product_not_open"
    CURRENCY_NOT_SUPPORTED = "currency_not_supported"
    AMOUNT_NOT_POSITIVE = "amount_not_positive"
    BELOW_MINIMUM = "below_minimum"
    NOT_A_STEP = "not_a_step"
    ABOVE_MAXIMUM = "above_maximum"
    # Too close to maturity to buy: the last coupon period has begun.
    TOO_CLOSE_TO_MATURITY = "too_close_to_maturity"
    NO_PRICE = "no_price"


@dataclass(frozen=True, slots=True)
class Quote:
    """What buying `face` of an issue costs today, and what it pays. Staff see all of it."""

    face: Money
    price: int
    clean: Money
    accrued: Money
    # What the client pays: the clean amount and the accrued interest.
    cost: Money
    # One coupon after tax, and how many are left including the one at maturity.
    coupon_net: Money
    coupons_left: int
    next_coupon_date: date
    # Everything the client receives if they hold to maturity, after tax.
    total_back: Money

    @property
    def gain(self) -> Money:
        return self.total_back - self.cost


@dataclass(frozen=True, slots=True)
class ClientQuote:
    """What a client is shown of a quote. Every figure is after tax."""

    face: Money
    price: int
    accrued: Money
    cost: Money
    coupon_net: Money
    coupons_left: int
    next_coupon_date: date
    total_back: Money
    gain: Money


def client_quote(quote: Quote) -> ClientQuote:
    return ClientQuote(
        face=quote.face,
        price=quote.price,
        accrued=quote.accrued,
        cost=quote.cost,
        coupon_net=quote.coupon_net,
        coupons_left=quote.coupons_left,
        next_coupon_date=quote.next_coupon_date,
        total_back=quote.total_back,
        gain=quote.gain,
    )


def check_purchase(issue: BondIssue, face: Money, on: date, largest: Money) -> Refusal | None:
    """Whether a client may buy `face` of `issue` on `on`. `None` means yes."""
    if issue.status is not BondStatus.LIVE:
        return Refusal.ISSUE_NOT_OPEN
    if face.currency is not issue.currency:
        return Refusal.CURRENCY_NOT_SUPPORTED
    if not face.is_positive:
        return Refusal.AMOUNT_NOT_POSITIVE
    if face < issue.minimum_face:
        return Refusal.BELOW_MINIMUM
    if face.minor % issue.face_step.minor:
        return Refusal.NOT_A_STEP
    if face > largest:
        return Refusal.ABOVE_MAXIMUM
    if len(remaining_coupons(issue, on)) < 2:
        # In the last period a buyer would wait for nothing but the face value, and the
        # dealing desk may not find the bond to buy before it matures.
        return Refusal.TOO_CLOSE_TO_MATURITY
    return None


def remaining_coupons(issue: BondIssue, on: date) -> tuple[date, ...]:
    """The coupon dates after `on`, the maturity date last."""
    return tuple(d for d in coupon_dates(issue) if d > on)


def quote(issue: BondIssue, face: Money, price: int, on: date) -> Quote:
    """What `face` of `issue` costs at `price` on `on`, and what it pays if held to maturity."""
    _check_face(issue, face, minimum=False)
    clean = clean_amount(face, price)
    accrued = accrued_interest(issue, face, on)
    gross = coupon_gross(issue, face)
    net = gross - tax_on(issue, gross)
    left = remaining_coupons(issue, on)
    return Quote(
        face=face,
        price=price,
        clean=clean,
        accrued=accrued,
        cost=clean + accrued,
        coupon_net=net,
        coupons_left=len(left),
        next_coupon_date=left[0],
        total_back=face + Money(net.minor * len(left), face.currency),
    )


@dataclass(frozen=True, slots=True)
class Sale:
    """What a client is paid when the dealing desk sells their bond early."""

    face: Money
    price: int
    clean: Money
    accrued: Money
    tax: Money

    @property
    def proceeds(self) -> Money:
        return self.clean + self.accrued - self.tax


def sale(issue: BondIssue, face: Money, price: int, settled_on: date) -> Sale:
    """The proceeds of selling `face` at `price`, settled on `settled_on`.

    The buyer pays the clean price and the accrued interest. Tax is held back on the accrued
    interest as on a coupon (decided for now, for counsel to confirm). The clean amount rounds
    down, because it is paid out.
    """
    check_price(price)
    _check_face(issue, face, minimum=False)
    clean = face.mul_ratio(price, PRICE_SCALE, PROCEEDS_ROUNDING)
    accrued = accrued_interest(issue, face, settled_on)
    return Sale(face=face, price=price, clean=clean, accrued=accrued, tax=tax_on(issue, accrued))


def price_label(price: int) -> str:
    """98.5 per 100 as "98.5000"."""
    check_price(price)
    return f"{price // 10_000}.{price % 10_000:04d}"


def coupon_label(coupon_bps: int, tax_bps: int) -> str:
    """The yearly coupon after tax as clients see it, two places, rounded down: "14.40"."""
    hundredths = coupon_bps * (BASIS_POINTS - tax_bps) // BASIS_POINTS
    return f"{hundredths // 100}.{hundredths % 100:02d}"
