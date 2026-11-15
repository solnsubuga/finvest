"""Selling a holding early, through the dealing desk. Pure: no I/O.

A client who wants their money before maturity asks Finvest to sell their whole holding. The
dealing desk sells that face value on the secondary market and one member of staff records the
price it fetched and the day it settled. A second member of staff approves it, and the worker
pays the client (`app/modules/bonds/sales.py`):

    requested ──► priced ──► approved ──► sold
        │  ▲         │
        │  └─────────┘ sent back, or withdrawn by whoever priced it
        ├─► cancelled  by the client, before it is priced
        └─► closed     the bond matured first, and was repaid instead

The client is paid the clean price and the accrued interest to the day the sale settled, less
tax at the issue's rate on the accrued interest (`issues.sale`). The price itself is not taxed.

What the client paid comes out of their bond holding, as at maturity, and none of it is
interest paid to them:

* the accrued interest they paid at purchase goes to `bond.accrued_interest_bought`, Finvest's
  income, as at maturity: the accrued interest they are paid now, or a coupon before it, covers
  the same days.
* the clean amount they are paid less the clean amount they paid is Finvest's expense when the
  bond sold for more (`bond.sold_above_cost`), and its income the other way round
  (`bond.sold_below_cost`), so every ledger balance stays at or above zero.

A sale settles inside one coupon period. A sale dated before a coupon date that has passed is
refused, because the holding was paid that coupon while it waited, and the buyer gets it. Once
a sale is approved, the coupon run and the maturity run leave the holding alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.bonds.domain.issues import BondIssue, Sale, coupon_dates, sale
from app.shared.money import Money

MAX_TEXT_LENGTH = 500


class SaleStatus(StrEnum):
    REQUESTED = "requested"
    PRICED = "priced"
    APPROVED = "approved"
    SOLD = "sold"
    CANCELLED = "cancelled"
    CLOSED = "closed"


# A sale that is still going: one per holding at a time, and none after one is sold.
OPEN = frozenset({SaleStatus.REQUESTED, SaleStatus.PRICED, SaleStatus.APPROVED})


class PayoutStatus:
    PAID_OUT = "paid_out"
    HELD = "held"


class SaleRefusal(StrEnum):
    # Only a holding that is paid for and has not matured or been sold is sold.
    NOT_ACTIVE = "holding_not_active"
    MATURED = "matured"
    # One sale at a time, and a holding is sold once.
    ALREADY_ASKED = "sale_already_asked"
    SOLD_ON_IN_FUTURE = "sold_on_in_future"
    SOLD_ON_BEFORE_START = "sold_on_before_start"
    SOLD_ON_AT_MATURITY = "sold_on_at_maturity"
    # A coupon date passed between the sale and now: the holding was paid that coupon.
    COUPON_SINCE = "coupon_since_sale"
    PRICE_OUT_OF_RANGE = "price_out_of_range"
    TEXT_REQUIRED = "required"
    TEXT_TOO_LONG = "too_long"
    NOT_PRICED = "not_priced"
    NOT_REQUESTED = "not_requested"
    # Two people: the one who priced a sale cannot approve or send it back, and only they can
    # withdraw it.
    SAME_PERSON = "same_person"
    NOT_YOURS = "not_yours"


def check_request(issue: BondIssue, start_date: date | None, today: date) -> SaleRefusal | None:
    """Whether a client may ask to sell a holding today."""
    if start_date is None:
        return SaleRefusal.NOT_ACTIVE
    if today >= issue.maturity_date:
        return SaleRefusal.MATURED
    return None


def check_sold_on(
    issue: BondIssue, start_date: date | None, sold_on: date, today: date
) -> SaleRefusal | None:
    """Whether a sale can be recorded, or approved, as settled on `sold_on`, on `today`."""
    if sold_on > today:
        return SaleRefusal.SOLD_ON_IN_FUTURE
    if start_date is None or sold_on < start_date:
        return SaleRefusal.SOLD_ON_BEFORE_START
    if sold_on >= issue.maturity_date:
        return SaleRefusal.SOLD_ON_AT_MATURITY
    if any(sold_on < day <= today for day in coupon_dates(issue)):
        return SaleRefusal.COUPON_SINCE
    return None


def check_text(value: str | None) -> SaleRefusal | None:
    if value is None or not value.strip():
        return SaleRefusal.TEXT_REQUIRED
    if len(value) > MAX_TEXT_LENGTH:
        return SaleRefusal.TEXT_TOO_LONG
    return None


@dataclass(frozen=True, slots=True)
class Settlement:
    """How a sale is paid, and how what the client paid comes out of their holding."""

    cost: Money
    # The accrued interest the client paid at purchase.
    bought_accrued: Money
    sale: Sale

    @property
    def clean(self) -> Money:
        return self.sale.clean

    @property
    def accrued(self) -> Money:
        return self.sale.accrued

    @property
    def tax(self) -> Money:
        return self.sale.tax

    @property
    def paid(self) -> Money:
        """What the client is paid: the clean price and accrued interest, less tax on it."""
        return self.sale.proceeds

    @property
    def bought_clean(self) -> Money:
        return self.cost - self.bought_accrued

    @property
    def above_cost(self) -> Money:
        """The clean price less the clean amount the client paid, when it sold for more."""
        difference = self.clean - self.bought_clean
        return difference if difference.is_positive else Money.zero(self.cost.currency)

    @property
    def below_cost(self) -> Money:
        """The clean amount the client paid less the clean price, when it sold for less."""
        difference = self.bought_clean - self.clean
        return difference if difference.is_positive else Money.zero(self.cost.currency)


def settlement(
    issue: BondIssue, face: Money, cost: Money, bought_accrued: Money, price: int, sold_on: date
) -> Settlement:
    return Settlement(
        cost=cost, bought_accrued=bought_accrued, sale=sale(issue, face, price, sold_on)
    )
