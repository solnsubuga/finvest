"""An investment: one payment by one client into one product. Pure: no I/O.

    awaiting_payment ──► active
           │
           └─► cancelled

* `awaiting_payment`: the client reviewed and confirmed. The terms are fixed and nothing has
  been paid. There is no wallet, so exactly one payment, for exactly this amount, starts it.
* `active`: the payment was confirmed and the money is in the client's holding. It earns
  interest from the next day.
* `cancelled`: it was not paid for in time. Final. No money ever moved.

Which day an investment starts on matters, because interest is counted from it. The start date
is the business day (Kampala) on which the payment was confirmed. It is not the day of the
review, and not the day the start happened to be processed: if the worker is slow or down, the
client still earns from the day their money arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.modules.investments.domain.interest import maturity_date
from app.shared.money import Money

# How long a confirmed review stays payable at the terms the client saw. A bank transfer can
# take a couple of days to arrive and be matched.
PAYMENT_WINDOW = timedelta(hours=72)
# An unpaid investment is cancelled this long after the window closes, so a payment approved in
# the window's last minutes can still be confirmed and start it.
CANCEL_GRACE = timedelta(hours=1)
# Investments one client may have waiting for payment at once.
MAX_AWAITING_PAYMENT = 5


class InvestmentStatus(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    ACTIVE = "active"
    CANCELLED = "cancelled"


class AtMaturity(StrEnum):
    """What the client wants done when a fixed term ends. Rolling over is the default."""

    ROLL_OVER = "roll_over"
    PAY_OUT = "pay_out"


class StartRefusal(StrEnum):
    NOT_AWAITING_PAYMENT = "not_awaiting_payment"
    # The balance waiting for this investment is not exactly its amount.
    PAYMENT_DOES_NOT_MATCH = "payment_does_not_match"
    # A confirmation dated before the review, or in the future, is a fault somewhere.
    CONFIRMATION_DATE_IMPOSSIBLE = "confirmation_date_impossible"


def pay_by(created_at: datetime) -> datetime:
    return created_at + PAYMENT_WINDOW


def can_be_paid(status: InvestmentStatus, pay_by_time: datetime, now: datetime) -> bool:
    """Whether a new payment may be taken for the investment."""
    return status is InvestmentStatus.AWAITING_PAYMENT and now < pay_by_time


def can_be_cancelled(
    status: InvestmentStatus, pay_by_time: datetime, now: datetime, waiting: Money
) -> bool:
    """Whether the sweep may cancel it: unpaid, past the window and its grace, and with no
    money waiting. Money that did arrive is never stranded behind a cancelled investment."""
    return (
        status is InvestmentStatus.AWAITING_PAYMENT
        and now >= pay_by_time + CANCEL_GRACE
        and waiting.is_zero
    )


@dataclass(frozen=True, slots=True)
class Start:
    start_date: date
    # None for an open term.
    maturity_date: date | None


def check_start(
    status: InvestmentStatus,
    *,
    amount: Money,
    waiting: Money,
    created_on: date,
    confirmed_on: date,
    today: date,
) -> StartRefusal | None:
    """Whether a confirmed payment may start the investment. `None` means yes.

    `waiting` is the balance of the client's awaiting-start account for this investment. It
    must be the whole amount and nothing more: a part payment does not start a smaller
    investment, and an overpayment is refunded by staff, never kept.
    """
    if status is not InvestmentStatus.AWAITING_PAYMENT:
        return StartRefusal.NOT_AWAITING_PAYMENT
    if waiting != amount:
        return StartRefusal.PAYMENT_DOES_NOT_MATCH
    if not created_on <= confirmed_on <= today:
        return StartRefusal.CONFIRMATION_DATE_IMPOSSIBLE
    return None


def start(confirmed_on: date, term_days: int | None) -> Start:
    """The dates of an investment whose payment was confirmed on `confirmed_on`."""
    return Start(
        start_date=confirmed_on,
        maturity_date=None if term_days is None else maturity_date(confirmed_on, term_days),
    )
