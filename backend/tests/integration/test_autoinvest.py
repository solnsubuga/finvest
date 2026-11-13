"""Automatic investing against real PostgreSQL: plans, the daily run, the fences and the races."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.autoinvest import service as autoinvest
from app.modules.autoinvest.domain.plans import MAX_OPEN_PLANS
from app.modules.payments.domain.deposits import Network
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import approved_client
from tests.support.ledger import client as new_client
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

PHONE = "0772 123 456"
PAYER = "+256772123456"
ACTIVE, PAUSED, STOPPED = (
    autoinvest.PlanStatus.ACTIVE,
    autoinvest.PlanStatus.PAUSED,
    autoinvest.PlanStatus.STOPPED,
)
DUE, MISSED = autoinvest.RunStatus.DUE, autoinvest.RunStatus.MISSED


def at(day: date, hour: int = 9) -> FakeClock:
    """`hour` in Kampala on `day`."""
    return FakeClock(datetime(day.year, day.month, day.day, hour - 3, tzinfo=UTC))


# Friday 13 November 2026.
DAY = date(2026, 11, 13)
NOW = at(DAY)


def terms(
    *,
    amount: Money | None = None,
    frequency: autoinvest.Frequency = autoinvest.Frequency.MONTHLY,
    day: int = 28,
    product_code: str = "TREA-91",
    phone: str = PHONE,
) -> autoinvest.PlanTerms:
    return autoinvest.PlanTerms(
        product_code=product_code,
        amount=amount or ugx(200_000),
        schedule=autoinvest.Schedule(frequency, day),
        network=Network.MTN,
        phone=phone,
    )


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def create(
    database: Database,
    client: Actor,
    plan: autoinvest.PlanTerms | None = None,
    *,
    clock: FakeClock = NOW,
    idempotency_key: str | None = None,
) -> autoinvest.Plan:
    async with database.actor_transaction(client) as session:
        return await autoinvest.create_plan(
            session,
            client,
            plan or terms(),
            idempotency_key=idempotency_key or key(),
            account_phone=PHONE,
            clock=clock,
        )


async def open_runs(worker_database: Database, clock: FakeClock) -> autoinvest.Opened:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await autoinvest.open_due_runs(session, clock)


async def runs_of(database: Database, client: Actor, plan_id: str) -> list[autoinvest.Run]:
    async with database.actor_transaction(client) as session:
        return await autoinvest.plan_runs(session, plan_id)


async def plan_of(database: Database, client: Actor, plan_id: str) -> autoinvest.Plan:
    async with database.actor_transaction(client) as session:
        return await autoinvest.client_plan(session, client, plan_id)


# Setting a plan up ------------------------------------------------------------------------------


async def test_a_plan_runs_from_its_first_due_day_after_today(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    plan = await create(app_database, client)
    assert plan.status is ACTIVE
    assert plan.next_run_on == date(2026, 11, 28)
    assert plan.payer_msisdn == PAYER
    assert plan.amount == ugx(200_000)
    weekly = await create(app_database, client, terms(frequency=autoinvest.Frequency.WEEKLY, day=5))
    # Friday's plan made on a Friday starts next Friday.
    assert weekly.next_run_on == date(2026, 11, 20)


async def test_a_retried_set_up_makes_one_plan(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    idempotency_key = key()
    first = await create(app_database, client, idempotency_key=idempotency_key)
    again = await create(app_database, client, idempotency_key=idempotency_key)
    assert again.id == first.id
    with pytest.raises(ApplicationError) as error:
        await create(app_database, client, terms(day=1), idempotency_key=idempotency_key)
    assert error.value.code == "idempotency_key_reused"


async def test_two_taps_at_once_make_one_plan(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    idempotency_key = key()
    barrier = AsyncBarrier(2)

    async def tap() -> str:
        async with app_database.actor_transaction(client) as session:
            await barrier.wait()
            plan = await autoinvest.create_plan(
                session,
                client,
                terms(),
                idempotency_key=idempotency_key,
                account_phone=PHONE,
                clock=NOW,
            )
            return plan.id

    first, second = await asyncio.gather(tap(), tap())
    assert first == second
    async with app_database.actor_transaction(client) as session:
        assert len(await autoinvest.client_plans(session, client)) == 1


async def test_nobody_sets_up_a_plan_before_their_identity_is_checked(
    app_database: Database,
) -> None:
    with pytest.raises(ApplicationError) as error:
        await create(app_database, new_client())
    assert error.value.code == "forbidden"


@pytest.mark.parametrize(
    ("plan", "problem"),
    [
        (terms(amount=ugx(99_999)), ("amount", "below_minimum")),
        (terms(amount=ugx(5_000_001)), ("amount", "amount_too_large")),
        (terms(amount=Money(50_000, Currency.USD)), ("amount", "unsupported_currency")),
        (terms(phone="0701 000 200"), ("phone", "not_account_phone")),
        (terms(phone="12345"), ("phone", "invalid_phone")),
    ],
)
async def test_a_plan_no_run_could_pay_for_is_refused(
    app_database: Database, plan: autoinvest.PlanTerms, problem: tuple[str, str]
) -> None:
    client = await approved_client(app_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await create(app_database, client, plan)
    assert refused(error) == ("validation_failed", [problem])


async def test_an_unknown_product_is_not_found(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await create(app_database, client, terms(product_code="NOPE-1"))
    assert error.value.code == "not_found"


async def test_a_client_keeps_at_most_ten_open_plans(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    plans = [await create(app_database, client) for _ in range(MAX_OPEN_PLANS)]
    with pytest.raises(ApplicationError) as error:
        await create(app_database, client)
    assert error.value.code == "conflict"
    # A stopped plan no longer counts.
    async with app_database.actor_transaction(client) as session:
        await autoinvest.stop_plan(session, client, plans[0].id, NOW)
    await create(app_database, client)


# Changing a plan --------------------------------------------------------------------------------


async def test_pausing_and_resuming_never_makes_up_the_days_in_between(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, NOW)
    plan = await create(app_database, client)
    async with app_database.actor_transaction(client) as session:
        paused = await autoinvest.pause_plan(session, client, plan.id, NOW)
        assert (paused.status, paused.next_run_on) == (PAUSED, None)
        assert await autoinvest.pause_plan(session, client, plan.id, NOW) == paused
    later = at(date(2027, 1, 5))
    async with app_database.actor_transaction(client) as session:
        resumed = await autoinvest.resume_plan(session, client, plan.id, later)
    assert (resumed.status, resumed.next_run_on) == (ACTIVE, date(2027, 1, 28))
    assert resumed.paused_at is None


async def test_a_stopped_plan_never_changes_again(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    plan = await create(app_database, client)
    async with app_database.actor_transaction(client) as session:
        stopped = await autoinvest.stop_plan(session, client, plan.id, NOW)
        assert (stopped.status, stopped.next_run_on) == (STOPPED, None)
        assert await autoinvest.stop_plan(session, client, plan.id, NOW) == stopped
    for command in (autoinvest.resume_plan, autoinvest.pause_plan):
        with pytest.raises(ApplicationError) as error:
            async with app_database.actor_transaction(client) as session:
                await command(session, client, plan.id, NOW)
        assert error.value.code == "conflict"
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(client) as session:
            await autoinvest.change_plan(
                session, client, plan.id, terms(day=1), account_phone=PHONE, clock=NOW
            )
    assert error.value.code == "conflict"


async def test_a_new_day_moves_the_next_run_and_never_to_today(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    plan = await create(app_database, client)
    async with app_database.actor_transaction(client) as session:
        changed = await autoinvest.change_plan(
            session,
            client,
            plan.id,
            terms(day=13, amount=ugx(300_000)),
            account_phone=PHONE,
            clock=NOW,
        )
    assert changed.next_run_on == date(2026, 12, 13)
    assert changed.amount == ugx(300_000)
    async with app_database.actor_transaction(client) as session:
        # A new amount on the same day keeps the next due day.
        again = await autoinvest.change_plan(
            session,
            client,
            plan.id,
            terms(day=13, amount=ugx(400_000)),
            account_phone=PHONE,
            clock=NOW,
        )
    assert again.next_run_on == date(2026, 12, 13)


async def test_a_client_never_reaches_another_clients_plan(app_database: Database) -> None:
    owner = await approved_client(app_database, NOW)
    other = await approved_client(app_database, NOW)
    plan = await create(app_database, owner)
    async with app_database.actor_transaction(other) as session:
        assert await autoinvest.client_plans(session, other) == []
        assert await autoinvest.plan_runs(session, plan.id) == []
    for command in (autoinvest.pause_plan, autoinvest.stop_plan):
        with pytest.raises(ApplicationError) as error:
            async with app_database.actor_transaction(other) as session:
                await command(session, other, plan.id, NOW)
        assert error.value.code == "not_found"
    # The row policy holds even for SQL the service would never send.
    async with app_database.actor_transaction(other) as session:
        result = await session.execute(
            text("UPDATE autoinvest_plans SET amount_minor = 100000 WHERE id = :id"),
            {"id": plan.id},
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]


# What the database refuses ----------------------------------------------------------------------


async def test_the_database_refuses_a_plan_that_runs_today(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    plan = await create(app_database, client)
    with pytest.raises(DBAPIError, match="from tomorrow"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "UPDATE autoinvest_plans SET run_day = 13, next_run_on = :today, "
                    "updated_at = :now WHERE id = :id"
                ),
                {"id": plan.id, "today": DAY, "now": NOW.now()},
            )


async def test_only_the_worker_records_runs(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    plan = await create(app_database, client)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO autoinvest_runs (id, plan_id, client_id, due_on, status, "
                    "created_at, updated_at) VALUES ('-Nabc0000000000000R1', :plan, :client, "
                    ":day, 'due', now(), now())"
                ),
                {"plan": plan.id, "client": client.id, "day": DAY},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE autoinvest_plans SET amount_minor = 5000000 WHERE id = :id"),
                {"id": plan.id},
            )
    with pytest.raises(DBAPIError, match="own plan"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO autoinvest_runs (id, plan_id, client_id, due_on, status, "
                    "created_at, updated_at) VALUES ('-Nabc0000000000000R2', :plan, :other, "
                    ":day, 'due', now(), now())"
                ),
                {"plan": plan.id, "other": new_staff().id, "day": DAY},
            )


async def test_nothing_about_a_plan_is_ever_deleted(application_database: Database) -> None:
    for table in ("autoinvest_plans", "autoinvest_runs"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with application_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608


# The daily run ----------------------------------------------------------------------------------


async def due_plan(app_database: Database, due_on: date) -> tuple[Actor, autoinvest.Plan]:
    """An active monthly plan whose next due day is `due_on`, set up the day before."""
    client = await approved_client(app_database, NOW)
    plan = await create(
        app_database, client, terms(day=due_on.day), clock=at(due_on - timedelta(days=1))
    )
    assert plan.next_run_on == due_on
    return client, plan


async def test_the_daily_run_opens_one_run_on_the_due_day_and_moves_the_plan_on(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2026, 12, 3)
    client, plan = await due_plan(app_database, day)
    opened = await open_runs(worker_database, at(day))
    [run] = await runs_of(app_database, client, plan.id)
    assert run.id in opened.due
    assert (run.status, run.due_on, run.attempt) == (DUE, day, 1)
    assert (await plan_of(app_database, client, plan.id)).next_run_on == date(2027, 1, 3)
    # Running again the same day opens nothing more.
    again = await open_runs(worker_database, at(day, 15))
    assert run.id not in again.due
    assert len(await runs_of(app_database, client, plan.id)) == 1


async def test_the_daily_run_sends_nothing_at_night(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2026, 12, 4)
    client, plan = await due_plan(app_database, day)
    for hour in (5, 7, 20, 23):
        assert await open_runs(worker_database, at(day, hour)) == autoinvest.Opened((), 0)
    assert await runs_of(app_database, client, plan.id) == []


async def test_a_run_a_day_late_is_collected_and_an_older_one_is_missed(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2026, 12, 5)
    client, plan = await due_plan(app_database, day)
    await open_runs(worker_database, at(day + timedelta(days=1)))
    [run] = await runs_of(app_database, client, plan.id)
    assert (run.status, run.due_on) == (DUE, day)

    late_day = date(2026, 12, 7)
    late_client, late_plan = await due_plan(app_database, late_day)
    await open_runs(worker_database, at(late_day + timedelta(days=2)))
    [missed] = await runs_of(app_database, late_client, late_plan.id)
    assert (missed.status, missed.due_on, missed.code) == (MISSED, late_day, "late")
    assert (await plan_of(app_database, late_client, late_plan.id)).next_run_on == date(2027, 1, 7)


async def test_a_plan_with_a_run_still_open_misses_its_next_day(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2026, 11, 16)
    client = await approved_client(app_database, NOW)
    plan = await create(
        app_database,
        client,
        terms(frequency=autoinvest.Frequency.WEEKLY, day=1),
        clock=at(day - timedelta(days=1)),
    )
    await open_runs(worker_database, at(day))
    await open_runs(worker_database, at(day + timedelta(days=7)))
    later, first = await runs_of(app_database, client, plan.id)
    assert (first.status, first.due_on) == (DUE, day)
    assert (later.status, later.due_on, later.code) == (
        MISSED,
        day + timedelta(days=7),
        "previous_run_open",
    )


async def test_a_paused_plan_opens_no_runs(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2026, 12, 8)
    client, plan = await due_plan(app_database, day)
    async with app_database.actor_transaction(client) as session:
        await autoinvest.pause_plan(session, client, plan.id, at(day - timedelta(days=1)))
    await open_runs(worker_database, at(day))
    assert await runs_of(app_database, client, plan.id) == []


async def fail(worker_database: Database, run_id: str, retry_on: date | None) -> None:
    """What the payment step does when a prompt fails."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        await session.execute(
            text(
                "UPDATE autoinvest_runs SET status = 'failed', retry_on = :retry_on, "
                "code = 'declined', updated_at = now() WHERE id = :id"
            ),
            {"id": run_id, "retry_on": retry_on},
        )


async def test_a_failed_run_is_tried_once_more_the_next_day(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2026, 12, 9)
    client, plan = await due_plan(app_database, day)
    await open_runs(worker_database, at(day))
    [run] = await runs_of(app_database, client, plan.id)
    await fail(worker_database, run.id, day + timedelta(days=1))
    opened = await open_runs(worker_database, at(day + timedelta(days=1)))
    [retried] = await runs_of(app_database, client, plan.id)
    assert retried.id in opened.due
    assert (retried.status, retried.attempt, retried.retry_on) == (DUE, 2, None)
    # A second failure is final: the database refuses a third try.
    await fail(worker_database, run.id, None)
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE autoinvest_runs SET status = 'due' WHERE id = :id"), {"id": run.id}
            )


async def test_a_retry_for_a_plan_paused_since_is_missed(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2026, 12, 10)
    client, plan = await due_plan(app_database, day)
    await open_runs(worker_database, at(day))
    [run] = await runs_of(app_database, client, plan.id)
    await fail(worker_database, run.id, day + timedelta(days=1))
    async with app_database.actor_transaction(client) as session:
        await autoinvest.pause_plan(session, client, plan.id, at(day))
    await open_runs(worker_database, at(day + timedelta(days=1)))
    [missed] = await runs_of(app_database, client, plan.id)
    assert (missed.status, missed.attempt, missed.code) == (MISSED, 1, "plan_not_active")


async def test_two_daily_runs_at_once_open_each_run_once(
    app_database: Database, test_database: object
) -> None:
    day = date(2026, 12, 11)
    plans = [await due_plan(app_database, day) for _ in range(3)]
    workers = [Database(test_database.settings("finvest_worker")) for _ in range(2)]  # type: ignore[attr-defined]
    barrier = AsyncBarrier(2)

    async def run(database: Database) -> autoinvest.Opened:
        async with database.actor_transaction(SYSTEM) as session:
            await barrier.wait()
            return await autoinvest.open_due_runs(session, at(day))

    try:
        first, second = await asyncio.gather(*(run(database) for database in workers))
    finally:
        for database in workers:
            await database.close()
    assert not set(first.due) & set(second.due)
    for client, plan in plans:
        [run_row] = await runs_of(app_database, client, plan.id)
        assert run_row.id in set(first.due) | set(second.due)


async def test_staff_list_runs_by_day(app_database: Database, worker_database: Database) -> None:
    day = date(2026, 12, 14)
    _, plan = await due_plan(app_database, day)
    await open_runs(worker_database, at(day))
    async with app_database.actor_transaction(new_staff()) as session:
        listed = await autoinvest.list_runs(session, date_from=day, date_to=day, status=DUE)
    assert plan.id in {run.plan_id for run in listed}
