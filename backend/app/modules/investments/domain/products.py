"""The product catalogue: what a client can invest in. Pure: no I/O.

A product says what the instrument is, its currency, how long money stays in and the smallest
amount accepted. It does not hold a rate. Rates change over time and each change is approved,
so they live in `rates.py` as dated versions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.shared.money import Currency, Money

# Short upper-case codes staff recognise, such as "TREA-91".
_CODE = re.compile(r"[A-Z][A-Z0-9]*(-[A-Z0-9]+)*")
MAX_CODE_LENGTH = 24
MAX_TERM_DAYS = 3650


class InvalidProduct(ValueError):
    """A product that contradicts itself."""


class ProductKind(StrEnum):
    TREASURY_BILL = "treasury_bill"
    FIXED_INCOME = "fixed_income"


class InterestPaid(StrEnum):
    """When the interest a client has earned is handed over. It is earned daily either way."""

    AT_MATURITY = "at_maturity"
    MONTHLY = "monthly"


class ProductStatus(StrEnum):
    # Not yet offered. Staff are still setting it up.
    DRAFT = "draft"
    # Clients can start new investments.
    LIVE = "live"
    # No new investments. Running ones carry on to maturity.
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class Product:
    code: str
    name: str
    kind: ProductKind
    currency: Currency
    # Days from start to maturity. `None` is an open term: it runs until the client withdraws.
    term_days: int | None
    minimum: Money
    interest_paid: InterestPaid
    status: ProductStatus = ProductStatus.LIVE

    def __post_init__(self) -> None:
        if len(self.code) > MAX_CODE_LENGTH or _CODE.fullmatch(self.code) is None:
            raise InvalidProduct("A product code is upper-case letters, digits and hyphens")
        if not self.name.strip() or len(self.name) > 80 or self.name != self.name.strip():
            raise InvalidProduct("A product name is 1 to 80 characters")
        if not isinstance(self.kind, ProductKind) or not isinstance(self.currency, Currency):
            raise InvalidProduct("Unknown product kind or currency")
        if not isinstance(self.interest_paid, InterestPaid):
            raise InvalidProduct("Unknown interest schedule")
        if not isinstance(self.status, ProductStatus):
            raise InvalidProduct("Unknown product status")
        if self.term_days is not None and (
            type(self.term_days) is not int or not 1 <= self.term_days <= MAX_TERM_DAYS
        ):
            raise InvalidProduct(f"A term is between 1 and {MAX_TERM_DAYS} days")
        if not isinstance(self.minimum, Money) or self.minimum.currency is not self.currency:
            raise InvalidProduct("The minimum is in the product's currency")
        if not self.minimum.is_positive:
            raise InvalidProduct("The minimum is a positive amount")
        if self.term_days is None and self.interest_paid is InterestPaid.AT_MATURITY:
            raise InvalidProduct("An open term has no maturity to pay interest at")

    @property
    def has_fixed_term(self) -> bool:
        return self.term_days is not None


class Refusal(StrEnum):
    PRODUCT_NOT_OPEN = "product_not_open"
    CURRENCY_NOT_SUPPORTED = "currency_not_supported"
    AMOUNT_NOT_POSITIVE = "amount_not_positive"
    BELOW_MINIMUM = "below_minimum"


def check_new_investment(product: Product, amount: Money) -> Refusal | None:
    """Whether a client may start an investment of `amount` in `product`. `None` means yes.

    There is no wallet, so the amount is the whole payment: it is never topped up from a
    balance and must meet the minimum by itself.
    """
    if product.status is not ProductStatus.LIVE:
        return Refusal.PRODUCT_NOT_OPEN
    if amount.currency is not product.currency:
        return Refusal.CURRENCY_NOT_SUPPORTED
    if not amount.is_positive:
        return Refusal.AMOUNT_NOT_POSITIVE
    if amount < product.minimum:
        return Refusal.BELOW_MINIMUM
    return None


# The three products Finvest 1.0 sold. The catalogue is seeded with these.
# The dollar product is seeded paused: paying in and out in dollars is not built yet.
SEED_PRODUCTS: tuple[Product, ...] = (
    Product(
        code="TREA-91",
        name="91 Day Treasury Bill",
        kind=ProductKind.TREASURY_BILL,
        currency=Currency.UGX,
        term_days=91,
        minimum=Money(100_000, Currency.UGX),
        interest_paid=InterestPaid.AT_MATURITY,
    ),
    Product(
        code="TREA-182",
        name="182 Day Treasury Bill",
        kind=ProductKind.TREASURY_BILL,
        currency=Currency.UGX,
        term_days=182,
        minimum=Money(200_000, Currency.UGX),
        interest_paid=InterestPaid.AT_MATURITY,
    ),
    Product(
        code="USD-BOND",
        name="USD Fixed Income",
        kind=ProductKind.FIXED_INCOME,
        currency=Currency.USD,
        term_days=None,
        minimum=Money(500_00, Currency.USD),
        interest_paid=InterestPaid.MONTHLY,
        status=ProductStatus.PAUSED,
    ),
)
