"""The bills the dealing desk holds, and what reconciliation expects to see. Queries only.

Every function works inside the caller's transaction. Staff transactions in the API and the
worker can both read these. Lots and sale lines are written only by `orders.confirm_result`,
and maturities only by `maturities.record_maturities` in the worker.
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
    """Cash the trust bank account should show for a settled order, a maturity or a movement of
    Finvest's own money. In is positive."""

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
-- A lot counts once its purchase is settled. It is written in the same transaction, so this
-- only matters for a lot written by hand.
JOIN dealing_orders AS bought ON bought.id = lot.order_id AND bought.status = 'settled'
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
    "SELECT entry_id, currency, amount_minor, value_date, reference FROM ("
    "SELECT settlement_entry_id AS entry_id, currency, result_settled_on AS value_date, "
    "CASE WHEN kind = 'secondary_sale' THEN result_amount_minor ELSE -result_amount_minor END "
    "AS amount_minor, custodian_reference AS reference, settled_at AS at FROM dealing_orders "
    "WHERE status = 'settled' AND result_settled_on BETWEEN :date_from AND :date_to "
    "UNION ALL "
    "SELECT matured.ledger_entry_id, lot.currency, matured.matured_on, matured.face_minor, "
    "lot.custodian_reference, matured.recorded_at FROM dealing_lot_maturities AS matured "
    "JOIN dealing_lots AS lot ON lot.id = matured.lot_id "
    "WHERE matured.matured_on BETWEEN :date_from AND :date_to "
    "UNION ALL "
    "SELECT ledger_entry_id, currency, value_date, "
    "CASE WHEN direction = 'in' THEN amount_minor ELSE -amount_minor END, "
    "coalesce(bank_reference, id), decided_at FROM dealing_buffer_capital "
    "WHERE status = 'approved' AND value_date BETWEEN :date_from AND :date_to"
    ") AS movement ORDER BY value_date, at, entry_id"
)
# Lots that have reached their maturity date, still hold bills, and are not recorded as paid.
_DUE = text(
    "SELECT lot.id FROM dealing_lots AS lot "
    "JOIN dealing_orders AS bought ON bought.id = lot.order_id AND bought.status = 'settled' "
    "WHERE lot.matures_on <= :on AND NOT EXISTS "
    "(SELECT FROM dealing_lot_maturities AS matured WHERE matured.lot_id = lot.id) "
    "AND lot.face_minor > (SELECT coalesce(sum(sale.face_minor), 0) FROM dealing_lot_sales "
    "AS sale WHERE sale.lot_id = lot.id) "
    "ORDER BY lot.matures_on, lot.id LIMIT :limit"
)
# What is left of one lot after every sale, and whether it has matured.
_LEFT_OF_LOT = text(
    "SELECT lot.id, lot.product_code, lot.currency, lot.settled_on, lot.matures_on, "
    "lot.custodian_reference, "
    "(lot.face_minor - coalesce(sum(sale.face_minor), 0))::bigint AS face_left, "
    "(lot.cost_minor - coalesce(sum(sale.cost_minor), 0))::bigint AS cost_left, "
    "EXISTS (SELECT FROM dealing_lot_maturities AS matured WHERE matured.lot_id = lot.id) "
    "AS matured FROM dealing_lots AS lot "
    "LEFT JOIN dealing_lot_sales AS sale ON sale.lot_id = lot.id "
    "WHERE lot.id = :id GROUP BY lot.id"
)
# What each lot of a product still holds after every sale, for allocation: lots of settled
# purchases that have not matured, oldest purchase first. The allocations trigger counts the
# same way.
_ALLOCATABLE = text(
    "SELECT lot.id, lot.product_code, lot.currency, lot.settled_on, lot.matures_on, "
    "lot.custodian_reference, "
    "(lot.face_minor - coalesce(sum(sale.face_minor), 0))::bigint AS face_left, "
    "(lot.cost_minor - coalesce(sum(sale.cost_minor), 0))::bigint AS cost_left "
    "FROM dealing_lots AS lot JOIN dealing_orders AS bought ON bought.id = lot.order_id "
    "LEFT JOIN dealing_lot_sales AS sale ON sale.lot_id = lot.id "
    "WHERE lot.product_code = :product_code AND bought.status = 'settled' "
    "AND NOT EXISTS (SELECT FROM dealing_lot_maturities AS matured WHERE matured.lot_id = lot.id) "
    # A lot past its maturity date has been paid, even if the run has not posted it yet.
    "AND lot.matures_on > :on "
    "GROUP BY lot.id HAVING lot.face_minor - coalesce(sum(sale.face_minor), 0) > 0 "
    "ORDER BY lot.settled_on, lot.id"
)


async def allocatable_lots(session: AsyncSession, product_code: str, *, on: date) -> list[HeldLot]:
    """The lots of a product that can stand behind investments on `on`, oldest purchase first.

    A lot that matures on or before `on` is left out, even before its maturity is posted."""
    rows = await session.execute(_ALLOCATABLE, {"product_code": product_code, "on": on})
    return [_held(row) for row in rows.all()]


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
    """What settled purchases, sales and maturities, and Finvest's own money paid into or taken
    out of the buffer, did to the trust bank account between two value dates."""
    rows = await session.execute(_MOVEMENTS, {"date_from": date_from, "date_to": date_to})
    return [
        BankMovement(
            ledger_entry_id=row.entry_id,
            currency=Currency(row.currency),
            amount_minor=row.amount_minor,
            value_date=row.value_date,
            bank_reference=row.reference,
        )
        for row in rows.all()
    ]


async def due_for_maturity(session: AsyncSession, on: date, *, limit: int) -> list[str]:
    """IDs of lots that mature on or before `on` and are not yet recorded as paid, oldest first."""
    rows = await session.execute(_DUE, {"on": on, "limit": limit})
    return [row.id for row in rows.all()]


async def left_of_lot(session: AsyncSession, lot_id: str) -> tuple[HeldLot | None, bool]:
    """What one lot still holds after every sale, or `None` when nothing is left, and whether
    it has been recorded as matured."""
    row = (await session.execute(_LEFT_OF_LOT, {"id": lot_id})).one()
    return (_held(row) if row.face_left > 0 else None), bool(row.matured)


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
