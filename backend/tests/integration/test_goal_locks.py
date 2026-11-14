"""Goal locks against real PostgreSQL: a locked goal's savings are not taken out early or sent
on until its date, and unlocking frees them seven days after the client asks (Solo's rule)."""

import secrets
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.goals import service as goals
from app.modules.goals.domain.goals import GoalCategory
from app.modules.goals.domain.plans import add_months
from app.modules.investments import service as investments
from app.modules.investments import transfers
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Money
from app.shared.push_ids import push_id
from tests.support.investments import PRESENT, approved_client, at, pay, started, today
from tests.support.kyc import verify
from tests.support.ledger import key, ugx

pytestmark = pytest.mark.integration

DAY = today()
NOW = PRESENT
YEAR_ON = add_months(DAY, 12)
WEEK_ON = DAY + timedelta(days=7)


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def person(app_database: Database) -> Actor:
    """A client with a sign-in account and an approved identity check, who can send and
    receive investments."""
    client_id = push_id()
    async with app_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO identity_clients (id, phone, password_hash, password_changed_at, "
                "created_at) VALUES (:id, :phone, 'x', now(), now())"
            ),
            {"id": client_id, "phone": f"+256772{secrets.randbelow(1_000_000):06d}"},
        )
    client = Actor("client", client_id)
    await verify(app_database, client, NOW)
    return client


async def new_goal(
    database: Database, client: Actor, *, target_date: object = YEAR_ON
) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.create_goal(
            session,
            client,
            idempotency_key=key(),
            name="School fees, Term 1",
            category=GoalCategory.SCHOOL_FEES,
            target=ugx(5_000_000),
            target_date=target_date,  # type: ignore[arg-type]
            clock=NOW,
        )


async def save(
    database: Database, client: Actor, goal_id: str, amount: Money
) -> investments.ClientInvestment:
    async with database.actor_transaction(client) as session:
        return await goals.save_to_goal(
            session, client, goal_id, idempotency_key=key(), amount=amount, clock=NOW
        )


async def started_saving(
    app_database: Database, worker_database: Database, client: Actor, goal_id: str
) -> str:
    saving = await save(app_database, client, goal_id, ugx(1_000_000))
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, saving.id)
    await pay(worker_database, investment, investment.amount, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, saving.id, confirmed_on=DAY, clock=NOW)
    return saving.id


async def lock(database: Database, client: Actor, goal_id: str) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.lock_goal(session, client, goal_id, NOW)


async def unlock(database: Database, client: Actor, goal_id: str) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.unlock_goal(session, client, goal_id, NOW)


async def seen(
    database: Database, client: Actor, investment_id: str, clock: object = NOW
) -> investments.ClientInvestment:
    async with database.actor_transaction(client) as session:
        return await investments.client_investment(session, client, investment_id, clock)  # type: ignore[arg-type]


async def ask_to_withdraw(database: Database, client: Actor, investment_id: str) -> None:
    async with database.actor_transaction(client) as session:
        await investments.request_withdrawal(
            session,
            client,
            investment_id,
            destination_id=push_id(),
            idempotency_key=key(),
            clock=NOW,
        )


async def lock_row(database: Database, investment_id: str) -> object:
    async with database.actor_transaction(SYSTEM) as session:
        return await session.scalar(
            text("SELECT locked_until FROM investment_withdrawal_locks WHERE investment_id = :id"),
            {"id": investment_id},
        )


# Locking ----------------------------------------------------------------------------------------


async def test_a_locked_goal_locks_its_savings_and_each_new_one_until_its_date(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    running = await started_saving(app_database, worker_database, client, goal.id)
    waiting = (await save(app_database, client, goal.id, ugx(500_000))).id
    assert (await seen(app_database, client, running)).can_withdraw

    locked = await lock(app_database, client, goal.id)
    assert (locked.locked_until, locked.unlock_requested_at) == (YEAR_ON, None)
    for investment_id in (running, waiting):
        view = await seen(app_database, client, investment_id)
        assert view.withdrawal_locked_until == YEAR_ON
        assert not view.can_withdraw
        assert not view.can_send
    later = (await save(app_database, client, goal.id, ugx(500_000))).id
    assert await lock_row(worker_database, later) == YEAR_ON

    with pytest.raises(ApplicationError) as error:
        await ask_to_withdraw(app_database, client, running)
    assert refused(error) == ("conflict", [("investment", "locked")])
    # Locking again changes nothing.
    again = await lock(app_database, client, goal.id)
    assert (again.locked_until, again.unlock_requested_at) == (YEAR_ON, None)


async def test_a_goal_with_no_date_or_closed_cannot_be_locked(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    no_date = await new_goal(app_database, client, target_date=None)
    with pytest.raises(ApplicationError) as error:
        await lock(app_database, client, no_date.id)
    assert refused(error) == ("validation_failed", [("target_date", "no_date_to_lock")])

    goal = await new_goal(app_database, client)
    with pytest.raises(ApplicationError) as error:
        await unlock(app_database, client, goal.id)
    assert error.value.code == "conflict"
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal.id, NOW)
    with pytest.raises(ApplicationError) as error:
        await lock(app_database, client, goal.id)
    assert error.value.code == "conflict"

    stranger = await approved_client(app_database, NOW)
    other = await new_goal(app_database, stranger)
    with pytest.raises(ApplicationError) as error:
        await lock(app_database, client, other.id)
    assert error.value.code == "not_found"


async def test_a_locked_saving_is_not_sent_to_another_client(
    app_database: Database, worker_database: Database
) -> None:
    sender, recipient = await person(app_database), await person(app_database)
    goal = await new_goal(app_database, sender)
    running = await started_saving(app_database, worker_database, sender, goal.id)
    await lock(app_database, sender, goal.id)
    async with app_database.actor_transaction(recipient) as session:
        number = await session.scalar(
            text("SELECT account_number FROM kyc_checks WHERE client_id = :id"),
            {"id": recipient.id},
        )
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(sender) as session:
            await transfers.send_investment(
                session,
                sender,
                running,
                to_account_number=str(number),
                amount=ugx(500_000),
                message=None,
                restricted_until=None,
                idempotency_key=key(),
                clock=NOW,
            )
    assert refused(error) == ("conflict", [("investment", "locked")])


# Unlocking --------------------------------------------------------------------------------------


async def test_unlocking_frees_the_savings_seven_days_after_asking(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    running = await started_saving(app_database, worker_database, client, goal.id)
    await lock(app_database, client, goal.id)

    asked = await unlock(app_database, client, goal.id)
    assert asked.locked_until == WEEK_ON
    assert asked.unlock_requested_at is not None
    assert await lock_row(worker_database, running) == WEEK_ON
    # Asking again changes nothing, and a new saving is locked only until the same day.
    assert (await unlock(app_database, client, goal.id)).unlock_requested_at == (
        asked.unlock_requested_at
    )
    later = (await save(app_database, client, goal.id, ugx(500_000))).id
    assert await lock_row(worker_database, later) == WEEK_ON

    # Still locked the day before; free on the day.
    eve = at(WEEK_ON - timedelta(days=1))
    assert not (await seen(app_database, client, running, eve)).can_withdraw
    free = await seen(app_database, client, running, at(WEEK_ON))
    assert (free.can_withdraw, free.can_send, free.withdrawal_locked_until) == (True, True, None)
    async with app_database.actor_transaction(client) as session:
        lapsed = await goals.client_goal(session, client, goal.id, at(WEEK_ON))
    assert (lapsed.locked_until, lapsed.unlock_requested_at) == (None, None)

    # Locking again before the day cancels the ask.
    relocked = await lock(app_database, client, goal.id)
    assert (relocked.locked_until, relocked.unlock_requested_at) == (YEAR_ON, None)
    assert await lock_row(worker_database, running) == YEAR_ON


async def test_closing_a_locked_goal_starts_the_wait_to_unlock(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    running = await started_saving(app_database, worker_database, client, goal.id)
    await lock(app_database, client, goal.id)
    async with app_database.actor_transaction(client) as session:
        closed = await goals.close_goal(session, client, goal.id, NOW)
    assert closed.locked_until == WEEK_ON
    assert closed.unlock_requested_at is not None
    assert await lock_row(worker_database, running) == WEEK_ON


async def test_a_rolled_over_saving_keeps_its_lock(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    first = await started_saving(app_database, worker_database, client, goal.id)
    await lock(app_database, client, goal.id)
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, first)
    assert investment.maturity_date is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        old = await investments.mature_investment(
            session, first, investment.maturity_date, at(investment.maturity_date)
        )
    assert old.rolled_into_id is not None
    assert await lock_row(worker_database, old.rolled_into_id) == YEAR_ON


# What the database holds ------------------------------------------------------------------------


async def test_the_database_holds_a_lock_to_its_day(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        await investments.lock_withdrawals(
            session,
            client_id=investment.client_id,
            investment_ids=[investment.id],
            until=YEAR_ON,
            now=NOW.now(),
        )

    async def run(database: Database, actor: Actor, sql: str, **values: object) -> None:
        async with database.actor_transaction(actor) as session:
            await session.execute(text(sql), values)

    shorten = "UPDATE investment_withdrawal_locks SET locked_until = :day WHERE investment_id = :id"
    # Sooner than seven days from today is refused, for the client and the worker alike.
    for database, actor in ((app_database, client), (worker_database, SYSTEM)):
        with pytest.raises(DBAPIError, match="seven days after asking"):
            await run(database, actor, shorten, day=WEEK_ON - timedelta(days=1), id=investment.id)
    with pytest.raises(DBAPIError, match="permission denied"):
        await run(
            app_database,
            client,
            "DELETE FROM investment_withdrawal_locks WHERE investment_id = :id",
            id=investment.id,
        )
    # The worker cannot remove it either.
    with pytest.raises(DBAPIError, match="permission denied"):
        await run(
            worker_database,
            SYSTEM,
            "DELETE FROM investment_withdrawal_locks WHERE investment_id = :id",
            id=investment.id,
        )

    # While it runs, the investment is not asked for early, even past the application.
    destination = push_id()
    with pytest.raises(DBAPIError, match="not withdrawn early"):
        await run(
            app_database,
            client,
            "UPDATE investments SET withdrawal_requested_at = clock_timestamp(), "
            "withdrawal_destination_id = :destination WHERE id = :id",
            destination=destination,
            id=investment.id,
        )

    # Seven days on is allowed, and after that only a later day.
    await run(app_database, client, shorten, day=WEEK_ON, id=investment.id)
    with pytest.raises(DBAPIError, match="seven days after asking"):
        await run(app_database, client, shorten, day=WEEK_ON - timedelta(days=1), id=investment.id)
    await run(app_database, client, shorten, day=YEAR_ON, id=investment.id)


async def test_a_lock_is_only_on_ones_own_waiting_or_running_investment(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    owner = Actor("client", investment.client_id)
    stranger = await approved_client(app_database, NOW)
    insert = (
        "INSERT INTO investment_withdrawal_locks (investment_id, client_id, locked_until, "
        "created_at, updated_at) VALUES (:id, :client_id, :until, now(), now())"
    )

    async def run(actor: Actor, **values: object) -> None:
        async with app_database.actor_transaction(actor) as session:
            await session.execute(text(insert), values)

    # Another client's investment, under their own name or its owner's.
    with pytest.raises(DBAPIError, match="waiting or running investment"):
        await run(stranger, id=investment.id, client_id=stranger.id, until=YEAR_ON)
    with pytest.raises(DBAPIError, match="row-level security"):
        await run(stranger, id=investment.id, client_id=owner.id, until=YEAR_ON)
    # Today or earlier is no lock.
    with pytest.raises(DBAPIError, match="waiting or running investment"):
        await run(owner, id=investment.id, client_id=owner.id, until=DAY)
    # Staff never lock.
    staff_kind = Actor("staff", push_id())
    with pytest.raises(DBAPIError):
        await run(staff_kind, id=investment.id, client_id=owner.id, until=YEAR_ON)


async def test_the_goals_own_copy_of_the_lock_is_held_the_same_way(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    await lock(app_database, client, goal.id)
    for day in (None, WEEK_ON - timedelta(days=1)):
        with pytest.raises(DBAPIError, match="seven days after asking"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text("UPDATE goals SET locked_until = :day WHERE id = :id"),
                    {"day": day, "id": goal.id},
                )
    other = await new_goal(app_database, client)
    with pytest.raises(DBAPIError, match="ck_goals_unlock"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text("UPDATE goals SET unlock_requested_at = now() WHERE id = :id"),
                {"id": other.id},
            )
