"""Dealing desk orders against real PostgreSQL: who may take each step, and what the database
refuses whatever the application does."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.dealing import orders as dealing
from app.modules.dealing.domain.orders import Fill, OrderKind, OrderStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

M = 1_000_000
TODAY = date(2026, 11, 19)
# Midday in Kampala.
NOW = FakeClock(datetime(2026, 11, 19, 9, tzinfo=UTC))
FILL = Fill(
    face=ugx(240 * M),
    amount=ugx(232_800_000),
    settled_on=TODAY,
    matures_on=date(2027, 2, 18),
    custodian_reference="CSD-2026-0502",
)


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def prepare(
    database: Database,
    staff: Actor,
    *,
    kind: OrderKind = OrderKind.AUCTION_BID,
    code: str = "TREA-91",
    face: Money | None = None,
) -> dealing.OrderView:
    async with database.actor_transaction(staff) as session:
        return await dealing.prepare_order(
            session, staff, kind=kind, product_code=code, face=face or ugx(240 * M), clock=NOW
        )


async def approved(database: Database, preparer: Actor, approver: Actor) -> dealing.OrderView:
    order = await prepare(database, preparer)
    async with database.actor_transaction(approver) as session:
        return await dealing.approve_order(session, approver, order.id, NOW)


async def recorded(database: Database, preparer: Actor, approver: Actor) -> dealing.OrderView:
    order = await approved(database, preparer, approver)
    async with database.actor_transaction(preparer) as session:
        return await dealing.record_result(session, preparer, order.id, FILL, NOW)


# The life of an order ---------------------------------------------------------------------------


async def test_two_people_take_an_order_from_prepared_to_a_recorded_result(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    assert (order.status, order.prepared_by, order.face) == (
        OrderStatus.PREPARED,
        daniel.id,
        ugx(240 * M),
    )

    async with app_database.actor_transaction(grace) as session:
        after_approval = await dealing.approve_order(session, grace, order.id, NOW)
    async with app_database.actor_transaction(daniel) as session:
        after_result = await dealing.record_result(session, daniel, order.id, FILL, NOW)
        events = await events_for_resource(session, dealing.ORDER_RESOURCE, order.id)

    assert (after_approval.status, after_approval.decided_by) == (OrderStatus.APPROVED, grace.id)
    assert after_result.status is OrderStatus.RESULT_RECORDED
    assert after_result.result == FILL
    assert after_result.result_recorded_by == daniel.id
    assert [event.action for event in reversed(events)] == [
        "dealing.order.prepared",
        "dealing.order.approved",
        "dealing.order.result_recorded",
    ]


async def test_open_orders_are_listed_oldest_first_and_counted_for_the_position(
    app_database: Database,
) -> None:
    daniel = new_staff()
    async with app_database.actor_transaction(daniel) as session:
        before = (await dealing.on_order(session)).get("TREA-182", ugx(0))
    first = await prepare(app_database, daniel, code="TREA-182", face=ugx(160 * M))
    second = await prepare(
        app_database, daniel, kind=OrderKind.SECONDARY_PURCHASE, code="TREA-182", face=ugx(50 * M)
    )
    async with app_database.actor_transaction(daniel) as session:
        open_ids = [view.id for view in await dealing.open_orders(session)]
        counted = (await dealing.on_order(session))["TREA-182"]
        await dealing.withdraw_order(session, daniel, first.id, NOW)
        after = (await dealing.on_order(session))["TREA-182"]
        still_open = [view.id for view in await dealing.open_orders(session)]

    assert open_ids.index(first.id) < open_ids.index(second.id)
    assert counted == before + ugx(210 * M)
    assert after == before + ugx(50 * M)
    assert first.id not in still_open


@pytest.mark.parametrize(
    ("code", "face", "problem"),
    [
        ("TREA-91", ugx(240 * M + 1), "not_a_whole_lot"),
        ("TREA-91", ugx(99_999), "not_a_whole_lot"),
        ("TREA-91", Money(100_000_00, Currency.USD), "currency_mismatch"),
        ("USD-BOND", ugx(100_000), "currency_mismatch"),
    ],
)
async def test_an_order_is_for_whole_lots_in_the_products_currency(
    app_database: Database, code: str, face: Money, problem: str
) -> None:
    with pytest.raises(ApplicationError) as error:
        await prepare(app_database, new_staff(), code=code, face=face)
    assert refused(error) == ("validation_failed", [("face", problem)])


async def test_an_order_for_an_unknown_product_is_not_found(app_database: Database) -> None:
    with pytest.raises(ApplicationError) as error:
        await prepare(app_database, new_staff(), code="TREA-364")
    assert error.value.code == "not_found"


async def test_a_sale_is_refused_while_no_bills_are_recorded_as_held(
    app_database: Database,
) -> None:
    with pytest.raises(ApplicationError) as error:
        await prepare(app_database, new_staff(), kind=OrderKind.SECONDARY_SALE, face=ugx(50 * M))
    assert refused(error) == ("validation_failed", [("face", "more_than_held")])


async def test_a_bid_that_won_nothing_ends_as_unsuccessful(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await approved(app_database, daniel, grace)
    async with app_database.actor_transaction(grace) as session:
        ended = await dealing.record_result(session, grace, order.id, None, NOW)
        with pytest.raises(ApplicationError) as again:
            await dealing.record_result(session, grace, order.id, FILL, NOW)
    assert (ended.status, ended.result, ended.result_recorded_by) == (
        OrderStatus.UNSUCCESSFUL,
        None,
        grace.id,
    )
    assert again.value.code == "conflict"


@pytest.mark.parametrize(
    ("change", "field", "problem"),
    [
        ({"face": ugx(240 * M + 100_000)}, "face", "over_the_order"),
        ({"amount": ugx(240 * M + 1)}, "amount", "above_face"),
        ({"settled_on": date(2026, 11, 20)}, "settled_on", "settles_in_the_future"),
        ({"matures_on": date(2027, 5, 20)}, "matures_on", "longer_than_term"),
    ],
)
async def test_a_result_that_cannot_be_right_is_refused(
    app_database: Database, change: dict[str, object], field: str, problem: str
) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await approved(app_database, daniel, grace)
    fill = Fill(**{**{name: getattr(FILL, name) for name in Fill.__slots__}, **change})
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as error:
            await dealing.record_result(session, daniel, order.id, fill, NOW)
        unchanged = await dealing.get_order(session, order.id)
    assert refused(error) == ("validation_failed", [(field, problem)])
    assert unchanged.status is OrderStatus.APPROVED


async def test_a_rejected_result_goes_back_to_be_recorded_again(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await recorded(app_database, daniel, grace)
    async with app_database.actor_transaction(grace) as session:
        rejected = await dealing.reject_result(
            session, grace, order.id, " Cash paid differs from the note ", NOW
        )
    assert (rejected.status, rejected.result, rejected.result_recorded_by) == (
        OrderStatus.APPROVED,
        None,
        None,
    )
    assert rejected.result_rejection_note == "Cash paid differs from the note"

    corrected = Fill(
        FILL.face, ugx(232_850_000), FILL.settled_on, FILL.matures_on, FILL.custodian_reference
    )
    async with app_database.actor_transaction(grace) as session:
        again = await dealing.record_result(session, grace, order.id, corrected, NOW)
    assert again.result == corrected
    assert again.result_rejection_note is None


# Who may do what --------------------------------------------------------------------------------


async def test_nobody_approves_or_sends_back_their_own_order(app_database: Database) -> None:
    daniel = new_staff()
    order = await prepare(app_database, daniel)
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as approve_error:
            await dealing.approve_order(session, daniel, order.id, NOW)
        with pytest.raises(ApplicationError) as send_back_error:
            await dealing.send_back_order(session, daniel, order.id, "No", NOW)
    assert approve_error.value.code == "forbidden"
    assert send_back_error.value.code == "forbidden"


async def test_sending_back_needs_a_note_and_ends_the_order(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    async with app_database.actor_transaction(grace) as session:
        with pytest.raises(ApplicationError) as error:
            await dealing.send_back_order(session, grace, order.id, "  ", NOW)
        sent_back = await dealing.send_back_order(session, grace, order.id, " Too large ", NOW)
        with pytest.raises(ApplicationError) as late:
            await dealing.approve_order(session, grace, order.id, NOW)
    assert refused(error) == ("validation_failed", [("note", "note_required")])
    assert (sent_back.status, sent_back.decision_note) == (OrderStatus.SENT_BACK, "Too large")
    assert late.value.code == "conflict"


async def test_only_the_preparer_withdraws_an_order(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    async with app_database.actor_transaction(grace) as session:
        with pytest.raises(ApplicationError) as error:
            await dealing.withdraw_order(session, grace, order.id, NOW)
    async with app_database.actor_transaction(daniel) as session:
        withdrawn = await dealing.withdraw_order(session, daniel, order.id, NOW)
    assert error.value.code == "forbidden"
    assert (withdrawn.status, withdrawn.decided_by) == (OrderStatus.WITHDRAWN, daniel.id)


async def test_whoever_recorded_a_result_cannot_reject_it(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await recorded(app_database, daniel, grace)
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as error:
            await dealing.reject_result(session, daniel, order.id, "Typo", NOW)
    assert error.value.code == "forbidden"


@pytest.mark.parametrize("actor", [new_client(), SYSTEM])
async def test_a_client_or_the_system_can_neither_prepare_nor_read_orders(
    app_database: Database, actor: Actor
) -> None:
    order = await prepare(app_database, new_staff())
    async with app_database.actor_transaction(actor) as session:
        with pytest.raises(ApplicationError) as error:
            await dealing.prepare_order(
                session,
                actor,
                kind=OrderKind.AUCTION_BID,
                product_code="TREA-91",
                face=ugx(100_000),
                clock=NOW,
            )
        assert await dealing.open_orders(session) == []
        with pytest.raises(ApplicationError) as hidden:
            await dealing.get_order(session, order.id)
    assert error.value.code == "forbidden"
    assert hidden.value.code == "not_found"


async def test_the_worker_reads_orders_but_never_changes_them(worker_database: Database) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        await session.execute(text("SELECT count(*) FROM dealing_orders"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(text("UPDATE dealing_orders SET status = 'withdrawn'"))


# What the database refuses whatever the application does -----------------------------------------


async def change(database: Database, actor: Actor, order_id: str, assignments: str) -> None:
    async with database.actor_transaction(actor) as session:
        await session.execute(
            text(f"UPDATE dealing_orders SET {assignments} WHERE id = :id"),  # noqa: S608
            {"id": order_id, "actor": actor.id},
        )


async def test_the_database_refuses_a_self_approval(app_database: Database) -> None:
    daniel = new_staff()
    order = await prepare(app_database, daniel)
    with pytest.raises(DBAPIError, match="ck_dealing_orders_second_person"):
        await change(
            app_database,
            daniel,
            order.id,
            "status = 'approved', decided_by = :actor, decided_at = now()",
        )


async def test_the_database_refuses_a_decision_signed_as_someone_else(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    with pytest.raises(DBAPIError, match="only decided, by the person signed in"):
        async with app_database.actor_transaction(daniel) as session:
            await session.execute(
                text(
                    "UPDATE dealing_orders SET status = 'approved', decided_by = :other, "
                    "decided_at = now() WHERE id = :id"
                ),
                {"id": order.id, "other": grace.id},
            )


async def test_the_database_refuses_an_order_prepared_in_another_name(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    with pytest.raises(DBAPIError, match="starts as prepared, by the person signed in"):
        async with app_database.actor_transaction(daniel) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_orders (id, kind, product_code, currency, face_minor, "
                    "prepared_by, prepared_at) VALUES (:id, 'auction_bid', 'TREA-91', 'UGX', "
                    "100000, :other, now())"
                ),
                {"id": grace.id, "other": grace.id},
            )


async def test_the_database_refuses_a_rejection_by_whoever_recorded_the_result(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await recorded(app_database, daniel, grace)
    with pytest.raises(DBAPIError, match="rejected by a second person"):
        async with app_database.actor_transaction(daniel) as session:
            await session.execute(
                text(
                    "UPDATE dealing_orders SET status = 'approved', result_face_minor = NULL, "
                    "result_amount_minor = NULL, result_settled_on = NULL, "
                    "result_matures_on = NULL, custodian_reference = NULL, "
                    "result_recorded_by = NULL, result_recorded_at = NULL, "
                    "result_rejection_note = 'Typo' WHERE id = :id"
                ),
                {"id": order.id},
            )


async def test_no_order_is_settled_without_its_ledger_entry_and_bills(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await recorded(app_database, daniel, grace)
    with pytest.raises(DBAPIError, match="settled by a second person, and only settled"):
        await change(app_database, grace, order.id, "status = 'settled'")
    with pytest.raises(DBAPIError, match="needs the ledger entry and bills its result shows"):
        await change(
            app_database,
            grace,
            order.id,
            f"status = 'settled', settled_by = '{grace.id}', settled_at = now(), "
            f"settlement_entry_id = '{order.id}'",
        )


async def test_a_step_cannot_be_skipped(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    with pytest.raises(DBAPIError, match="cannot go from prepared to unsuccessful"):
        await change(
            app_database,
            grace,
            order.id,
            "status = 'unsuccessful', decided_by = :actor, decided_at = now(), "
            "result_recorded_by = :actor, result_recorded_at = now()",
        )


async def test_what_an_order_is_for_never_changes(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    with pytest.raises(DBAPIError, match="permission denied"):
        await change(app_database, grace, order.id, "face_minor = 500000000")
    with pytest.raises(DBAPIError, match="permission denied"):
        await change(app_database, grace, order.id, "prepared_by = :actor")


async def test_a_finished_order_never_changes_and_is_never_removed(
    app_database: Database, migrator_database: Database
) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    async with app_database.actor_transaction(daniel) as session:
        await dealing.withdraw_order(session, daniel, order.id, NOW)
    with pytest.raises(DBAPIError, match="cannot go from withdrawn to approved"):
        await change(
            app_database,
            grace,
            order.id,
            "status = 'approved', decided_by = :actor, decided_at = now()",
        )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(text("DELETE FROM dealing_orders"))
    # Not even the table owner removes one.
    for statement in (
        "DELETE FROM dealing_orders",
        "TRUNCATE dealing_orders, dealing_lots, dealing_lot_sales",
    ):
        with pytest.raises(DBAPIError, match=r"never (removed|changed or removed)"):
            async with migrator_database.transaction() as session:
                await session.execute(text(statement))


async def test_a_result_larger_than_the_order_is_refused_by_the_database(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await approved(app_database, daniel, grace)
    with pytest.raises(DBAPIError, match="ck_dealing_orders_result_amounts"):
        await change(
            app_database,
            daniel,
            order.id,
            "status = 'result_recorded', result_face_minor = face_minor + 1, "
            "result_amount_minor = 1, result_settled_on = DATE '2026-11-19', "
            "result_matures_on = DATE '2027-02-18', custodian_reference = 'X', "
            "result_recorded_by = :actor, result_recorded_at = now()",
        )


# Two people at once -----------------------------------------------------------------------------


async def test_two_people_approving_at_once_approve_it_once(app_database: Database) -> None:
    order = await prepare(app_database, new_staff())
    barrier = AsyncBarrier(2)

    async def approve() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await dealing.approve_order(session, staff, order.id, NOW)
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(approve(), approve())) == ["approved", "conflict"]


async def test_an_approval_racing_a_withdrawal_ends_one_way(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await prepare(app_database, daniel)
    barrier = AsyncBarrier(2)

    async def act(staff: Actor, approve: bool) -> str:
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                if approve:
                    await dealing.approve_order(session, staff, order.id, NOW)
                else:
                    await dealing.withdraw_order(session, staff, order.id, NOW)
        except ApplicationError as error:
            return error.code
        return "approved" if approve else "withdrawn"

    outcomes = sorted(await asyncio.gather(act(grace, True), act(daniel, False)))
    async with app_database.actor_transaction(daniel) as session:
        final = await dealing.get_order(session, order.id)
    assert outcomes in (["approved", "conflict"], ["conflict", "withdrawn"])
    assert final.status.value in outcomes


async def test_a_result_recorded_twice_at_once_is_recorded_once(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    order = await approved(app_database, daniel, grace)
    barrier = AsyncBarrier(2)

    async def record(staff: Actor, fill: Fill | None) -> str:
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await dealing.record_result(session, staff, order.id, fill, NOW)
        except ApplicationError as error:
            return error.code
        return "recorded"

    outcomes = sorted(await asyncio.gather(record(daniel, FILL), record(grace, None)))
    assert outcomes == ["conflict", "recorded"]
