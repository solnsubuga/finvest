"""Settling dealing orders against real PostgreSQL: the ledger entry, the bills held, and what
the database refuses whatever the application does.

Lots and ledger rows are never removed, so these tests share the bills they buy. Each test
uses its own maturity date in the 182 day bill, which keeps its instrument to itself, and none
touches the 91 day bill that the order tests expect to be empty.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.dealing import holdings
from app.modules.dealing import orders as dealing
from app.modules.dealing.domain.orders import Fill, OrderKind, OrderStatus
from app.modules.dealing.domain.settlement import BANK_TRUST, BILLS, SALE_GAINS, SALE_LOSSES
from app.modules.dealing.domain.settlement import accounts as settlement_accounts
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TEST_SOURCE
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

M = 1_000_000
TODAY = date(2026, 11, 19)
NOW = FakeClock(datetime(2026, 11, 19, 9, tzinfo=UTC))
CODE = "TREA-182"
_maturities = iter(range(1, 120))


def maturity() -> date:
    """A maturity date no other test uses, inside the 182 day term."""
    return TODAY + timedelta(days=60 + next(_maturities))


def fill(face: int, amount: int, matures_on: date, *, settled_on: date = TODAY) -> Fill:
    return Fill(ugx(face), ugx(amount), settled_on, matures_on, f"CSD-{push_id()[-8:]}")


async def fund_bank(database: Database, amount: int) -> None:
    """Put cash in the trust bank account, against a throwaway liability."""
    staff = new_staff()
    async with database.actor_transaction(staff) as session:
        bank = await ledger.open_account(
            session, settlement_accounts(Currency.UGX)[BANK_TRUST], NOW
        )
        owed = await ledger.open_account(
            session,
            AccountSpec(
                "test.owed", AccountKind.LIABILITY, Currency.UGX, "Test", reference=push_id()
            ),
            NOW,
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                "test.funding",
                TODAY,
                (debit(bank.id, ugx(amount)), credit(owed.id, ugx(amount))),
                key(),
                source_type=TEST_SOURCE,
                source_id=push_id(),
            ),
            NOW,
        )


async def balance(database: Database, code: str) -> int:
    async with database.actor_transaction(new_staff()) as session:
        account_id = await ledger.system_account_id(session, code, Currency.UGX)
        return (await ledger.get_balance(session, account_id)).minor


async def recorded(
    database: Database,
    result: Fill,
    *,
    kind: OrderKind = OrderKind.AUCTION_BID,
    face: int | None = None,
    recorder: Actor | None = None,
) -> dealing.OrderView:
    preparer, approver = new_staff(), new_staff()
    recorder = recorder or preparer
    async with database.actor_transaction(preparer) as session:
        order = await dealing.prepare_order(
            session,
            preparer,
            kind=kind,
            product_code=CODE,
            face=ugx(face or result.face.minor),
            clock=NOW,
        )
    async with database.actor_transaction(approver) as session:
        await dealing.approve_order(session, approver, order.id, NOW)
    async with database.actor_transaction(recorder) as session:
        return await dealing.record_result(session, recorder, order.id, result, NOW)


async def confirm(
    database: Database, order_id: str, staff: Actor | None = None
) -> dealing.OrderView:
    staff = staff or new_staff()
    async with database.actor_transaction(staff) as session:
        return await dealing.confirm_result(session, staff, order_id, NOW)


async def bought(database: Database, face: int, cost: int, matures_on: date) -> dealing.OrderView:
    await fund_bank(database, cost)
    order = await recorded(database, fill(face, cost, matures_on))
    return await confirm(database, order.id)


async def entry_lines(database: Database, order: dealing.OrderView) -> dict[str, int]:
    """The settlement entry's lines by account code, once it is known to point at `order`."""
    assert order.settlement_entry_id is not None and order.result is not None
    entry_id = order.settlement_entry_id
    async with database.actor_transaction(new_staff()) as session:
        entry = await ledger.get_entry(session, entry_id)
        codes = {}
        for line in entry.lines:
            code = (
                await session.execute(
                    text("SELECT code FROM ledger_accounts WHERE id = :id"), {"id": line.account_id}
                )
            ).scalar_one()
            codes[code] = line.amount.minor
        assert (entry.source_type, entry.source_id, entry.effective_date) == (
            "dealing.order",
            order.id,
            order.result.settled_on,
        )
        return codes


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


# Purchases --------------------------------------------------------------------------------------


async def test_confirming_a_purchase_posts_it_and_records_the_bills(
    app_database: Database,
) -> None:
    matures_on = maturity()
    await fund_bank(app_database, 97 * M)
    bank_before = await balance(app_database, BANK_TRUST)
    order = await recorded(app_database, fill(100 * M, 97 * M, matures_on))
    grace = new_staff()

    settled = await confirm(app_database, order.id, grace)

    assert (settled.status, settled.settled_by) == (OrderStatus.SETTLED, grace.id)
    assert await entry_lines(app_database, settled) == {
        BILLS: 97 * M,
        BANK_TRUST: -97 * M,
    }
    assert await balance(app_database, BANK_TRUST) == bank_before - 97 * M
    async with app_database.actor_transaction(grace) as session:
        lots = await holdings.lots_of(session, CODE, matures_on, on=TODAY)
        events = await events_for_resource(session, dealing.ORDER_RESOURCE, order.id)
        held = await holdings.holdings_on(session, TODAY)
        before = await holdings.holdings_on(session, TODAY - timedelta(days=1))
        movements = await holdings.expected_bank_movements(session, date_from=TODAY, date_to=TODAY)
    assert [(held.lot.face, held.lot.cost) for held in lots] == [(ugx(100 * M), ugx(97 * M))]
    assert events[0].action == "dealing.order.settled"
    assert events[0].actor_id == grace.id
    instrument = next(h for h in held if (h.product_code, h.matures_on) == (CODE, matures_on))
    assert (instrument.face_minor, instrument.cost_minor) == (100 * M, 97 * M)
    assert settled.result is not None
    assert instrument.custodian_references == (settled.result.custodian_reference,)
    assert all(h.matures_on != matures_on for h in before)
    movement = next(m for m in movements if m.ledger_entry_id == settled.settlement_entry_id)
    assert (movement.amount_minor, movement.value_date) == (-97 * M, TODAY)


async def test_whoever_recorded_a_result_cannot_confirm_it(app_database: Database) -> None:
    daniel = new_staff()
    order = await recorded(app_database, fill(100 * M, 97 * M, maturity()), recorder=daniel)
    with pytest.raises(ApplicationError) as error:
        await confirm(app_database, order.id, daniel)
    assert error.value.code == "forbidden"


async def test_an_order_is_settled_once(app_database: Database) -> None:
    order = await bought(app_database, 100 * M, 97 * M, maturity())
    with pytest.raises(ApplicationError) as error:
        await confirm(app_database, order.id)
    assert error.value.code == "conflict"


async def test_a_purchase_the_bank_account_cannot_pay_for_is_refused(
    app_database: Database,
) -> None:
    held = await balance(app_database, BANK_TRUST)
    face = (held // (100 * M) + 2) * 100 * M
    order = await recorded(app_database, fill(face, held + 1, maturity()))
    with pytest.raises(ApplicationError) as error:
        await confirm(app_database, order.id)
    assert refused(error) == ("validation_failed", [("amount", "not_enough_cash")])
    async with app_database.actor_transaction(new_staff()) as session:
        still = await dealing.get_order(session, order.id)
    assert still.status is OrderStatus.RESULT_RECORDED
    assert await balance(app_database, BANK_TRUST) == held


# Sales ------------------------------------------------------------------------------------------


async def test_a_sale_takes_the_oldest_bills_first_and_books_the_gain(
    app_database: Database,
) -> None:
    matures_on = maturity()
    first = await bought(app_database, 100 * M, 96 * M, matures_on)
    second = await bought(app_database, 100 * M, 98 * M, matures_on)
    gains_before = await balance(app_database, SALE_GAINS)

    # 150m face: all of the first lot (96m cost) and half of the second (49m cost).
    order = await recorded(
        app_database,
        fill(150 * M, 147 * M, matures_on),
        kind=OrderKind.SECONDARY_SALE,
    )
    settled = await confirm(app_database, order.id)

    assert await entry_lines(app_database, settled) == {
        BANK_TRUST: 147 * M,
        BILLS: -145 * M,
        SALE_GAINS: -2 * M,
    }
    assert await balance(app_database, SALE_GAINS) == gains_before + 2 * M
    async with app_database.actor_transaction(new_staff()) as session:
        lots = await holdings.lots_of(session, CODE, matures_on, on=TODAY)
        parts = (
            await session.execute(
                text(
                    "SELECT lot.order_id, part.face_minor, part.cost_minor "
                    "FROM dealing_lot_sales AS part "
                    "JOIN dealing_lots AS lot ON lot.id = part.lot_id "
                    "WHERE part.order_id = :id ORDER BY lot.settled_on, lot.id"
                ),
                {"id": order.id},
            )
        ).all()
        movements = await holdings.expected_bank_movements(session, date_from=TODAY, date_to=TODAY)
    assert [tuple(part) for part in parts] == [
        (first.id, 100 * M, 96 * M),
        (second.id, 50 * M, 49 * M),
    ]
    assert [(held.lot.face, held.lot.cost) for held in lots] == [(ugx(50 * M), ugx(49 * M))]
    movement = next(m for m in movements if m.ledger_entry_id == settled.settlement_entry_id)
    assert movement.amount_minor == 147 * M


async def test_a_sale_below_cost_books_a_loss(app_database: Database) -> None:
    matures_on = maturity()
    await bought(app_database, 100 * M, 97 * M, matures_on)
    order = await recorded(
        app_database, fill(100 * M, 96_500_000, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    settled = await confirm(app_database, order.id)
    assert await entry_lines(app_database, settled) == {
        BANK_TRUST: 96_500_000,
        BILLS: -97 * M,
        SALE_LOSSES: 500_000,
    }
    async with app_database.actor_transaction(new_staff()) as session:
        assert await holdings.lots_of(session, CODE, matures_on, on=TODAY) == []


async def test_a_sale_of_more_than_one_maturity_holds_is_refused(app_database: Database) -> None:
    matures_on = maturity()
    await bought(app_database, 100 * M, 97 * M, matures_on)
    with pytest.raises(ApplicationError) as error:
        await recorded(
            app_database,
            fill(100 * M, 98 * M, maturity()),
            kind=OrderKind.SECONDARY_SALE,
        )
    assert refused(error) == ("validation_failed", [("face", "more_than_held")])


async def test_two_sales_of_the_same_bills_at_once_sell_them_once(
    app_database: Database,
) -> None:
    matures_on = maturity()
    await bought(app_database, 100 * M, 97 * M, matures_on)
    sales = [
        await recorded(
            app_database, fill(100 * M, 98 * M, matures_on), kind=OrderKind.SECONDARY_SALE
        )
        for _ in range(2)
    ]
    barrier = AsyncBarrier(2)

    async def settle(order_id: str) -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await dealing.confirm_result(session, staff, order_id, NOW)
        except ApplicationError as error:
            return error.fields[0].code if error.fields else error.code
        return "settled"

    outcomes = await asyncio.gather(*(settle(sale.id) for sale in sales))
    assert sorted(outcomes) == ["more_than_held", "settled"]
    async with app_database.actor_transaction(new_staff()) as session:
        assert await holdings.lots_of(session, CODE, matures_on, on=TODAY) == []


# What the database refuses ----------------------------------------------------------------------


async def test_the_database_refuses_a_lot_that_differs_from_its_purchase(
    app_database: Database,
) -> None:
    order = await recorded(app_database, fill(100 * M, 97 * M, maturity()))
    grace = new_staff()
    with pytest.raises(DBAPIError, match="exactly what its purchase recorded"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_lots (id, order_id, product_code, currency, face_minor, "
                    "cost_minor, settled_on, matures_on, custodian_reference) "
                    "SELECT :id, id, product_code, currency, result_face_minor, "
                    "result_amount_minor - 1, result_settled_on, result_matures_on, "
                    "custodian_reference FROM dealing_orders WHERE id = :order_id"
                ),
                {"id": push_id(), "order_id": order.id},
            )


async def test_the_database_refuses_a_sale_above_what_a_lot_still_holds(
    app_database: Database,
) -> None:
    matures_on = maturity()
    purchase = await bought(app_database, 100 * M, 97 * M, matures_on)
    # Recorded while the lot still held 100m, before an earlier sale took 60m of it.
    late = await recorded(
        app_database, fill(100 * M, 98 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    early = await recorded(
        app_database, fill(60 * M, 59 * M, matures_on), kind=OrderKind.SECONDARY_SALE
    )
    await confirm(app_database, early.id)
    async with app_database.actor_transaction(new_staff()) as session:
        lot_id = (
            await session.execute(
                text("SELECT id FROM dealing_lots WHERE order_id = :id"), {"id": purchase.id}
            )
        ).scalar_one()
    with pytest.raises(DBAPIError, match="more than a lot still holds"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_lot_sales (lot_id, order_id, face_minor, cost_minor) "
                    "VALUES (:lot_id, :order_id, 100000000, 1)"
                ),
                {"lot_id": lot_id, "order_id": late.id},
            )


async def test_the_database_refuses_bills_moved_by_whoever_recorded_the_result(
    app_database: Database,
) -> None:
    daniel = new_staff()
    order = await recorded(app_database, fill(100 * M, 97 * M, maturity()), recorder=daniel)
    with pytest.raises(DBAPIError, match="while a second person settles the order"):
        async with app_database.actor_transaction(daniel) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_lots (id, order_id, product_code, currency, face_minor, "
                    "cost_minor, settled_on, matures_on, custodian_reference) "
                    "SELECT :id, id, product_code, currency, result_face_minor, "
                    "result_amount_minor, result_settled_on, result_matures_on, "
                    "custodian_reference FROM dealing_orders WHERE id = :order_id"
                ),
                {"id": push_id(), "order_id": order.id},
            )


async def test_bills_held_are_never_changed_or_removed(app_database: Database) -> None:
    order = await bought(app_database, 100 * M, 97 * M, maturity())
    for statement in (
        "UPDATE dealing_lots SET cost_minor = 1 WHERE order_id = :id",
        "DELETE FROM dealing_lots WHERE order_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(new_staff()) as session:
                await session.execute(text(statement), {"id": order.id})


async def test_the_worker_reads_holdings_and_bank_movements(
    app_database: Database, worker_database: Database
) -> None:
    matures_on = maturity()
    order = await bought(app_database, 100 * M, 97 * M, matures_on)
    async with worker_database.actor_transaction(SYSTEM) as session:
        held = await holdings.holdings_on(session, TODAY)
        movements = await holdings.expected_bank_movements(session, date_from=TODAY, date_to=TODAY)
    assert any(h.matures_on == matures_on and h.face_minor == 100 * M for h in held)
    assert any(m.ledger_entry_id == order.settlement_entry_id for m in movements)
