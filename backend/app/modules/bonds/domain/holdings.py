"""A client's holding of one bond issue: bought at a quoted price, paid once. Pure: no I/O.

    unpaid ──► active
       │
       └─► cancelled

* `unpaid`: the client confirmed a quote. The face value, price, accrued interest and cost are
  fixed, and nothing has been paid. There is no wallet, so exactly one payment, for exactly
  the cost, makes it active.
* `active`: the payment was confirmed and the cost is in the client's bond holding. It is paid
  every coupon dated after the day it started.
* `cancelled`: it was not paid for in time. Final. No money ever moved.

The client pays the accrued interest as at the day of the quote, and is then paid the whole
next coupon. So a quote holds only while that coupon is still to come: a payment confirmed on
or after the next coupon date would pay for interest the client is then not paid, and is
refused. The window is short as well, because a bond's price moves with the market.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum

from app.modules.bonds.domain.issues import BondIssue, remaining_coupons
from app.shared.money import Money

# How long a confirmed quote stays payable at the price the client saw. Mobile money and cards
# confirm in minutes. Bank transfers are not offered for bonds yet.
PAYMENT_WINDOW = timedelta(hours=24)
# An unpaid holding is cancelled this long after the window closes, so a payment approved in
# the window's last minutes can still be confirmed and start it.
CANCEL_GRACE = timedelta(hours=1)
# Bond purchases one client may have waiting for payment at once.
MAX_UNPAID = 5


class HoldingStatus(StrEnum):
    UNPAID = "unpaid"
    ACTIVE = "active"
    CANCELLED = "cancelled"


class StartRefusal(StrEnum):
    NOT_UNPAID = "not_unpaid"
    # The balance waiting for this holding is not exactly its cost.
    PAYMENT_DOES_NOT_MATCH = "payment_does_not_match"
    # A confirmation dated before the quote, or in the future, is a fault somewhere.
    CONFIRMATION_DATE_IMPOSSIBLE = "confirmation_date_impossible"
    # A coupon fell due between the quote and the payment, so the accrued interest paid is
    # for a period that coupon has already paid.
    COUPON_PASSED = "coupon_passed"


def pay_by(created_at: datetime) -> datetime:
    return created_at + PAYMENT_WINDOW


def coupon_passed(issue: BondIssue, quoted_on: date, on: date) -> bool:
    """Whether a coupon date falls after the quote and on or before `on`."""
    following = remaining_coupons(issue, quoted_on)
    return not following or following[0] <= on


def can_be_paid(
    status: HoldingStatus,
    pay_by_time: datetime,
    now: datetime,
    *,
    issue: BondIssue,
    quoted_on: date,
    today: date,
) -> bool:
    """Whether a new payment may be taken for the holding."""
    return (
        status is HoldingStatus.UNPAID
        and now < pay_by_time
        and not coupon_passed(issue, quoted_on, today)
    )


def can_be_cancelled(
    status: HoldingStatus, pay_by_time: datetime, now: datetime, waiting: Money
) -> bool:
    """Whether the sweep may cancel it: unpaid, past the window and its grace, and with no
    money waiting. Money that did arrive is never stranded behind a cancelled holding."""
    return status is HoldingStatus.UNPAID and now >= pay_by_time + CANCEL_GRACE and waiting.is_zero


def check_start(
    status: HoldingStatus,
    *,
    issue: BondIssue,
    cost: Money,
    waiting: Money,
    quoted_on: date,
    confirmed_on: date,
    today: date,
) -> StartRefusal | None:
    """Whether a confirmed payment may make the holding active. `None` means yes.

    `waiting` is the balance of the client's awaiting-start account for this holding. It must
    be the whole cost and nothing more: a part payment does not buy a smaller face value, and
    an overpayment is refunded by staff, never kept.
    """
    if status is not HoldingStatus.UNPAID:
        return StartRefusal.NOT_UNPAID
    if waiting != cost:
        return StartRefusal.PAYMENT_DOES_NOT_MATCH
    if not quoted_on <= confirmed_on <= today:
        return StartRefusal.CONFIRMATION_DATE_IMPOSSIBLE
    if coupon_passed(issue, quoted_on, confirmed_on):
        return StartRefusal.COUPON_PASSED
    return None
