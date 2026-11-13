"""Funding early withdrawals against real PostgreSQL: from the buffer, from the queue, from a
sale, and what the database refuses.

The ledger and the fundings are never removed, so the buffer is whatever earlier tests left.
The buffer tests use dollars, which nothing else in the dealing tests touches, and move the
dollar buffer to the level each needs before they start. The sale tests use shillings above
the buffer's limit, which always wait for a sale whatever the buffer holds, and fund every
withdrawal they leave waiting before they end.
"""

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.dealing import buffer, withdrawals
from app.modules.dealing.domain.orders import OrderKind
from app.modules.dealing.domain.withdrawals import FundingStatus, Source
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.integration.test_dealing_settlement import M, bought, confirm, fill, maturity, recorded
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import client, key
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

NOW = FakeClock(datetime(2026, 11, 19, 9, tzinfo=UTC))
# Dollar rules in cents: target $20,000, floor $10,000, up to $500 from the buffer.
FLOOR, UP_TO = 1_000_000, 50_000


def usd(cents: int) -> Money:
    return Money(cents, Currency.USD)


async def usd_rules(database: Database) -> None:
    """Dollar buffer rules in force from 2 November 2026, approved once per test run."""
    async with database.actor_transaction(new_staff()) as session:
        if await buffer.versions(session, Currency.USD):
            return
    daniel, grace = new_staff(), new_staff()
    proposed_on = FakeClock(datetime(2026, 11, 1, 9, tzinfo=UTC))
    async with database.actor_transaction(daniel) as session:
        proposal = await buffer.propose_rules(
            session,
            daniel,
            effective_from=date(2026, 11, 2),
            target=usd(2 * FLOOR),
            floor=usd(FLOOR),
            from_buffer_up_to=usd(UP_TO),
            pay_within_working_days=2,
            reason="Dollar bond withdrawals",
            clock=proposed_on,
        )
    async with database.actor_transaction(grace) as session:
        await buffer.approve_rules(session, grace, proposal.id, proposed_on)


async def post(
    database: Database, debit_spec: AccountSpec, credit_spec: AccountSpec, cents: int
) -> None:
    async with database.actor_transaction(new_staff()) as session:
        debited = await ledger.open_account(session, debit_spec, NOW)
        credited = await ledger.open_account(session, credit_spec, NOW)
        await ledger.post_entry(
            session,
            EntryDraft(
                "test.buffer",
                NOW.now().date(),
                (debit(debited.id, usd(cents)), credit(credited.id, usd(cents))),
                key(),
            ),
            NOW,
        )


def spec(code: str, kind: AccountKind, *, owner: Actor | None = None) -> AccountSpec:
    return AccountSpec(
        code,
        kind,
        Currency.USD,
        "Test",
        owner_client_id=owner.id if owner else None,
        reference=push_id(),
    )


async def dollar_buffer(database: Database) -> withdrawals.BufferView:
    async with database.actor_transaction(new_staff()) as session:
        return await withdrawals.buffer_now(session, Currency.USD, NOW)


async def set_buffer(database: Database, cents: int) -> None:
    """Move the dollar buffer to exactly `cents`, with cash in or client money owed."""
    await usd_rules(database)
    state = await dollar_buffer(database)
    change = cents - state.buffer.minor + state.short.minor
    if change > 0:
        await post(
            database,
            AccountSpec("bank.trust", AccountKind.ASSET, Currency.USD, "Trust bank account"),
            spec("test.capital", AccountKind.LIABILITY),
            change,
        )
    elif change < 0:
        await post(
            database,
            spec("test.receivable", AccountKind.ASSET),
            spec("client.payout_pending", AccountKind.LIABILITY, owner=client()),
            -change,
        )
    assert (await dollar_buffer(database)).buffer == usd(cents)


async def fund(
    database: Database, amount: Money, investment_id: str | None = None
) -> withdrawals.FundingView:
    async with database.actor_transaction(SYSTEM) as session:
        return await withdrawals.fund_early_withdrawal(
            session, investment_id or push_id(), amount, NOW
        )


async def release(worker: Database) -> int:
    async with worker.actor_transaction(SYSTEM) as session:
        return await withdrawals.release_queue(session, NOW)


async def announced(worker: Database, funding_id: str) -> list[dict[str, object]]:
    async with worker.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT payload FROM outbox_events "
                "WHERE topic = 'dealing.withdrawal_funded' AND idempotency_key = :id"
            ),
            {"id": funding_id},
        )
        return [row.payload for row in rows]


async def test_the_buffer_counts_cash_less_what_clients_are_owed(app_database: Database) -> None:
    await set_buffer(app_database, 3 * FLOOR)
    await post(
        app_database,
        spec("test.receivable", AccountKind.ASSET),
        spec("client.awaiting_start", AccountKind.LIABILITY, owner=client()),
        FLOOR,
    )
    state = await dollar_buffer(app_database)
    assert state.buffer == usd(2 * FLOOR)
    assert state.cash - state.owed - state.to_place == state.buffer
    assert state.level.value == "on_target"


async def test_a_small_withdrawal_is_paid_from_a_buffer_on_its_floor(
    app_database: Database, worker_database: Database
) -> None:
    # The withdrawal is already owed, so the buffer before it was exactly the floor.
    await set_buffer(app_database, FLOOR - UP_TO)
    funding = await fund(worker_database, usd(UP_TO))
    assert (funding.source, funding.status) == (Source.BUFFER, FundingStatus.FUNDED)
    assert await announced(worker_database, funding.id) == [
        {
            "funding_id": funding.id,
            "investment_id": funding.investment_id,
            "source": "buffer",
            "amount_minor": UP_TO,
            "currency": "USD",
        }
    ]


async def test_below_the_floor_withdrawals_queue_in_the_order_asked(
    app_database: Database, worker_database: Database
) -> None:
    await set_buffer(app_database, FLOOR - UP_TO - 1)
    first = await fund(worker_database, usd(UP_TO))
    assert (first.source, first.status) == (Source.BUFFER, FundingStatus.WAITING)
    assert await release(worker_database) == 0
    # Enough for a later, smaller one alone, but it joins the queue behind the first.
    await set_buffer(app_database, FLOOR - 100)
    second = await fund(worker_database, usd(100))
    assert second.status is FundingStatus.WAITING
    assert await announced(worker_database, first.id) == []

    await set_buffer(app_database, FLOOR)
    assert await release(worker_database) == 2
    async with worker_database.actor_transaction(SYSTEM) as session:
        statuses = [
            (await withdrawals.funding_for(session, f.investment_id)) for f in (first, second)
        ]
        left = await withdrawals.waiting(session, Currency.USD, Source.BUFFER)
    assert [f.status for f in statuses if f] == [FundingStatus.FUNDED] * 2
    assert left == []
    assert len(await announced(worker_database, second.id)) == 1


async def test_asking_twice_or_twice_at_once_records_one_funding(
    app_database: Database, worker_database: Database
) -> None:
    await set_buffer(app_database, 2 * FLOOR)
    investment_id = push_id()
    barrier = AsyncBarrier(2)

    async def once() -> withdrawals.FundingView:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await barrier.wait()
            return await withdrawals.fund_early_withdrawal(session, investment_id, usd(UP_TO), NOW)

    first, second = await asyncio.gather(once(), once())
    again = await fund(worker_database, usd(UP_TO), investment_id)
    assert first.id == second.id == again.id
    with pytest.raises(ApplicationError) as different:
        await fund(worker_database, usd(UP_TO - 1), investment_id)
    assert different.value.code == "conflict"
    assert len(await announced(worker_database, first.id)) == 1


async def test_a_large_withdrawal_waits_for_a_sale_and_sales_fund_in_order(
    app_database: Database, worker_database: Database
) -> None:
    waiting = [
        await fund(worker_database, Money(amount * M, Currency.UGX)) for amount in (20, 15, 10)
    ]
    assert {(f.source, f.status) for f in waiting} == {(Source.SALE, FundingStatus.WAITING)}

    matures_on = maturity()
    await bought(app_database, 100 * M, 97 * M, matures_on)
    sale = await recorded(
        app_database, fill(40 * M, 39 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    unsettled = new_staff()
    with pytest.raises(ApplicationError) as too_soon:
        async with app_database.actor_transaction(unsettled) as session:
            await withdrawals.fund_from_sale(session, unsettled, sale.id, NOW)
    assert too_soon.value.code == "conflict"
    await confirm(app_database, sale.id)

    grace = new_staff()
    async with app_database.actor_transaction(grace) as session:
        funded = await withdrawals.fund_from_sale(session, grace, sale.id, NOW)
        again = await withdrawals.fund_from_sale(session, grace, sale.id, NOW)
    # 39m pays the 20m and the 15m. The 10m does not fit in the 4m left, so it waits.
    assert [f.investment_id for f in funded] == [f.investment_id for f in waiting[:2]]
    assert {(f.sale_order_id, f.funded_by) for f in funded} == {(sale.id, grace.id)}
    assert again == []

    second_sale = await recorded(
        app_database, fill(11 * M, 10 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await confirm(app_database, second_sale.id)
    async with app_database.actor_transaction(grace) as session:
        last = await withdrawals.fund_from_sale(session, grace, second_sale.id, NOW)
        left = await withdrawals.waiting(session, Currency.UGX, Source.SALE)
    assert [f.investment_id for f in last] == [waiting[2].investment_id]
    assert left == []


async def test_only_the_worker_records_a_funding_and_funds_from_the_buffer(
    app_database: Database, worker_database: Database
) -> None:
    await set_buffer(app_database, 0)
    queued = await fund(worker_database, usd(UP_TO))
    staff = new_staff()
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(staff) as session:
            await withdrawals.fund_early_withdrawal(session, push_id(), usd(UP_TO), NOW)
    with pytest.raises(DBAPIError, match="only the worker funds"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE dealing_withdrawal_fundings SET status = 'funded', funded_at = now() "
                    "WHERE id = :id"
                ),
                {"id": queued.id},
            )
    with pytest.raises(DBAPIError, match="starts as waiting"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_withdrawal_fundings (id, investment_id, currency, "
                    "amount_minor, source, status, asked_at, funded_at) VALUES (:id, :id, 'UGX', "
                    "1, 'sale', 'funded', now(), now())"
                ),
                {"id": push_id()},
            )
    async with app_database.actor_transaction(Actor("client", client().id)) as session:
        with pytest.raises(ApplicationError):
            await withdrawals.buffer_now(session, Currency.USD, NOW)
    await set_buffer(app_database, FLOOR)
    assert await release(worker_database) >= 1


async def test_the_database_refuses_more_than_a_sale_raised_and_any_later_change(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    await bought(app_database, 50 * M, 48 * M, matures_on)
    sale = await recorded(
        app_database, fill(10 * M, 9 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await confirm(app_database, sale.id)
    large = await fund(worker_database, Money(10 * M, Currency.UGX))
    staff = new_staff()

    def funding_from(order_id: str) -> tuple[str, dict[str, str | None]]:
        return (
            "UPDATE dealing_withdrawal_fundings SET status = 'funded', funded_at = now(), "
            "sale_order_id = :order, funded_by = :by WHERE id = :id",
            {"order": order_id, "by": staff.id, "id": large.id},
        )

    with pytest.raises(DBAPIError, match="cannot exceed what it raised"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text(funding_from(sale.id)[0]), funding_from(sale.id)[1])
    purchase = await bought(app_database, 1 * M, 1 * M, matures_on)
    with pytest.raises(DBAPIError, match="settled sale in its currency"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text(funding_from(purchase.id)[0]), funding_from(purchase.id)[1])

    bigger = await recorded(
        app_database, fill(11 * M, 10 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await confirm(app_database, bigger.id)
    async with app_database.actor_transaction(staff) as session:
        funded = await withdrawals.fund_from_sale(session, staff, bigger.id, NOW)
    assert [f.id for f in funded] == [large.id]
    for statement in (
        "UPDATE dealing_withdrawal_fundings SET funded_at = now() WHERE id = :id",
        "DELETE FROM dealing_withdrawal_fundings WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": large.id})
