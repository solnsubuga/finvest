"""The latest bills Finvest bought, per product, for staff setting rates.

Read from the settled dealing orders: what was bought, what was paid and when it matures, and
the yearly yield that works out to before tax. The Rates screen shows it beside the rate in
force, so a person proposing a new rate can see what the bills behind it last cost. It only
reads.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealing.domain.bought_yield import InvalidPurchase, yearly_yield_bps
from app.shared.money import Currency, Money

_LATEST = text(
    "SELECT DISTINCT ON (product_code) product_code, kind, currency, result_face_minor, "
    "result_amount_minor, result_settled_on, result_matures_on FROM dealing_orders "
    "WHERE status = 'settled' AND kind IN ('auction_bid', 'secondary_purchase') "
    "ORDER BY product_code, result_settled_on DESC, settled_at DESC, id DESC"
)


@dataclass(frozen=True, slots=True)
class LastBuy:
    product_code: str
    at_auction: bool
    face: Money
    paid: Money
    settled_on: date
    matures_on: date
    # Before tax. None when the figures cannot give one.
    yearly_yield_bps: int | None


async def last_buys(session: AsyncSession) -> list[LastBuy]:
    """The latest settled purchase of each product, by product code."""
    found: list[LastBuy] = []
    for row in (await session.execute(_LATEST)).all():
        currency = Currency(row.currency)
        try:
            bps: int | None = yearly_yield_bps(
                int(row.result_face_minor),
                int(row.result_amount_minor),
                row.result_settled_on,
                row.result_matures_on,
            )
        except InvalidPurchase:
            bps = None
        found.append(
            LastBuy(
                product_code=row.product_code,
                at_auction=row.kind == "auction_bid",
                face=Money(int(row.result_face_minor), currency),
                paid=Money(int(row.result_amount_minor), currency),
                settled_on=row.result_settled_on,
                matures_on=row.result_matures_on,
                yearly_yield_bps=bps,
            )
        )
    return found
