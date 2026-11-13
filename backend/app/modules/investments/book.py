"""The book of running investments, as the dealing desk reads it to buy and allocate bills.

Read only. Each function works inside the caller's transaction, which is a staff session or the
worker; row security keeps a client session to the client's own rows. Interest here is net of
tax, as posted to the clients' accrued-interest accounts so far.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class ProductOwed:
    """What clients are owed on one product's running investments."""

    product_code: str
    currency: Currency
    investments: int
    principal: Money
    interest_accrued: Money


@dataclass(frozen=True, slots=True)
class RunningInvestment:
    investment_id: str
    client_id: str
    product_code: str
    principal: Money
    start_date: date
    # None for an investment without a fixed term.
    maturity_date: date | None


_RUNNING_ON = (
    "i.status = 'active' AND i.start_date <= :on "
    "AND (i.maturity_date IS NULL OR i.maturity_date > :on)"
)
_OWED = text(
    "SELECT p.code AS product_code, i.currency, count(*) AS investments, "  # noqa: S608 (constant fragments only)
    "sum(i.principal_minor) AS principal_minor, "
    "sum(i.accrued_gross_minor - i.accrued_tax_minor) AS interest_minor "
    "FROM investments i JOIN investment_products p ON p.id = i.product_id "
    f"WHERE {_RUNNING_ON} GROUP BY p.code, i.currency ORDER BY p.code, i.currency"
)
_RUNNING_COLUMNS = (
    "i.id, i.client_id, p.code AS product_code, i.currency, i.principal_minor, i.start_date, "
    "i.maturity_date FROM investments i JOIN investment_products p ON p.id = i.product_id"
)
_RUNNING = text(
    f"SELECT {_RUNNING_COLUMNS} WHERE i.status = 'active' AND p.code = :product_code "
    "AND (CAST(:started_after AS date) IS NULL OR i.start_date > :started_after) "
    "ORDER BY i.start_date, i.id"
)
_MATURING = text(
    f"SELECT {_RUNNING_COLUMNS} WHERE i.status = 'active' "
    "AND i.maturity_date BETWEEN :date_from AND :date_to ORDER BY i.maturity_date, i.id"
)


async def owed_by_product(session: AsyncSession, on: date) -> list[ProductOwed]:
    """Principal and net interest owed on investments running on `on`: started by then and not
    yet at their maturity date. One row per product and currency."""
    rows = await session.execute(_OWED, {"on": on})
    owed = []
    for row in rows.all():
        currency = Currency(row.currency)
        owed.append(
            ProductOwed(
                product_code=row.product_code,
                currency=currency,
                investments=int(row.investments),
                principal=Money(int(row.principal_minor), currency),
                interest_accrued=Money(int(row.interest_minor), currency),
            )
        )
    return owed


async def running_investments(
    session: AsyncSession, product_code: str, started_after: date | None = None
) -> list[RunningInvestment]:
    """A product's active investments, oldest start first, then by ID. With `started_after`,
    only those that started after that date."""
    rows = await session.execute(
        _RUNNING, {"product_code": product_code, "started_after": started_after}
    )
    return [_running(row) for row in rows.all()]


async def maturing(
    session: AsyncSession, date_from: date, date_to: date
) -> list[RunningInvestment]:
    """Active investments whose maturity date falls from `date_from` to `date_to`, both included,
    in maturity date order, then by ID."""
    rows = await session.execute(_MATURING, {"date_from": date_from, "date_to": date_to})
    return [_running(row) for row in rows.all()]


def _running(row: Row[Any]) -> RunningInvestment:
    return RunningInvestment(
        investment_id=row.id,
        client_id=row.client_id,
        product_code=row.product_code,
        principal=Money(row.principal_minor, Currency(row.currency)),
        start_date=row.start_date,
        maturity_date=row.maturity_date,
    )
