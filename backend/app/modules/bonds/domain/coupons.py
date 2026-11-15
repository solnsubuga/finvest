"""Coupons on a holding: which are due on a day and what each pays. Pure: no I/O.

A holding is paid every coupon dated after the business day it started, because the client
paid the interest accrued up to the quote and the coupon pays the whole period. The last
coupon falls on the maturity date and is paid with the face value at maturity, not by the
coupon run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.modules.bonds.domain.issues import BondIssue, coupon_dates, coupon_gross, tax_on
from app.shared.money import Money


@dataclass(frozen=True, slots=True)
class Coupon:
    gross: Money
    tax: Money

    @property
    def net(self) -> Money:
        return self.gross - self.tax


class HoldReason:
    # The client has not said where coupons go.
    NO_DESTINATION = "no_destination"


def coupon(issue: BondIssue, face: Money) -> Coupon:
    """One coupon on `face`: before tax, and the tax withheld from it."""
    gross = coupon_gross(issue, face)
    return Coupon(gross=gross, tax=tax_on(issue, gross))


def is_coupon_run_day(issue: BondIssue, on: date) -> bool:
    """Whether the coupon run pays this issue on `on`: a coupon date before maturity."""
    return on < issue.maturity_date and on in coupon_dates(issue)


def is_owed(start_date: date | None, coupon_date: date) -> bool:
    """Whether a holding that started on `start_date` is paid the coupon dated `coupon_date`."""
    return start_date is not None and start_date < coupon_date
