"""Allocating bills to client investments against real PostgreSQL.

Lots, investments and allocations are never removed, so the 182 day bill already holds what
earlier tests bought and started. Each test first brings the allocations up to date, buys
enough for what is waiting plus its own investment, and checks what must hold whatever else is
in the database: no lot or investment is over allocated, and nobody waits while bills are free.
"""

import asyncio
from collections import defaultdict
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.dealing import allocations, holdings
from app.modules.dealing.domain.orders import OrderKind
from app.modules.investments import service as investments
from app.shared.actors import SYSTEM
from app.shared.push_ids import push_id
from tests.integration.test_dealing_settlement import (
    CODE,
    NOW,
    TODAY,
    M,
    bought,
    confirm,
    fill,
    recorded,
)
from tests.support.clock import AsyncBarrier
from tests.support.investments import PRESENT, started
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

LOT = 100_000
# Later than any bill the maturity tests pay, so their runs never pay these, and inside the
# 182 day term. The sale test keeps the day before to itself.
MATURES_ON = TODAY + timedelta(days=182)
SOLD_MATURES_ON = TODAY + timedelta(days=181)


async def run(worker: Database) -> allocations.Run:
    async with worker.actor_transaction(SYSTEM) as session:
        return await allocations.allocate_all(session, NOW)


async def book(worker: Database) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Principal of each running investment, cost left in each lot, and in force by both."""
    async with worker.actor_transaction(SYSTEM) as session:
        principal = {
            i.investment_id: i.principal.minor
            for i in await investments.running_investments(session, CODE)
        }
        lots = {
            held.id: held.lot.cost.minor
            for held in await holdings.allocatable_lots(session, CODE, on=TODAY)
        }
        rows = (
            await session.execute(
                text(
                    "SELECT a.investment_id, a.lot_id, a.cost_minor FROM dealing_allocations a "
                    "JOIN dealing_lots l ON l.id = a.lot_id "
                    "WHERE l.product_code = :code AND a.released_at IS NULL"
                ),
                {"code": CODE},
            )
        ).all()
    by_investment: defaultdict[str, int] = defaultdict(int)
    by_lot: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        by_investment[row.investment_id] += row.cost_minor
        by_lot[row.lot_id] += row.cost_minor
    return principal, lots, {**by_investment, **{f"lot:{k}": v for k, v in by_lot.items()}}


async def check(worker: Database) -> int:
    """The rules that hold after every run. Returns the principal still waiting."""
    principal, lots, in_force = await book(worker)
    for investment_id, amount in principal.items():
        assert in_force.get(investment_id, 0) <= amount
    for key, amount in in_force.items():
        if key.startswith("lot:"):
            assert amount <= lots.get(key[4:], 0)
        else:
            assert key in principal
    waiting = sum(amount - in_force.get(i, 0) for i, amount in principal.items())
    free = sum(cost - in_force.get(f"lot:{lot}", 0) for lot, cost in lots.items())
    assert waiting == 0 or free == 0
    return waiting


async def buy(app: Database, cost: int) -> str:
    """Buy a lot of the 182 day bill costing exactly `cost`, and return its ID."""
    face = (cost * 103 // 100 // LOT + 1) * LOT
    order = await bought(app, face, cost, MATURES_ON)
    async with app.actor_transaction(new_staff()) as session:
        return str(
            (
                await session.execute(
                    text("SELECT id FROM dealing_lots WHERE order_id = :id"), {"id": order.id}
                )
            ).scalar_one()
        )


async def test_a_new_lot_covers_what_waits_oldest_first_then_the_newest_investment(
    app_database: Database, worker_database: Database
) -> None:
    await run(worker_database)
    waiting = await check(worker_database)
    newest = await started(app_database, worker_database, PRESENT, product_code=CODE)
    lot = await buy(app_database, waiting + newest.amount.minor)

    result = await run(worker_database)
    assert result.allocated >= 1
    assert await check(worker_database) == 0
    async with worker_database.actor_transaction(SYSTEM) as session:
        behind = await allocations.bills_behind(session, newest.id)
    assert sum(b.cost.minor for b in behind) == newest.amount.minor
    assert {b.product_code for b in behind} == {CODE}
    assert lot in {b.lot_id for b in behind}
    # A second run changes nothing.
    assert await run(worker_database) == allocations.Run(0, 0)


async def test_a_lot_due_to_mature_stands_behind_nothing_even_before_it_is_posted(
    app_database: Database, worker_database: Database
) -> None:
    lot = await buy(app_database, 5 * LOT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        day_before = await holdings.allocatable_lots(
            session, CODE, on=MATURES_ON - timedelta(days=1)
        )
        on_the_day = await holdings.allocatable_lots(session, CODE, on=MATURES_ON)
    assert lot in {held.id for held in day_before}
    assert lot not in {held.id for held in on_the_day}


async def test_a_sale_releases_the_newest_allocations_from_the_lot(
    app_database: Database, worker_database: Database
) -> None:
    await run(worker_database)
    waiting = await check(worker_database)
    newest = await started(
        app_database, worker_database, PRESENT, product_code=CODE, amount=ugx(2 * M)
    )
    cost = waiting + newest.amount.minor
    face = (cost * 103 // 100 // LOT + 1) * LOT
    matures_on = SOLD_MATURES_ON
    await bought(app_database, face, cost, matures_on)
    await run(worker_database)
    assert await check(worker_database) == 0

    # Selling a slice of the lot leaves it holding less than is allocated from it.
    sale = await recorded(
        app_database, fill(10 * LOT, 9 * LOT, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await confirm(app_database, sale.id)
    result = await run(worker_database)
    assert result.released >= 1
    await check(worker_database)


async def test_two_runs_at_once_never_allocate_a_lot_twice(
    app_database: Database, worker_database: Database
) -> None:
    await run(worker_database)
    waiting = await check(worker_database)
    newest = await started(app_database, worker_database, PRESENT, product_code=CODE)
    await buy(app_database, waiting + newest.amount.minor)
    barrier = AsyncBarrier(2)

    async def once() -> allocations.Run:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await barrier.wait()
            return await allocations.allocate_all(session, NOW)

    await asyncio.gather(once(), once())
    assert await check(worker_database) == 0


async def test_the_database_refuses_what_a_lot_does_not_hold_and_any_change(
    app_database: Database, worker_database: Database
) -> None:
    await run(worker_database)
    await check(worker_database)
    lot = await buy(app_database, 5 * LOT)
    investment_id = push_id()
    insert = text(
        "INSERT INTO dealing_allocations (id, investment_id, lot_id, cost_minor, allocated_at) "
        "VALUES (:id, :investment_id, :lot, :cost, now())"
    )
    with pytest.raises(DBAPIError, match="more allocated than the cost it still holds"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                insert,
                {"id": push_id(), "investment_id": investment_id, "lot": lot, "cost": 5 * LOT + 1},
            )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                insert, {"id": push_id(), "investment_id": investment_id, "lot": lot, "cost": 1}
            )
    allocation_id = push_id()
    async with worker_database.actor_transaction(SYSTEM) as session:
        await session.execute(
            insert, {"id": allocation_id, "investment_id": investment_id, "lot": lot, "cost": 1}
        )
    for statement in (
        "UPDATE dealing_allocations SET cost_minor = 2 WHERE id = :id",
        "DELETE FROM dealing_allocations WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": allocation_id})
    # An allocation for no running investment is released by the next run.
    assert (await run(worker_database)).released >= 1
    with pytest.raises(DBAPIError, match="only released, once"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE dealing_allocations SET released_at = now(), "
                    "release_reason = 'lot_reduced' WHERE id = :id"
                ),
                {"id": allocation_id},
            )
    await check(worker_database)
