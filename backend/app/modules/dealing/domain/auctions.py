"""The Treasury bill auction calendar: which dates may be entered, and each product's next
auction. Pure: no I/O.

The Bank of Uganda auctions Treasury bills on dates it publishes. Staff enter each date for a
product, so the desk can say when client money waiting for a bill can next be placed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Refusal(StrEnum):
    NOT_A_TREASURY_BILL = "not_a_treasury_bill"
    DATE_PASSED = "auction_date_passed"
    ALREADY_ENTERED = "auction_already_entered"
    ALREADY_CANCELLED = "already_cancelled"
    REASON_REQUIRED = "reason_required"


@dataclass(frozen=True, slots=True)
class AuctionDate:
    product_code: str
    auction_date: date
    cancelled: bool


def check_new(
    product_code: str,
    auction_date: date,
    *,
    treasury_bill: bool,
    today: date,
    existing: Iterable[AuctionDate],
) -> Refusal | None:
    """Whether a date may be entered for a product. Today counts: bids close during the day."""
    if not treasury_bill:
        return Refusal.NOT_A_TREASURY_BILL
    if auction_date < today:
        return Refusal.DATE_PASSED
    if any(
        a.product_code == product_code and a.auction_date == auction_date and not a.cancelled
        for a in existing
    ):
        return Refusal.ALREADY_ENTERED
    return None


def check_cancel(auction: AuctionDate, reason: str) -> Refusal | None:
    if auction.cancelled:
        return Refusal.ALREADY_CANCELLED
    if not reason.strip():
        return Refusal.REASON_REQUIRED
    return None


def next_auctions(dates: Iterable[AuctionDate], today: date) -> dict[str, date]:
    """Each product's earliest standing auction on or after today."""
    found: dict[str, date] = {}
    for a in dates:
        if a.cancelled or a.auction_date < today:
            continue
        if a.product_code not in found or a.auction_date < found[a.product_code]:
            found[a.product_code] = a.auction_date
    return found
