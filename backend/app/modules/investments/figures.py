"""Totals across every client's investments, for the console's Overview and reports. Read only.

Nothing here names a client: each figure adds every client together.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class ProductTotal:
    """Every running investment in one product."""

    product_code: str
    product_name: str
    investments: int
    clients: int
    principal: Money


@dataclass(frozen=True, slots=True)
class MaturingDay:
    """Every running term in one currency whose maturity date is `day`, early withdrawals left
    out, by what the clients chose. A term with no choice rolls over."""

    day: date
    investments: int
    principal: Money
    # The principal of terms set to pay out: the cash that leaves, before interest.
    pay_out_principal: Money


_RUNNING_BY_PRODUCT = text(
    "SELECT p.code, p.name, count(*) AS investments, count(DISTINCT i.client_id) AS clients, "
    "CAST(sum(i.principal_minor) AS bigint) AS principal_minor "
    "FROM investments i JOIN investment_products p ON p.id = i.product_id "
    "WHERE i.status = 'active' AND i.currency = :currency "
    "GROUP BY p.code, p.name ORDER BY sum(i.principal_minor) DESC, p.code"
)
_RUNNING_CLIENTS = text(
    "SELECT count(DISTINCT i.client_id) AS clients FROM investments i WHERE i.status = 'active'"
)
_MATURING = text(
    "SELECT i.maturity_date AS day, count(*) AS investments, "
    "CAST(sum(i.principal_minor) AS bigint) AS principal_minor, "
    "CAST(coalesce(sum(i.principal_minor) FILTER (WHERE i.at_maturity = 'pay_out'), 0) "
    "AS bigint) AS pay_out_minor "
    "FROM investments i "
    "WHERE i.status = 'active' AND i.withdrawal_requested_at IS NULL "
    "AND i.currency = :currency AND i.maturity_date BETWEEN :date_from AND :date_to "
    "GROUP BY i.maturity_date ORDER BY i.maturity_date"
)


def _staff(actor: Actor) -> None:
    if actor.kind not in ("staff", "system"):
        raise ApplicationError("forbidden")


async def running_by_product(
    session: AsyncSession, staff: Actor, currency: Currency
) -> list[ProductTotal]:
    """Running investments per product, the largest first."""
    _staff(staff)
    rows = await session.execute(_RUNNING_BY_PRODUCT, {"currency": currency.value})
    return [
        ProductTotal(
            product_code=row.code,
            product_name=row.name,
            investments=int(row.investments),
            clients=int(row.clients),
            principal=Money(int(row.principal_minor), currency),
        )
        for row in rows.all()
    ]


async def running_clients(session: AsyncSession, staff: Actor) -> int:
    """Clients with at least one running investment, in any currency."""
    _staff(staff)
    return int((await session.execute(_RUNNING_CLIENTS)).one().clients)


async def maturing(
    session: AsyncSession, staff: Actor, currency: Currency, *, date_from: date, date_to: date
) -> list[MaturingDay]:
    """Running terms maturing from `date_from` to `date_to` inclusive, per day."""
    _staff(staff)
    rows = await session.execute(
        _MATURING, {"currency": currency.value, "date_from": date_from, "date_to": date_to}
    )
    return [
        MaturingDay(
            day=row.day,
            investments=int(row.investments),
            principal=Money(int(row.principal_minor), currency),
            pay_out_principal=Money(int(row.pay_out_minor), currency),
        )
        for row in rows.all()
    ]
