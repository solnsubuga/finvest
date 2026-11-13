"""The bills the dealing desk holds, and what reconciliation expects to see. Queries only.

Every function works inside the caller's transaction. Staff transactions in the API and the
worker can both read these. Lots and sale lines are written only by `orders.confirm_result`.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealing.domain.holdings import Lot
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class HeldLot:
    id: str
    product_code: str
    settled_on: date
    custodian_reference: str
    # What is still held of it: face value and the cost that goes with it.
    lot: Lot


@dataclass(frozen=True, slots=True)
class Holding:
    """One instrument at the custodian: a product and a maturity date. Amounts in minor units."""

    product_code: str
    currency: Currency
    matures_on: date
    face_minor: int
    cost_minor: int
    custodian_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BankMovement:
    """Cash the trust bank account should show for a settled order. In is positive."""

    ledger_entry_id: str
    currency: Currency
    amount_minor: int
    value_date: date
    bank_reference: str


# What is left of each lot once sales settled on or before :on are taken away.
_LEFT = """
SELECT lot.id, lot.product_code, lot.currency, lot.settled_on, lot.matures_on,
    lot.custodian_reference,
    (lot.face_minor - coalesce(sum(sale.face_minor), 0))::bigint AS face_left,
    (lot.cost_minor - coalesce(sum(sale.cost_minor), 0))::bigint AS cost_left
FROM dealing_lots AS lot
LEFT JOIN (
    SELECT part.lot_id, part.face_minor, part.cost_minor
    FROM dealing_lot_sales AS part
    JOIN dealing_orders AS sale ON sale.id = part.order_id
    WHERE sale.result_settled_on <= :on
) AS sale ON sale.lot_id = lot.id
WHERE lot.settled_on <= :on AND lot.matures_on > :on {where}
GROUP BY lot.id
HAVING lot.face_minor - coalesce(sum(sale.face_minor), 0) > 0
ORDER BY lot.product_code, lot.matures_on, lot.settled_on, lot.id
"""
_ALL_LEFT = text(_LEFT.format(where=""))
_INSTRUMENT_LEFT = text(
    _LEFT.format(where="AND lot.product_code = :product_code AND lot.matures_on = :matures_on")
)
_PRODUCT_LEFT = text(_LEFT.format(where="AND lot.product_code = :product_code"))
_MOVEMENTS = text(
    "SELECT settlement_entry_id, kind, currency, result_amount_minor, result_settled_on, "
    "custodian_reference FROM dealing_orders "
    "WHERE status = 'settled' AND result_settled_on BETWEEN :date_from AND :date_to "
    "ORDER BY result_settled_on, settled_at, id"
)


async def lots_of(
    session: AsyncSession, product_code: str, matures_on: date, *, on: date
) -> list[HeldLot]:
    """The lots of one instrument still held on `on`, oldest purchase first."""
    rows = await session.execute(
        _INSTRUMENT_LEFT, {"product_code": product_code, "matures_on": matures_on, "on": on}
    )
    return [_held(row) for row in rows.all()]


async def face_held(
    session: AsyncSession,
    product_code: str,
    currency: Currency,
    *,
    on: date,
    matures_on: date | None = None,
) -> Money:
    """Face value held in a product on `on`, or in one maturity of it."""
    if matures_on is None:
        rows = (
            await session.execute(_PRODUCT_LEFT, {"product_code": product_code, "on": on})
        ).all()
        lots = [_held(row) for row in rows]
    else:
        lots = await lots_of(session, product_code, matures_on, on=on)
    return sum((held.lot.face for held in lots), Money.zero(currency))


async def holdings_on(session: AsyncSession, on: date) -> list[Holding]:
    """Every instrument held at the end of `on`, for comparing with the custodian's statement.

    A lot counts from its settlement date until the day before it matures. Sales count from
    their settlement date.
    """
    grouped: dict[tuple[str, date], list[HeldLot]] = {}
    for row in (await session.execute(_ALL_LEFT, {"on": on})).all():
        held = _held(row)
        grouped.setdefault((held.product_code, held.lot.matures_on), []).append(held)
    holdings = []
    for (product_code, matures_on), lots in grouped.items():
        holdings.append(
            Holding(
                product_code=product_code,
                currency=lots[0].lot.face.currency,
                matures_on=matures_on,
                face_minor=sum(held.lot.face.minor for held in lots),
                cost_minor=sum(held.lot.cost.minor for held in lots),
                custodian_references=tuple(sorted({held.custodian_reference for held in lots})),
            )
        )
    return holdings


async def expected_bank_movements(
    session: AsyncSession, *, date_from: date, date_to: date
) -> list[BankMovement]:
    """What settled purchases and sales did to the trust bank account between two value dates."""
    rows = await session.execute(_MOVEMENTS, {"date_from": date_from, "date_to": date_to})
    return [
        BankMovement(
            ledger_entry_id=row.settlement_entry_id,
            currency=Currency(row.currency),
            amount_minor=(
                row.result_amount_minor
                if row.kind == "secondary_sale"
                else -row.result_amount_minor
            ),
            value_date=row.result_settled_on,
            bank_reference=row.custodian_reference,
        )
        for row in rows.all()
    ]


def _held(row: Row[Any]) -> HeldLot:
    currency = Currency(row.currency)
    return HeldLot(
        id=row.id,
        product_code=row.product_code,
        settled_on=row.settled_on,
        custodian_reference=row.custodian_reference,
        lot=Lot(
            face=Money(row.face_left, currency),
            cost=Money(row.cost_left, currency),
            matures_on=row.matures_on,
        ),
    )
