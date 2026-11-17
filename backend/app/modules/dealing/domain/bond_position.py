"""The desk's position in each bond issue: the face value clients hold against the face value
held at the custodian. Pure.

A client's bond holding starts the moment their payment is confirmed. The desk then buys that
face value of the same issue, at an auction or on the secondary market, and holds it until the
issue matures or a client sells. Unlike bills, bonds are measured by face value: every bond of an
issue pays the same coupons and the same face value at maturity, whatever price it was bought
at, so a holding is covered exactly when the desk holds its face value of that issue.

Client money for bonds not yet bought is in the trust bank account. It is not the buffer's to
lend: `to_place` is the share of what clients paid that has no bond behind it yet, rounded up
to the shilling so the buffer is never overstated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.modules.dealing.domain.position import Cover, InvalidPosition
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class BondPosition:
    code: str
    # Face value of every active client holding of the issue, and what those clients paid.
    clients_face: Money
    clients_paid: Money
    # Face value and cost held at the custodian.
    face_held: Money
    cost_held: Money
    # Open orders to buy, and open orders to sell, at face value.
    on_order: Money
    on_sale: Money

    def __post_init__(self) -> None:
        if not self.code:
            raise InvalidPosition("A position is for one issue")
        currency = self.clients_face.currency
        figures = (
            self.clients_face,
            self.clients_paid,
            self.face_held,
            self.cost_held,
            self.on_order,
            self.on_sale,
        )
        for amount in figures:
            if amount.currency is not currency:
                raise InvalidPosition("A position is in one currency")
            if amount.is_negative:
                raise InvalidPosition("Position figures are never negative")
        if self.clients_face.is_zero != self.clients_paid.is_zero:
            raise InvalidPosition("Clients paid for exactly the face value they hold")
        if self.on_sale > self.face_held:
            raise InvalidPosition("Nothing is sold that is not held")

    @property
    def currency(self) -> Currency:
        return self.clients_face.currency

    @property
    def to_buy(self) -> Money:
        """Face value clients hold with no bond behind it."""
        return _at_least_zero(self.clients_face - self.face_held)

    @property
    def uncovered(self) -> Money:
        """What is still to buy once the orders already prepared have settled."""
        return _at_least_zero(self.to_buy - self.on_order)

    @property
    def surplus(self) -> Money:
        """Face value held beyond what clients hold: after a client sold or a holding ended."""
        return _at_least_zero(self.face_held - self.clients_face)

    @property
    def to_sell(self) -> Money:
        """The surplus not already in an open sale."""
        return _at_least_zero(self.surplus - self.on_sale)

    @property
    def cover(self) -> Cover:
        if self.surplus.is_positive:
            return Cover.SURPLUS
        if self.to_buy.is_zero:
            return Cover.COVERED
        return Cover.BID_NEEDED if self.uncovered.is_positive else Cover.ON_ORDER

    @property
    def to_place(self) -> Money:
        """Client money for this issue with no bond behind it yet, rounded up."""
        if self.to_buy.is_zero:
            return Money.zero(self.currency)
        # to_buy is positive, so clients hold some face value and paid for it.
        share = -(-self.clients_paid.minor * self.to_buy.minor // self.clients_face.minor)
        return Money(share, self.currency)


def bonds_to_place(positions: Iterable[BondPosition], currency: Currency) -> Money:
    """Client money in `currency` waiting for bonds, across issues. A surplus in one issue never
    stands behind a holding of another."""
    total = Money.zero(currency)
    seen: set[str] = set()
    for position in positions:
        if position.code in seen:
            raise InvalidPosition("An issue appears once in the position")
        seen.add(position.code)
        if position.currency is currency:
            total += position.to_place
    return total


def _at_least_zero(amount: Money) -> Money:
    return amount if amount.is_positive else Money.zero(amount.currency)
