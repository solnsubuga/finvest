"""What a Treasury bill bought at a discount yields over its life, from what was paid. Pure.

A bill pays its face value at maturity and is bought for less. The simple yearly yield is the
discount over the cash paid, scaled from the days held to a 365 day year:

    yield = (face - paid) / paid * 365 / days

Worked out in whole basis points with integer arithmetic and rounded down, so it never shows
more than was earned. It is before tax: for staff setting rates, never for a client.
"""

from datetime import date

BASIS_POINTS = 10_000
DAYS_IN_YEAR = 365


class InvalidPurchase(ValueError):
    """A purchase whose yield cannot be worked out."""


def yearly_yield_bps(face_minor: int, paid_minor: int, settled_on: date, matures_on: date) -> int:
    days = (matures_on - settled_on).days
    if days <= 0:
        raise InvalidPurchase("A bill matures after it settles")
    if paid_minor <= 0 or face_minor < paid_minor:
        raise InvalidPurchase("A bill is bought for a positive amount no more than its face value")
    return ((face_minor - paid_minor) * DAYS_IN_YEAR * BASIS_POINTS) // (paid_minor * days)
