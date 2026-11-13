"""Bills held at the custodian, and what selling or maturing them does. Pure: no I/O.

A lot is one purchase: a face value, what was paid for it and the day it matures. Cost is the
figure the position and the ledger carry. Nothing is revalued day to day. The difference
between cost and what comes back is recognised only when cash arrives, at a sale or at maturity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.shared.money import Money, Rounding


class InvalidLot(ValueError):
    """A lot, sale or maturity that contradicts itself."""


@dataclass(frozen=True, slots=True)
class Lot:
    # What the issuer pays at maturity.
    face: Money
    # What was paid for the face still held.
    cost: Money
    matures_on: date

    def __post_init__(self) -> None:
        if self.face.currency is not self.cost.currency:
            raise InvalidLot("Face value and cost are in one currency")
        if not self.face.is_positive or not self.cost.is_positive:
            raise InvalidLot("A lot has a positive face value and cost")


@dataclass(frozen=True, slots=True)
class Sale:
    # Cost that leaves the books with the face sold.
    cost_released: Money
    # Proceeds minus that cost. Negative when the bills were sold for less than they cost.
    result: Money
    # What is still held, or `None` when the whole lot was sold.
    remaining: Lot | None


def sell(lot: Lot, face_sold: Money, proceeds: Money) -> Sale:
    """Sell part or all of a lot on the secondary market.

    The cost released is the lot's cost in proportion to the face sold, rounded down. Selling
    everything that is left releases every remaining shilling of cost, so the parts always add
    up to what was paid and a lot never ends with cost and no face.
    """
    if not face_sold.is_positive or face_sold > lot.face:
        raise InvalidLot("The face sold is above zero and at most the face held")
    if not proceeds.is_positive or proceeds.currency is not lot.cost.currency:
        raise InvalidLot("Proceeds are a positive amount in the lot's currency")
    if face_sold == lot.face:
        return Sale(cost_released=lot.cost, result=proceeds - lot.cost, remaining=None)
    released = lot.cost.mul_ratio(face_sold.minor, lot.face.minor, Rounding.DOWN)
    remaining = Lot(lot.face - face_sold, lot.cost - released, lot.matures_on)
    return Sale(cost_released=released, result=proceeds - released, remaining=remaining)


def income_at_maturity(lot: Lot, on: date) -> Money:
    """What the lot earns when the issuer pays its face value. Refused before the maturity date.

    Negative only for a bond that was bought above its face value.
    """
    if on < lot.matures_on:
        raise InvalidLot("A lot is not paid before it matures")
    return lot.face - lot.cost


def face_for_cost(cost: Money, price_per_million: int, lot_size: Money) -> Money:
    """The face value to bid for so that the bills bought cost at least `cost`.

    `price_per_million` is what one million of face value is expected to cost: 970,000 for a
    bill priced at 97.00. The answer is rounded up to a whole number of `lot_size`, the smallest
    step the auction accepts, because a bid one step short leaves client money without a bill.
    """
    if type(price_per_million) is not int or price_per_million <= 0:
        raise InvalidLot("The price is a whole positive number per million of face value")
    if not lot_size.is_positive or lot_size.currency is not cost.currency:
        raise InvalidLot("The lot size is a positive amount in the same currency")
    if cost.is_negative:
        raise InvalidLot("The cost to cover cannot be negative")
    face = cost.mul_ratio(1_000_000, price_per_million, Rounding.UP)
    lots = -(-face.minor // lot_size.minor)
    return Money(lots * lot_size.minor, cost.currency)
