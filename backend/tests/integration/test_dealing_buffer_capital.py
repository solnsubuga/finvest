"""Finvest's own money in the buffer against real PostgreSQL: paid in and taken out by two
people, what the buffer then counts, and what the database refuses.

These tests use dollars, like the buffer tests in test_dealing_withdrawal_funding, and move the
dollar buffer to the level each needs before they start. What Finvest paid in stays, so each
test reads it before and compares the change.
"""

import asyncio
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.dealing import capital, holdings
from app.modules.dealing.domain.capital import Direction, MovementStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.integration.test_dealing_withdrawal_funding import (
    NOW,
    dollar_buffer,
    set_buffer,
    usd,
)
from tests.support.clock import AsyncBarrier
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

TODAY = NOW.now().date()


async def paid_in(database: Database) -> Money:
    async with database.actor_transaction(new_staff()) as session:
        return await capital.paid_in(session, Currency.USD)


async def propose(
    database: Database,
    staff: Actor,
    direction: Direction,
    cents: int,
    *,
    reference: str | None = None,
    value_date: date | None = None,
) -> capital.MovementView:
    async with database.actor_transaction(staff) as session:
        return await capital.propose(
            session,
            staff,
            direction=direction,
            amount=usd(cents),
            reason="Top up the dollar buffer",
            bank_reference=reference,
            value_date=value_date,
            clock=NOW,
        )


async def pay_in(database: Database, cents: int) -> capital.MovementView:
    movement = await propose(
        database, new_staff(), Direction.IN, cents, reference=f"FT{push_id()}", value_date=TODAY
    )
    return await approve(database, new_staff(), movement.id)


async def approve(database: Database, staff: Actor, movement_id: str) -> capital.MovementView:
    async with database.actor_transaction(staff) as session:
        return await capital.approve(session, staff, movement_id, NOW)


def refusal(error: pytest.ExceptionInfo[ApplicationError]) -> str:
    return error.value.fields[0].code


async def test_money_paid_in_counts_in_the_buffer_once_a_second_person_approves(
    app_database: Database,
) -> None:
    await set_buffer(app_database, 0)
    before = await paid_in(app_database)
    daniel, grace = new_staff(), new_staff()
    reference = f"FT{push_id()}"
    movement = await propose(
        app_database, daniel, Direction.IN, 2_000_000, reference=reference, value_date=TODAY
    )
    assert movement.status is MovementStatus.PROPOSED
    # Nothing is posted yet.
    assert (await dollar_buffer(app_database)).buffer == usd(0)
    with pytest.raises(ApplicationError) as same:
        await approve(app_database, daniel, movement.id)
    assert same.value.code == "forbidden"

    approved = await approve(app_database, grace, movement.id)
    assert (approved.status, approved.decided_by, approved.value_date) == (
        MovementStatus.APPROVED,
        grace.id,
        TODAY,
    )
    assert approved.ledger_entry_id is not None
    assert (await dollar_buffer(app_database)).buffer == usd(2_000_000)
    assert await paid_in(app_database) == before + usd(2_000_000)
    with pytest.raises(ApplicationError) as again:
        await approve(app_database, new_staff(), movement.id)
    assert again.value.code == "conflict"

    # Reconciliation expects it on the trust account's statement, by the bank's reference.
    async with app_database.actor_transaction(new_staff()) as session:
        expected = await holdings.expected_bank_movements(session, date_from=TODAY, date_to=TODAY)
    assert [
        (m.amount_minor, m.ledger_entry_id) for m in expected if m.bank_reference == reference
    ] == [(2_000_000, approved.ledger_entry_id)]


async def test_paying_in_needs_the_bank_reference_and_a_day_it_has_arrived(
    app_database: Database,
) -> None:
    staff = new_staff()
    with pytest.raises(ApplicationError) as no_reference:
        await propose(app_database, staff, Direction.IN, 100, value_date=TODAY)
    assert refusal(no_reference) == "reference_required"
    with pytest.raises(ApplicationError) as tomorrow:
        await propose(
            app_database, staff, Direction.IN, 100, reference="FT1", value_date=date(2026, 11, 20)
        )
    assert refusal(tomorrow) == "value_date_in_future"
    reference = f"FT{push_id()}"
    await propose(app_database, staff, Direction.IN, 100, reference=reference, value_date=TODAY)
    with pytest.raises(ApplicationError) as twice:
        await propose(app_database, staff, Direction.IN, 100, reference=reference, value_date=TODAY)
    assert refusal(twice) == "already_recorded"


async def test_taking_out_is_refused_beyond_what_was_paid_in_or_what_the_buffer_holds(
    app_database: Database,
) -> None:
    await set_buffer(app_database, 0)
    await pay_in(app_database, 1_000_000)
    # Client money owed takes the buffer below what Finvest put in.
    await set_buffer(app_database, 400_000)
    with pytest.raises(ApplicationError) as beyond_buffer:
        await propose(app_database, new_staff(), Direction.OUT, 500_000)
    assert refusal(beyond_buffer) == "more_than_the_buffer"

    # A buffer bigger than all Finvest put in still only gives back what it put in.
    await set_buffer(app_database, (await paid_in(app_database)).minor + 1_000_000)
    with pytest.raises(ApplicationError) as beyond_paid_in:
        await propose(
            app_database, new_staff(), Direction.OUT, (await paid_in(app_database)).minor + 1
        )
    assert refusal(beyond_paid_in) == "more_than_paid_in"


async def test_taking_out_is_checked_again_when_approved_and_dated_that_day(
    app_database: Database,
) -> None:
    await set_buffer(app_database, 0)
    await pay_in(app_database, 600_000)
    first = await propose(app_database, new_staff(), Direction.OUT, 400_000)
    second = await propose(app_database, new_staff(), Direction.OUT, 400_000)
    assert first.value_date is None and first.bank_reference is None

    taken = await approve(app_database, new_staff(), first.id)
    assert (taken.status, taken.value_date) == (MovementStatus.APPROVED, TODAY)
    assert (await dollar_buffer(app_database)).buffer == usd(200_000)
    # Both fitted when proposed. Only one does now.
    with pytest.raises(ApplicationError) as late:
        await approve(app_database, new_staff(), second.id)
    assert refusal(late) == "more_than_the_buffer"

    async with app_database.actor_transaction(new_staff()) as session:
        expected = await holdings.expected_bank_movements(session, date_from=TODAY, date_to=TODAY)
    assert [m.amount_minor for m in expected if m.bank_reference == first.id] == [-400_000]


async def test_two_approvals_at_once_never_take_out_more_than_the_buffer(
    app_database: Database,
) -> None:
    await set_buffer(app_database, 0)
    await pay_in(app_database, 600_000)
    movements = [await propose(app_database, new_staff(), Direction.OUT, 400_000) for _ in "ab"]
    barrier = AsyncBarrier(2)

    async def decide(movement_id: str) -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await capital.approve(session, staff, movement_id, NOW)
        except ApplicationError as error:
            return error.fields[0].code
        return "approved"

    results = await asyncio.gather(*(decide(m.id) for m in movements))
    assert sorted(results) == ["approved", "more_than_the_buffer"]
    assert (await dollar_buffer(app_database)).buffer == usd(200_000)


async def test_a_decline_posts_nothing_and_needs_a_reason(app_database: Database) -> None:
    await set_buffer(app_database, 0)
    movement = await propose(
        app_database, new_staff(), Direction.IN, 300, reference=f"FT{push_id()}", value_date=TODAY
    )
    grace = new_staff()
    with pytest.raises(ApplicationError) as no_reason:
        async with app_database.actor_transaction(grace) as session:
            await capital.decline(session, grace, movement.id, " ", NOW)
    assert refusal(no_reason) == "reason_required"
    async with app_database.actor_transaction(grace) as session:
        declined = await capital.decline(session, grace, movement.id, "Not on the statement", NOW)
    assert (declined.status, declined.ledger_entry_id) == (MovementStatus.DECLINED, None)
    assert (await dollar_buffer(app_database)).buffer == usd(0)


async def test_the_database_refuses_one_person_a_changed_proposal_and_a_made_up_entry(
    app_database: Database, worker_database: Database
) -> None:
    daniel = new_staff()
    movement = await propose(
        app_database, daniel, Direction.IN, 700, reference=f"FT{push_id()}", value_date=TODAY
    )

    async def as_staff(staff: Actor, statement: str, values: dict[str, object]) -> None:
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text(statement), values)

    approve_sql = (
        "UPDATE dealing_buffer_capital SET status = 'approved', decided_by = :by, "
        "decided_at = now(), ledger_entry_id = :entry WHERE id = :id"
    )
    with pytest.raises(DBAPIError, match="ck_dealing_buffer_capital_two_people"):
        await as_staff(
            daniel,
            "UPDATE dealing_buffer_capital SET status = 'declined', decided_by = :by, "
            "decided_at = now(), decision_reason = 'Mine' WHERE id = :id",
            {"by": daniel.id, "id": movement.id},
        )
    grace = new_staff()
    with pytest.raises(DBAPIError, match="decided by the person signed in"):
        await as_staff(grace, approve_sql, {"by": push_id(), "entry": None, "id": movement.id})

    # An entry that is not this movement's, here another movement's.
    other = await pay_in(app_database, 900)
    with pytest.raises(DBAPIError, match="names the entry it posted"):
        await as_staff(
            grace, approve_sql, {"by": grace.id, "entry": other.ledger_entry_id, "id": movement.id}
        )
    # The amount and the rest are not even granted. The day it arrived is, for taking out.
    with pytest.raises(DBAPIError, match="does not change"):
        await as_staff(
            grace,
            "UPDATE dealing_buffer_capital SET status = 'declined', decided_by = :by, "
            "decided_at = now(), decision_reason = 'x', value_date = value_date - 1 "
            "WHERE id = :id",
            {"by": grace.id, "id": movement.id},
        )
    with pytest.raises(DBAPIError, match="once"):
        await as_staff(
            grace,
            "UPDATE dealing_buffer_capital SET decision_reason = 'changed' WHERE id = :id",
            {"id": other.id},
        )
    with pytest.raises(DBAPIError, match="proposed by the person signed in"):
        await as_staff(
            grace,
            "INSERT INTO dealing_buffer_capital (id, direction, currency, amount_minor, reason, "
            "status, proposed_by, proposed_at) VALUES (:id, 'out', 'USD', 1, 'x', 'proposed', "
            ":by, now())",
            {"id": push_id(), "by": daniel.id},
        )
    for statement in (
        "DELETE FROM dealing_buffer_capital WHERE id = :id",
        "UPDATE dealing_buffer_capital SET status = 'declined' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": movement.id})
