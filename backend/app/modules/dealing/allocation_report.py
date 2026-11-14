"""The allocation report: for each product in a currency, the bills held, the cost that stands
behind client investments, and what is left over on either side. Read inside the caller's staff
transaction. The rules are in `domain/allocation_report.py`."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealing import allocations, holdings
from app.modules.dealing.domain.allocation_report import (
    LotHeld,
    Owed,
    ProductReport,
    product_report,
)
from app.modules.investments import service as investments
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency


@dataclass(frozen=True, slots=True)
class Report:
    currency: Currency
    on: date
    products: tuple[ProductReport, ...]


async def report(session: AsyncSession, staff: Actor, currency: Currency, clock: Clock) -> Report:
    """Every product in `currency`, in the order the catalogue lists them."""
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    on = business_date(clock.now())
    products = [
        view.product
        for view in await investments.list_products(session, clock)
        if view.product.currency is currency
    ]
    found: list[ProductReport] = []
    for product in products:
        lots = [
            LotHeld(
                lot_id=held.id,
                settled_on=held.settled_on,
                matures_on=held.lot.matures_on,
                custodian_reference=held.custodian_reference,
                face=held.lot.face,
                cost=held.lot.cost,
            )
            for held in await holdings.allocatable_lots(session, product.code, on=on)
        ]
        owed = [
            Owed(running.investment_id, running.principal)
            for running in await investments.running_investments(session, product.code)
        ]
        found.append(
            product_report(
                product.code,
                currency,
                lots,
                await allocations.in_force(session, product.code),
                owed,
            )
        )
    return Report(currency, on, tuple(found))
