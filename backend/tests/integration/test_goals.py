"""Goals against real PostgreSQL: setting one up, saving towards it, its progress from real
investments, the fences the database holds and the races."""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.goals import reminders
from app.modules.goals import service as goals
from app.modules.goals.domain.goals import MAX_ACTIVE_GOALS, GoalCategory, GoalStatus, Standing
from app.modules.goals.domain.plans import add_months
from app.modules.goals.domain.reminders import next_due
from app.modules.goals.events import FOLLOW_ROLLOVER_KIND
from app.modules.goals.jobs import GoalJobs
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier
from tests.support.investments import PRESENT, approved_client, at, pay, today
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

# The database refuses an investment that is not created now, so these run at the real time.
DAY = today()
NOW = PRESENT
YEAR_ON = add_months(DAY, 12)
SOONER_THAN_EVERY_TERM = DAY + timedelta(days=40)


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def new_goal(
    database: Database,
    client: Actor,
    *,
    name: str = "School fees, Term 1",
    target: Money | None = None,
    target_date: date | None = YEAR_ON,
    category: GoalCategory = GoalCategory.SCHOOL_FEES,
    idempotency_key: str | None = None,
) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.create_goal(
            session,
            client,
            idempotency_key=idempotency_key or key(),
            name=name,
            category=category,
            target=target or ugx(2_000_000),
            target_date=target_date,
            clock=NOW,
        )


async def save(
    database: Database,
    client: Actor,
    goal_id: str,
    amount: Money,
    *,
    idempotency_key: str | None = None,
) -> investments.ClientInvestment:
    async with database.actor_transaction(client) as session:
        return await goals.save_to_goal(
            session,
            client,
            goal_id,
            idempotency_key=idempotency_key or key(),
            amount=amount,
            clock=NOW,
        )


async def started_saving(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    goal_id: str,
    amount: Money,
) -> str:
    saving = await save(app_database, client, goal_id, amount)
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, saving.id)
    await pay(worker_database, investment, amount, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, saving.id, confirmed_on=DAY, clock=NOW)
    return saving.id


async def view(database: Database, client: Actor, goal_id: str) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.client_goal(session, client, goal_id, NOW)


# Setting a goal up ------------------------------------------------------------------------------


async def test_a_goal_is_set_up_with_the_product_of_its_plan(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    assert goal.status is GoalStatus.ACTIVE
    assert goal.name == "School fees, Term 1"
    assert goal.product_code == "TREA-91"
    assert goal.progress.value == ugx(0)
    assert goal.progress.standing is Standing.ON_TRACK
    assert goal.savings == []
    assert goal.plan is not None
    assert goal.plan.product_code == "TREA-91"
    assert goal.plan.saving.minor % 1_000 == 0
    assert goal.plan.savings == 10
    assert goal.plan.rate_percent == "10.63"
    assert goal.plan.put_in + goal.plan.interest >= ugx(2_000_000)


async def test_the_plan_before_saving_matches_the_goal_once_saved(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    async with app_database.actor_transaction(client) as session:
        preview = await goals.client_plan(
            session, target=ugx(2_000_000), target_date=YEAR_ON, clock=NOW
        )
    goal = await new_goal(app_database, client)
    assert preview == goal.plan


async def test_a_plan_for_a_date_sooner_than_every_term_is_none(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    async with app_database.actor_transaction(client) as session:
        assert (
            await goals.client_plan(
                session, target=ugx(2_000_000), target_date=SOONER_THAN_EVERY_TERM, clock=NOW
            )
            is None
        )


async def test_a_goal_sooner_than_every_term_is_refused(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await new_goal(app_database, client, target_date=SOONER_THAN_EVERY_TERM)
    assert refused(error) == ("validation_failed", [("target_date", "sooner_than_every_term")])


async def test_a_goal_with_no_date_uses_the_shortest_term_and_has_no_plan(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(
        app_database,
        client,
        name="Emergency fund",
        category=GoalCategory.EMERGENCY,
        target=ugx(3_000_000),
        target_date=None,
    )
    assert goal.product_code == "TREA-91"
    assert goal.plan is None
    assert goal.progress.standing is Standing.NO_DATE


@pytest.mark.parametrize(
    ("name", "target", "target_date", "problem"),
    [
        ("  ", 2_000_000, YEAR_ON, ("name", "name_empty")),
        ("x" * 61, 2_000_000, YEAR_ON, ("name", "name_too_long")),
        ("Land", 2_000_000, DAY, ("target_date", "date_not_in_future")),
        ("Land", 2_000_000, DAY + timedelta(days=3661), ("target_date", "date_too_far")),
    ],
)
async def test_a_goal_that_breaks_a_rule_is_refused(
    app_database: Database, name: str, target: int, target_date: date, problem: tuple[str, str]
) -> None:
    client = await approved_client(app_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await new_goal(app_database, client, name=name, target=ugx(target), target_date=target_date)
    assert refused(error) == ("validation_failed", [problem])


async def test_a_dollar_goal_is_refused_while_no_dollar_product_is_live(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await new_goal(app_database, client, target=Money(100_000, Currency.USD), target_date=None)
    assert refused(error) == ("validation_failed", [("currency", "no_product")])


async def test_a_retry_returns_the_same_goal(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    idempotency_key = key()
    first = await new_goal(app_database, client, idempotency_key=idempotency_key)
    second = await new_goal(app_database, client, idempotency_key=idempotency_key)
    assert first.id == second.id
    with pytest.raises(ApplicationError) as error:
        await new_goal(app_database, client, idempotency_key=idempotency_key, name="Other")
    assert error.value.code == "idempotency_key_reused"


async def test_two_taps_at_once_set_up_one_goal(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    idempotency_key = key()
    barrier = AsyncBarrier(2)

    async def tap() -> str:
        async with app_database.actor_transaction(client) as session:
            await barrier.wait()
            goal = await goals.create_goal(
                session,
                client,
                idempotency_key=idempotency_key,
                name="Land",
                category=GoalCategory.LAND_OR_HOME,
                target=ugx(20_000_000),
                target_date=add_months(DAY, 24),
                clock=NOW,
            )
            return goal.id

    first, second = await asyncio.gather(tap(), tap())
    assert first == second
    async with app_database.actor_transaction(client) as session:
        assert len(await goals.client_goals(session, client, NOW)) == 1


async def test_open_goals_are_capped_even_when_set_up_at_once(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    for number in range(MAX_ACTIVE_GOALS - 1):
        await new_goal(app_database, client, name=f"Goal {number}")
    barrier = AsyncBarrier(2)

    async def tap(name: str) -> str:
        try:
            async with app_database.actor_transaction(client) as session:
                await barrier.wait()
                await goals.create_goal(
                    session,
                    client,
                    idempotency_key=key(),
                    name=name,
                    category=GoalCategory.OTHER,
                    target=ugx(1_000_000),
                    target_date=None,
                    clock=NOW,
                )
            return "saved"
        except ApplicationError as error:
            return error.code

    assert sorted(await asyncio.gather(tap("Last"), tap("One too many"))) == ["conflict", "saved"]


# Saving towards a goal --------------------------------------------------------------------------


async def test_a_saving_is_an_investment_in_the_goals_product_that_rolls_over(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    saving = await save(app_database, client, goal.id, ugx(200_000))
    assert saving.status is InvestmentStatus.AWAITING_PAYMENT
    assert saving.product_code == "TREA-91"
    assert saving.amount == ugx(200_000)
    assert saving.at_maturity is AtMaturity.ROLL_OVER
    after = await view(app_database, client, goal.id)
    assert [s.id for s in after.savings] == [saving.id]
    assert after.progress.value == ugx(0)
    assert after.progress.awaiting_payment == ugx(200_000)


async def test_a_saving_below_the_product_minimum_is_refused(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    with pytest.raises(ApplicationError) as error:
        await save(app_database, client, goal.id, ugx(99_999))
    assert error.value.code == "validation_failed"
    assert (await view(app_database, client, goal.id)).savings == []


async def test_a_retried_saving_is_one_investment(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    idempotency_key = key()
    first = await save(app_database, client, goal.id, ugx(200_000), idempotency_key=idempotency_key)
    again = await save(app_database, client, goal.id, ugx(200_000), idempotency_key=idempotency_key)
    assert first.id == again.id
    with pytest.raises(ApplicationError) as error:
        await save(app_database, client, goal.id, ugx(300_000), idempotency_key=idempotency_key)
    assert error.value.code == "idempotency_key_reused"
    async with app_database.actor_transaction(client) as session:
        assert len(await investments.client_investments(session, client, NOW)) == 1


async def test_one_key_for_two_goals_saves_to_each_once(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    school = await new_goal(app_database, client)
    land = await new_goal(app_database, client, name="Land")
    idempotency_key = key()
    first = await save(
        app_database, client, school.id, ugx(200_000), idempotency_key=idempotency_key
    )
    with pytest.raises(ApplicationError) as error:
        await save(app_database, client, land.id, ugx(200_000), idempotency_key=idempotency_key)
    # The client's key names one saving. Sent again for another goal it is a different request.
    assert error.value.code == "idempotency_key_reused"
    assert [s.id for s in (await view(app_database, client, school.id)).savings] == [first.id]


async def test_two_taps_at_once_save_once(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    idempotency_key = key()
    barrier = AsyncBarrier(2)

    async def tap() -> str:
        async with app_database.actor_transaction(client) as session:
            await barrier.wait()
            saving = await goals.save_to_goal(
                session,
                client,
                goal.id,
                idempotency_key=idempotency_key,
                amount=ugx(200_000),
                clock=NOW,
            )
            return saving.id

    first, second = await asyncio.gather(tap(), tap())
    assert first == second
    assert len((await view(app_database, client, goal.id)).savings) == 1


async def test_progress_is_the_value_of_the_goals_running_investments(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    other = await new_goal(app_database, client, name="Land")
    await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    await started_saving(app_database, worker_database, client, other.id, ugx(500_000))
    await save(app_database, client, goal.id, ugx(200_000))
    after = await view(app_database, client, goal.id)
    assert after.progress.value == ugx(1_000_000)
    assert after.progress.awaiting_payment == ugx(200_000)
    assert after.progress.percent == 50
    assert after.progress.standing is Standing.ON_TRACK
    # What is still needed is planned after what is running and what is awaiting payment.
    assert after.plan is not None
    assert after.plan.put_in + after.plan.interest >= ugx(800_000)
    assert (await view(app_database, client, other.id)).progress.value == ugx(500_000)


async def test_a_reached_goal_has_no_plan(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client, target=ugx(1_000_000))
    await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    after = await view(app_database, client, goal.id)
    assert after.progress.standing is Standing.REACHED
    assert after.progress.percent == 100
    assert after.plan is None


# Changing and closing ---------------------------------------------------------------------------


async def test_a_goal_can_be_changed_and_its_product_follows_the_new_date(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client, target=ugx(5_000_000), target_date=None)
    async with app_database.actor_transaction(client) as session:
        changed = await goals.update_goal(
            session,
            client,
            goal.id,
            name="  Plot   in Wakiso ",
            category=GoalCategory.LAND_OR_HOME,
            target=ugx(5_000_000),
            target_date=date.fromordinal(DAY.toordinal() + 182),
            clock=NOW,
        )
    assert changed.name == "Plot in Wakiso"
    assert changed.category is GoalCategory.LAND_OR_HOME
    assert changed.target_date is not None
    assert changed.plan is not None
    assert changed.plan.product_code == changed.product_code


async def test_closing_a_goal_leaves_its_investments_running(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    async with app_database.actor_transaction(client) as session:
        closed = await goals.close_goal(session, client, goal.id, NOW)
        again = await goals.close_goal(session, client, goal.id, NOW)
    assert closed.status is GoalStatus.CLOSED
    assert again.status is GoalStatus.CLOSED
    assert closed.plan is None
    assert closed.progress.value == ugx(1_000_000)
    async with app_database.actor_transaction(client) as session:
        running = await investments.client_investments(session, client, NOW)
    assert [i.status for i in running] == [InvestmentStatus.ACTIVE]


async def test_a_closed_goal_takes_no_saving_and_no_change(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal.id, NOW)
    with pytest.raises(ApplicationError) as error:
        await save(app_database, client, goal.id, ugx(200_000))
    assert error.value.code == "conflict"
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(client) as session:
            await goals.update_goal(
                session,
                client,
                goal.id,
                name="New",
                category=GoalCategory.OTHER,
                target=ugx(1),
                target_date=None,
                clock=NOW,
            )
    assert error.value.code == "conflict"
    async with app_database.actor_transaction(client) as session:
        assert len(await investments.client_investments(session, client, NOW)) == 0


async def test_a_saving_and_a_close_at_once_never_leave_a_saving_on_a_closed_goal(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    barrier = AsyncBarrier(2)

    async def close() -> str:
        async with app_database.actor_transaction(client) as session:
            await barrier.wait()
            await goals.close_goal(session, client, goal.id, NOW)
        return "closed"

    async def saving() -> str:
        try:
            async with app_database.actor_transaction(client) as session:
                await barrier.wait()
                await goals.save_to_goal(
                    session,
                    client,
                    goal.id,
                    idempotency_key=key(),
                    amount=ugx(200_000),
                    clock=NOW,
                )
            return "saved"
        except (ApplicationError, DBAPIError):
            return "refused"

    results = await asyncio.gather(close(), saving())
    after = await view(app_database, client, goal.id)
    assert after.status is GoalStatus.CLOSED
    async with app_database.actor_transaction(client) as session:
        made = await investments.client_investments(session, client, NOW)
    # Either the saving landed before the close, or nothing was made at all.
    assert len(after.savings) == len(made) == (1 if results[1] == "saved" else 0)


# Who sees what ----------------------------------------------------------------------------------


async def test_a_client_never_reaches_another_clients_goal(app_database: Database) -> None:
    owner = await approved_client(app_database, NOW)
    other = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, owner)
    for attempt in (
        view(app_database, other, goal.id),
        save(app_database, other, goal.id, ugx(200_000)),
    ):
        with pytest.raises(ApplicationError) as error:
            await attempt
        assert error.value.code == "not_found"
    async with app_database.actor_transaction(other) as session:
        assert await goals.client_goals(session, other, NOW) == []


async def test_staff_read_goals_and_their_savings_but_cannot_change_them(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        listed = await goals.list_goals(session, staff, client_id=client.id)
        detail = await goals.staff_goal(session, staff, goal.id, NOW)
    assert [g.id for g in listed] == [goal.id]
    assert detail.progress.value == ugx(1_000_000)
    assert [s.status for s in detail.savings] == [InvestmentStatus.ACTIVE]
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(staff) as session:
            result = await session.execute(
                text("UPDATE goals SET name = 'Taken' WHERE id = :id"), {"id": goal.id}
            )
            assert result.rowcount == 0  # type: ignore[attr-defined]
            await session.execute(
                text(
                    "INSERT INTO goals (id, client_id, name, category, currency, target_minor, "
                    "product_code, created_at, updated_at) VALUES ('-Nabc0000000000000Z9', "
                    ":client_id, 'X', 'other', 'UGX', 1, 'TREA-91', now(), now())"
                ),
                {"client_id": client.id},
            )


async def test_a_client_command_is_refused_for_staff_and_the_system(
    app_database: Database,
) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as error:
            await goals.client_goals(session, staff, NOW)
    assert error.value.code == "forbidden"
    with pytest.raises(ApplicationError) as error:
        await goals.list_goals(None, SYSTEM)  # type: ignore[arg-type]
    assert error.value.code == "forbidden"


# What the database enforces ---------------------------------------------------------------------


async def test_a_saving_is_never_changed_or_removed(
    app_database: Database, migrator_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    saving = await save(app_database, client, goal.id, ugx(200_000))
    for statement in (
        "UPDATE goal_savings SET created_at = now() WHERE investment_id = :id",
        "DELETE FROM goal_savings WHERE investment_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with migrator_database.transaction() as session:
                await session.execute(text(statement), {"id": saving.id})


async def test_a_closed_goal_never_changes_and_never_takes_a_saving(
    app_database: Database, migrator_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal.id, NOW)
    with pytest.raises(DBAPIError):
        async with migrator_database.transaction() as session:
            await session.execute(
                text("UPDATE goals SET status = 'active', closed_at = NULL WHERE id = :id"),
                {"id": goal.id},
            )
    with pytest.raises(DBAPIError):
        async with migrator_database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at) "
                    "VALUES ('-Nabc0000000000000Z8', :goal_id, :client_id, now())"
                ),
                {"goal_id": goal.id, "client_id": client.id},
            )


async def test_a_saving_must_name_its_goals_own_client(
    app_database: Database, migrator_database: Database
) -> None:
    owner = await approved_client(app_database, NOW)
    other = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, owner)
    with pytest.raises(DBAPIError):
        async with migrator_database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at) "
                    "VALUES ('-Nabc0000000000000Z7', :goal_id, :client_id, now())"
                ),
                {"goal_id": goal.id, "client_id": other.id},
            )


async def test_nobody_deletes_a_goal(application_database: Database) -> None:
    with pytest.raises(DBAPIError):
        async with application_database.actor_transaction(SYSTEM) as session:
            await session.execute(text("DELETE FROM goals"))


async def test_a_goal_keeps_its_client_and_currency(
    app_database: Database, migrator_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    for change in ("client_id = '-Nabc0000000000000Z6'", "currency = 'USD'"):
        with pytest.raises(DBAPIError):
            async with migrator_database.transaction() as session:
                await session.execute(
                    text(f"UPDATE goals SET {change} WHERE id = :id"),  # noqa: S608
                    {"id": goal.id},
                )


# Rollovers --------------------------------------------------------------------------------------


async def mature(worker_database: Database, investment_id: str) -> investments.Investment:
    """Mature an investment on its maturity day, which rolls it over."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, investment_id)
    assert investment.maturity_date is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.mature_investment(
            session, investment_id, investment.maturity_date, at(investment.maturity_date)
        )


async def linked(database: Database, goal_id: str) -> list[tuple[str, str | None]]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT investment_id, rolled_from_investment_id FROM goal_savings "
                "WHERE goal_id = :goal_id ORDER BY created_at, investment_id"
            ),
            {"goal_id": goal_id},
        )
        return [(row.investment_id, row.rolled_from_investment_id) for row in rows]


async def test_a_saving_that_rolls_over_stays_in_its_goal(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    first = await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))

    old = await mature(worker_database, first)
    assert old.rolled_into_id is not None
    assert old.maturity_date is not None
    # The rollover's job is due when the old investment matured.
    runner = Worker(
        worker_database,
        GoalJobs(worker_database, at(old.maturity_date)).handlers(),
        WorkerConfig(),
        at(old.maturity_date, hour=10),
    )
    while await runner.run_once():
        pass
    async with worker_database.actor_transaction(SYSTEM) as session:
        status = await session.scalar(
            text(
                "SELECT status FROM jobs WHERE kind = :kind "
                "AND payload -> 'data' ->> 'investment_id' = :id"
            ),
            {"kind": FOLLOW_ROLLOVER_KIND, "id": first},
        )
    assert status == "succeeded"

    assert await linked(worker_database, goal.id) == [(first, None), (old.rolled_into_id, first)]
    async with worker_database.actor_transaction(SYSTEM) as session:
        new = await investments.get_investment(session, old.rolled_into_id)
    seen = await view(app_database, client, goal.id)
    assert {s.id for s in seen.savings} == {first, new.id}
    # Only the running investment counts, so the goal holds the proceeds once.
    assert seen.progress.value == new.amount
    assert seen.progress.value > ugx(1_000_000)


async def test_following_a_rollover_twice_links_it_once(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    first = await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    old = await mature(worker_database, first)
    assert old.rolled_into_id is not None
    for _ in range(2):
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert await goals.follow_rollover(
                session, investment_id=first, new_investment_id=old.rolled_into_id, clock=NOW
            )
    assert await linked(worker_database, goal.id) == [(first, None), (old.rolled_into_id, first)]


async def test_a_closed_goal_and_an_investment_made_outside_goals_are_not_followed(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    first = await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal.id, NOW)
    old = await mature(worker_database, first)
    assert old.rolled_into_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert not await goals.follow_rollover(
            session, investment_id=first, new_investment_id=old.rolled_into_id, clock=NOW
        )
        assert not await goals.follow_rollover(
            session,
            investment_id=push_id(),
            new_investment_id=push_id(),
            clock=NOW,
        )
    assert await linked(worker_database, goal.id) == [(first, None)]


async def test_only_the_worker_records_a_rollover_and_only_from_the_same_goal(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    other = await new_goal(app_database, client, name="Land")
    first = await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    insert = text(
        "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at, "
        "rolled_from_investment_id) VALUES (:id, :goal_id, :client_id, now(), :rolled_from)"
    )
    values = {"goal_id": goal.id, "client_id": client.id, "rolled_from": first}
    # The client cannot pass a saving off as a rollover.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client) as session:
            await session.execute(insert, {**values, "id": push_id()})
    # The worker cannot record a saving that is not a rollover.
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(insert, {**values, "id": push_id(), "rolled_from": None})
    # Nor a rollover into another goal than the saving's own.
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(insert, {**values, "id": push_id(), "goal_id": other.id})
    # Nor a second rollover from the same saving.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await session.execute(insert, {**values, "id": push_id()})
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(insert, {**values, "id": push_id()})


async def test_a_goal_closed_after_the_rollover_is_read_is_not_followed(
    app_database: Database, worker_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client)
    first = await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    old = await mature(worker_database, first)
    assert old.rolled_into_id is not None
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal.id, NOW)
    # As if the goal was read while still open, and closed before the link was written.
    monkeypatch.setattr(
        goals,
        "_SAVED_FROM",
        text(
            "SELECT goal_id, client_id, 'active' AS status FROM goal_savings "
            "WHERE investment_id = :investment_id"
        ),
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert not await goals.follow_rollover(
            session, investment_id=first, new_investment_id=old.rolled_into_id, clock=NOW
        )
        # The job's transaction is still usable, so it completes cleanly.
        assert await session.scalar(text("SELECT 1")) == 1
    assert await linked(worker_database, goal.id) == [(first, None)]


# Reminders --------------------------------------------------------------------------------------


async def told(database: Database, client: Actor) -> list[tuple[str, str, str | None, str]]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT event, idempotency_key, link_kind, body FROM notifications "
                "WHERE client_id = :client_id AND event LIKE 'goal_%' ORDER BY event, created_at"
            ),
            {"client_id": client.id},
        )
        return [(row.event, row.idempotency_key, row.link_kind, row.body) for row in rows]


async def remind(database: Database, day: date) -> dict[str, int]:
    async with database.actor_transaction(SYSTEM) as session:
        return await reminders.send_reminders(session, at(day, hour=5))


async def test_a_reached_goal_is_told_once(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client, name="New roof", target=ugx(1_000_000))
    await started_saving(app_database, worker_database, client, goal.id, ugx(1_000_000))
    await remind(worker_database, DAY)
    await remind(worker_database, DAY + timedelta(days=1))
    seen = await told(worker_database, client)
    assert [(event, k, link) for event, k, link, _ in seen] == [
        ("goal_reached", f"goal-reached:{goal.id}", "goal")
    ]
    assert "New roof" in seen[0][3]
    assert "UGX" not in seen[0][3]


async def test_a_saving_due_in_three_days_is_reminded_once(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    goal = await new_goal(app_database, client, name="School fees 2027")
    due = next_due(DAY, DAY)
    # Too early: more than three days before.
    await remind(worker_database, due - timedelta(days=4))
    assert await told(worker_database, client) == []
    await remind(worker_database, due - timedelta(days=3))
    await remind(worker_database, due - timedelta(days=2))
    seen = await told(worker_database, client)
    assert [(event, k) for event, k, _, _ in seen] == [
        ("goal_top_up_due", f"goal-top-up:{goal.id}:{due.isoformat()}")
    ]
    assert "School fees 2027" in seen[0][3]
    assert "UGX" not in seen[0][3]


async def test_goals_with_no_date_or_closed_are_not_reminded(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    await new_goal(app_database, client, name="Emergency", target_date=None)
    closed = await new_goal(app_database, client, name="Car")
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, closed.id, clock=NOW)
    await remind(worker_database, next_due(DAY, DAY) - timedelta(days=2))
    assert await told(worker_database, client) == []
