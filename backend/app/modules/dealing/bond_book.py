"""The dealing desk's bonds: its position in each issue, from the bonds catalogue, the clients'
holdings and the lots held at the custodian. Queries only, inside the caller's transaction.

Staff and the worker read every holding, so both can work out the buffer.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bonds import catalogue
from app.modules.bonds import holdings as clients
from app.modules.dealing import holdings, orders
from app.modules.dealing.domain.bond_position import BondPosition
from app.modules.dealing.domain.orders import Instrument
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class BondLine:
    code: str
    isin: str
    name: str
    maturity_date: date
    # Active client holdings of the issue.
    holders: int
    position: BondPosition


async def bond_positions(
    session: AsyncSession,
    currency: Currency,
    on: date,
    *,
    open_orders: list[orders.OrderView] | None = None,
) -> list[BondLine]:
    """The position in every issue in `currency` that has not matured by `on`, or that clients
    or the desk still hold, soonest maturity first. `open_orders` saves reading them again."""
    owed = {
        each.code: each
        for each in await clients.owed_by_issue(session)
        if each.face.currency is currency
    }
    face: defaultdict[str, int] = defaultdict(int)
    cost: defaultdict[str, int] = defaultdict(int)
    for holding in await holdings.holdings_on(session, on):
        if holding.instrument is Instrument.BOND and holding.currency is currency:
            face[holding.product_code] += holding.face_minor
            cost[holding.product_code] += holding.cost_minor
    buying: defaultdict[str, int] = defaultdict(int)
    selling: defaultdict[str, int] = defaultdict(int)
    for order in open_orders if open_orders is not None else await orders.open_orders(session):
        if order.instrument is Instrument.BOND and order.face.currency is currency:
            (buying if order.kind.buys else selling)[order.product_code] += order.face.minor
    zero = Money.zero(currency)
    lines = []
    for view in await catalogue.every_issue(session):
        issue = view.issue
        code = issue.code
        if issue.currency is not currency:
            continue
        if issue.maturity_date <= on and code not in owed and not face[code]:
            continue
        held = owed.get(code)
        lines.append(
            BondLine(
                code=code,
                isin=issue.isin,
                name=issue.name,
                maturity_date=issue.maturity_date,
                holders=held.holders if held else 0,
                position=BondPosition(
                    code=code,
                    clients_face=held.face if held else zero,
                    clients_paid=held.paid if held else zero,
                    face_held=Money(face[code], currency),
                    cost_held=Money(cost[code], currency),
                    on_order=Money(buying[code], currency),
                    on_sale=Money(min(selling[code], face[code]), currency),
                ),
            )
        )
    return lines
