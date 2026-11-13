"""Bills paid at maturity, against real PostgreSQL: the worker's entry, what is left of a lot, and
what the database refuses."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.dealing import holdings, maturities
from app.modules.dealing.domain.orders import OrderKind
from app.modules.dealing.domain.settlement import BANK_TRUST, BILL_INCOME
from app.modules.ledger import closing
from app.shared.actors import SYSTEM
from tests.integration.test_dealing_settlement import (
    NOW,
    M,
    balance,
    bought,
    confirm,
    fill,
    fund_bank,
    maturity,
    recorded,
)
from tests.integration.test_ledger_closing import first_open_day
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


def on(day: date) -> FakeClock:
    """Midday in Kampala on `day`."""
    return FakeClock(datetime.combine(day, time(9), tzinfo=UTC))


async def run(worker: Database, day: date) -> maturities.Run:
    async with worker.actor_transaction(SYSTEM) as session:
        return await maturities.record_maturities(session, on(day), limit=500)


async def lot_id(database: Database, order_id: str) -> str:
    async with database.actor_transaction(new_staff()) as session:
        return str(
            (
                await session.execute(
                    text("SELECT id FROM dealing_lots WHERE order_id = :id"), {"id": order_id}
                )
            ).scalar_one()
        )


async def maturity_of(database: Database, lot: str) -> tuple[int, int, str] | None:
    async with database.actor_transaction(new_staff()) as session:
        row = (
            await session.execute(
                text(
                    "SELECT face_minor, cost_minor, ledger_entry_id FROM dealing_lot_maturities "
                    "WHERE lot_id = :id"
                ),
                {"id": lot},
            )
        ).one_or_none()
    return None if row is None else (row.face_minor, row.cost_minor, row.ledger_entry_id)


async def test_a_lot_is_paid_on_its_maturity_date_and_leaves_the_holdings(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    order = await bought(app_database, 100 * M, 97 * M, matures_on)
    lot = await lot_id(app_database, order.id)
    bank, income = await balance(app_database, BANK_TRUST), await balance(app_database, BILL_INCOME)

    await run(worker_database, matures_on - timedelta(days=1))
    assert await maturity_of(app_database, lot) is None
    await run(worker_database, matures_on)

    recorded_maturity = await maturity_of(app_database, lot)
    assert recorded_maturity is not None
    assert recorded_maturity[:2] == (100 * M, 97 * M)
    assert await balance(app_database, BANK_TRUST) == bank + 100 * M
    assert await balance(app_database, BILL_INCOME) == income + 3 * M
    async with worker_database.actor_transaction(SYSTEM) as session:
        held = await holdings.holdings_on(session, matures_on)
        movements = await holdings.expected_bank_movements(
            session, date_from=matures_on, date_to=matures_on
        )
    assert all(h.matures_on != matures_on for h in held)
    movement = next(m for m in movements if m.ledger_entry_id == recorded_maturity[2])
    assert (movement.amount_minor, movement.value_date) == (100 * M, matures_on)


async def test_a_late_run_dates_the_payment_on_the_maturity_date(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    await run(worker_database, matures_on + timedelta(days=3))
    recorded_maturity = await maturity_of(app_database, lot)
    assert recorded_maturity is not None
    async with app_database.actor_transaction(new_staff()) as session:
        entry = (
            await session.execute(
                text("SELECT effective_date FROM ledger_journal_entries WHERE id = :id"),
                {"id": recorded_maturity[2]},
            )
        ).scalar_one()
    assert entry == matures_on


async def test_only_what_is_left_after_a_sale_matures(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    sale = await recorded(
        app_database, fill(40 * M, 39 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await confirm(app_database, sale.id)
    await run(worker_database, matures_on)
    recorded_maturity = await maturity_of(app_database, lot)
    # 40m of face took 38.8m of cost, so 60m of face and 58.2m of cost were left.
    assert recorded_maturity is not None
    assert recorded_maturity[:2] == (60 * M, 58_200_000)


async def test_a_lot_sold_in_full_never_matures(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    sale = await recorded(
        app_database, fill(100 * M, 98 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await confirm(app_database, sale.id)
    await run(worker_database, matures_on)
    assert await maturity_of(app_database, lot) is None


async def test_running_twice_or_twice_at_once_pays_a_lot_once(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    bank = await balance(app_database, BANK_TRUST)
    barrier = AsyncBarrier(2)

    async def once() -> None:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await barrier.wait()
            await maturities.record_maturities(session, on(matures_on), limit=500)

    await asyncio.gather(once(), once())
    await run(worker_database, matures_on)
    assert await maturity_of(app_database, lot) is not None
    assert await balance(app_database, BANK_TRUST) == bank + 100 * M


async def test_bills_that_have_matured_cannot_be_sold(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    # Recorded while the bills were still held, confirmed after the issuer paid them.
    sale = await recorded(
        app_database, fill(50 * M, 49 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await run(worker_database, matures_on)
    with pytest.raises(DBAPIError, match="bills that have matured"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_lot_sales (lot_id, order_id, face_minor, cost_minor) "
                    "VALUES (:lot_id, :order_id, 50000000, 48500000)"
                ),
                {"lot_id": lot, "order_id": sale.id},
            )


async def test_only_the_worker_records_a_maturity(app_database: Database) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_lot_maturities (lot_id, matured_on, face_minor, "
                    "cost_minor, ledger_entry_id, recorded_at) VALUES (:id, :on, 1, 1, :id, now())"
                ),
                {"id": lot, "on": matures_on},
            )


async def test_the_database_refuses_a_maturity_that_differs_from_the_lot(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    with pytest.raises(DBAPIError, match="what the lot still holds"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_lot_maturities (lot_id, matured_on, face_minor, "
                    "cost_minor, ledger_entry_id, recorded_at) "
                    "VALUES (:id, :on, 100000000, 96000000, :id, now())"
                ),
                {"id": lot, "on": matures_on},
            )
    with pytest.raises(DBAPIError, match="needs its ledger entry"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_lot_maturities (lot_id, matured_on, face_minor, "
                    "cost_minor, ledger_entry_id, recorded_at) "
                    "VALUES (:id, :on, 100000000, 97000000, :id, now())"
                ),
                {"id": lot, "on": matures_on},
            )


async def test_a_maturity_is_never_changed_or_removed(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    lot = await lot_id(app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id)
    await run(worker_database, matures_on)
    for statement in (
        "UPDATE dealing_lot_maturities SET face_minor = 1 WHERE lot_id = :id",
        "DELETE FROM dealing_lot_maturities WHERE lot_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": lot})
    assert await maturity_of(app_database, lot) is not None


async def test_a_lot_in_a_closed_day_is_reported_and_the_others_are_still_paid(
    app_database: Database, worker_database: Database
) -> None:
    # Closing is for the whole ledger and cannot be undone, so, like the ledger's own closing
    # tests, this one closes only days in 2001, before any other test posts.
    day = await first_open_day(app_database)
    await fund_bank(app_database, 97 * M)
    old = await recorded(
        app_database, fill(100 * M, 97 * M, day + timedelta(days=5), settled_on=day)
    )
    await confirm(app_database, old.id)
    stuck_lot = await lot_id(app_database, old.id)
    closer = new_staff()
    async with app_database.actor_transaction(closer) as session:
        await closing.close_through(session, closer, day + timedelta(days=5), NOW)

    matures_on = maturity()
    paid_lot = await lot_id(
        app_database, (await bought(app_database, 100 * M, 97 * M, matures_on)).id
    )
    result = await run(worker_database, matures_on)
    assert stuck_lot in result.stuck
    assert await maturity_of(app_database, stuck_lot) is None
    assert await maturity_of(app_database, paid_lot) is not None
