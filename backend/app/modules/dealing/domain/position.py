"""The desk's daily position: what clients are owed against the bills actually held. Pure.

A client's investment starts the moment the payment is confirmed. The bill behind it is bought
later, at the next auction or on the secondary market. The position says, product by product,
how much client money still has no bill behind it.

Both sides are measured the same way, decided for now (see docs/decisions.md): clients are owed
the principal of their running investments, and bills are held at what was paid for them. Neither
side counts interest, so a bill bought for exactly the money paid in covers it on every day.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.shared.money import Currency, Money


class InvalidPosition(ValueError):
    """Figures that cannot describe a position."""


class Cover(StrEnum):
    # Every shilling clients paid in has a bill behind it, and nothing is left over.
    COVERED = "covered"
    # Client money is waiting and no order has been prepared for it.
    BID_NEEDED = "bid_needed"
    # Client money is waiting, and orders already prepared will cover it.
    ON_ORDER = "on_order"
    # More bills than clients are owed. This happens after an early withdrawal is paid from
    # the liquidity buffer. New client money is covered by these bills before any are bought.
    SURPLUS = "surplus"


@dataclass(frozen=True, slots=True)
class ProductPosition:
    product_code: str
    # Principal of every running investment in the product.
    clients_owed: Money
    # Cost of the bills held for the product.
    bills_held: Money
    # Expected cost of orders to buy that are prepared or approved and not yet settled.
    on_order: Money

    def __post_init__(self) -> None:
        if not self.product_code:
            raise InvalidPosition("A position is for one product")
        currency = self.clients_owed.currency
        for amount in (self.clients_owed, self.bills_held, self.on_order):
            if amount.currency is not currency:
                raise InvalidPosition("A position is in one currency")
            if amount.is_negative:
                raise InvalidPosition("Position figures are never negative")

    @property
    def currency(self) -> Currency:
        return self.clients_owed.currency

    @property
    def to_buy(self) -> Money:
        """Client money with no bill behind it."""
        return _at_least_zero(self.clients_owed - self.bills_held)

    @property
    def uncovered(self) -> Money:
        """What is still to buy once the orders already prepared have settled."""
        return _at_least_zero(self.to_buy - self.on_order)

    @property
    def surplus(self) -> Money:
        """Bills held beyond what clients are owed."""
        return _at_least_zero(self.bills_held - self.clients_owed)

    @property
    def cover(self) -> Cover:
        if self.surplus.is_positive:
            return Cover.SURPLUS
        if self.to_buy.is_zero:
            return Cover.COVERED
        return Cover.BID_NEEDED if self.uncovered.is_positive else Cover.ON_ORDER


def money_to_place(positions: Iterable[ProductPosition], currency: Currency) -> Money:
    """Client money in `currency` that is waiting to be placed, across products.

    A surplus in one product never offsets a gap in another: a 91 day bill does not stand
    behind a 182 day investment.
    """
    total = Money.zero(currency)
    seen: set[str] = set()
    for position in positions:
        if position.product_code in seen:
            raise InvalidPosition("A product appears once in the position")
        seen.add(position.product_code)
        if position.currency is currency:
            total += position.to_buy
    return total


def _at_least_zero(amount: Money) -> Money:
    return amount if amount.is_positive else Money.zero(amount.currency)
