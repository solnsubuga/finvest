"""Saving the interest to a goal at maturity, against real PostgreSQL: the principal rolls into
a new term that keeps the choice, the interest starts a saving in the goal's product in the
same entry, below the product's minimum (the owner's decision), and goals records it. A goal
that cannot take it leaves the interest with the principal."""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.goals import service as goals
from app.modules.goals.domain.goals import GoalCategory
from app.modules.goals.domain.plans import add_months
from app.modules.investments import accrual
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.investments.holdings import HOLDING
from app.modules.ledger import service as ledger
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.investments import (
    PRESENT,
    account_balance,
    approved_client,
    at,
    pay,
    request,
    today,
)
from tests.support.ledger import key, ugx

pytestmark = pytest.mark.integration

DAY = today()
MATURED, ACTIVE = InvestmentStatus.MATURED, InvestmentStatus.ACTIVE
PRINCIPAL = 1_000_000
# UGX 1,000,000 for 91 days, after tax. Below the 91 day bill's minimum of UGX 100,000.
INTEREST = 26_490


def day(offset: int) -> date:
    return DAY + timedelta(days=offset)


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> list[tuple[str, str]]:
    return [(str(problem.field_path[0]), problem.code) for problem in error.value.fields]


async def running(
    app_database: Database, worker_database: Database, client: Actor
) -> investments.Investment:
    investment = await request(app_database, client, PRESENT)
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment.id, confirmed_on=DAY, clock=PRESENT
        )


async def new_goal(app_database: Database, client: Actor, name: str = "School fees") -> str:
    async with app_database.actor_transaction(client) as session:
        goal = await goals.create_goal(
            session,
            client,
            idempotency_key=key(),
            name=name,
            category=GoalCategory.SCHOOL_FEES,
            target=ugx(5_000_000),
            target_date=add_months(DAY, 12),
            clock=PRESENT,
        )
    return goal.id


async def choose(
    app_database: Database, client: Actor, investment_id: str, goal_id: str | None
) -> investments.Investment:
    async with app_database.actor_transaction(client) as session:
        return await investments.choose_at_maturity(
            session,
            client,
            investment_id,
            at_maturity=AtMaturity.INTEREST_TO_GOAL,
            destination_id=None,
            goal_id=goal_id,
            clock=PRESENT,
        )


async def mature(worker_database: Database, investment_id: str, offset: int = 91) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.mature_investment(session, investment_id, day(offset), at(day(offset)))


async def reload(worker_database: Database, investment_id: str) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.get_investment(session, investment_id)


async def interest_saving(worker_database: Database, matured_id: str) -> str | None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        found = await session.scalar(
            text(
                "SELECT id FROM investments WHERE source_type = 'goal_interest' AND source_id = :id"
            ),
            {"id": matured_id},
        )
    return None if found is None else str(found)


async def link(worker_database: Database, matured: investments.Investment, saving_id: str) -> bool:
    """What the goals worker does with `investments.interest_saved_to_goal`."""
    assert matured.interest_goal_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await goals.link_interest_saving(
            session,
            investment_id=matured.id,
            saving_investment_id=saving_id,
            goal_id=matured.interest_goal_id,
            client_id=matured.client_id,
            clock=at(day(91)),
        )


async def notices(worker_database: Database, client_id: str, event: str) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM notifications WHERE client_id = :c AND event = :e"),
                {"c": client_id, "e": event},
            )
            or 0
        )


async def published(worker_database: Database, matured_id: str) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM jobs WHERE kind = 'goals.link_interest_saving' "
                    "AND payload -> 'data' ->> 'investment_id' = :id"
                ),
                {"id": matured_id},
            )
            or 0
        )


# The choice -------------------------------------------------------------------------------------


async def test_the_interest_starts_a_saving_in_the_goal_and_the_principal_another_term(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    chosen = await choose(app_database, client, investment.id, goal_id)
    assert (chosen.at_maturity, chosen.interest_goal_id) == (AtMaturity.INTEREST_TO_GOAL, goal_id)

    await mature(worker_database, investment.id)

    old = await reload(worker_database, investment.id)
    assert (old.status, old.accrued_net, old.payout_id, old.payout_refusal) == (
        MATURED,
        ugx(INTEREST),
        None,
        None,
    )
    assert old.rolled_into_id is not None and old.waiting is None
    new = await reload(worker_database, old.rolled_into_id)
    assert (new.status, new.amount, new.start_date, new.maturity_date) == (
        ACTIVE,
        ugx(PRINCIPAL),
        day(91),
        day(182),
    )
    # The new term keeps the choice and the goal, so every term's interest goes there.
    assert (new.at_maturity, new.interest_goal_id) == (AtMaturity.INTEREST_TO_GOAL, goal_id)

    saving_id = await interest_saving(worker_database, old.id)
    assert saving_id is not None
    saving = await reload(worker_database, saving_id)
    async with app_database.actor_transaction(client) as session:
        goal = await goals.client_goal(session, client, goal_id, at(day(91)))
    # Below the product's minimum: the owner chose to waive it for interest.
    assert (saving.status, saving.amount, saving.product_code) == (
        ACTIVE,
        ugx(INTEREST),
        goal.product_code,
    )
    assert (saving.source_type, saving.source_id, saving.at_maturity) == (
        "goal_interest",
        old.id,
        AtMaturity.ROLL_OVER,
    )
    assert saving.start_date == day(91)
    assert await account_balance(worker_database, old, HOLDING) == 0
    assert await account_balance(worker_database, new, HOLDING) == PRINCIPAL
    assert await account_balance(worker_database, saving, HOLDING) == INTEREST
    async with worker_database.actor_transaction(SYSTEM) as session:
        owed = await ledger.open_account(session, accrual.interest_account(old), FakeClock())
        assert (await ledger.get_balance(session, owed.id)).minor == 0
    assert await published(worker_database, old.id) == 1

    # Goals records it as a saving, once, and tells the client once.
    assert await link(worker_database, old, saving_id) is True
    assert await link(worker_database, old, saving_id) is True
    assert await notices(worker_database, client.id or "", "goal_interest_saved") == 1
    async with app_database.actor_transaction(client) as session:
        goal = await goals.client_goal(session, client, goal_id, at(day(92)))
    [counted] = [s for s in goal.savings if s.id == saving_id]
    assert counted.interest_from_id == old.id
    assert goal.interest_from_products == {saving_id: old.product_name}
    assert goal.progress.value.minor >= INTEREST
    # The new term will save its interest here too.
    assert [i.id for i in goal.interest_coming] == [new.id]


async def test_the_goal_is_shown_on_the_investment_that_saves_to_it(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    await choose(app_database, client, investment.id, goal_id)
    async with app_database.actor_transaction(client) as session:
        view = await investments.client_investment(session, client, investment.id, PRESENT)
        goal = await goals.client_goal(session, client, goal_id, PRESENT)
    assert (view.at_maturity, view.interest_goal_id) == (AtMaturity.INTEREST_TO_GOAL, goal_id)
    assert [i.id for i in goal.interest_coming] == [investment.id]

    # Changing the choice back clears the goal.
    async with app_database.actor_transaction(client) as session:
        back = await investments.choose_at_maturity(
            session,
            client,
            investment.id,
            at_maturity=AtMaturity.ROLL_OVER,
            destination_id=None,
            clock=PRESENT,
        )
        goal = await goals.client_goal(session, client, goal_id, PRESENT)
    assert (back.at_maturity, back.interest_goal_id) == (AtMaturity.ROLL_OVER, None)
    assert goal.interest_coming == ()


async def test_a_locked_goal_locks_the_new_saving(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    async with app_database.actor_transaction(client) as session:
        locked = await goals.lock_goal(session, client, goal_id, PRESENT)
    await choose(app_database, client, investment.id, goal_id)

    await mature(worker_database, investment.id)

    saving_id = await interest_saving(worker_database, investment.id)
    assert saving_id is not None
    assert (await reload(worker_database, saving_id)).withdrawal_locked_until == locked.locked_until


async def test_a_closed_goal_leaves_the_interest_with_the_principal(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    await choose(app_database, client, investment.id, goal_id)
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal_id, PRESENT)

    await mature(worker_database, investment.id)

    old = await reload(worker_database, investment.id)
    assert old.rolled_into_id is not None
    new = await reload(worker_database, old.rolled_into_id)
    # All of it rolled over, and a closed goal ends the choice.
    assert (new.amount, new.at_maturity, new.interest_goal_id) == (
        ugx(PRINCIPAL + INTEREST),
        AtMaturity.ROLL_OVER,
        None,
    )
    assert await interest_saving(worker_database, old.id) is None
    assert await published(worker_database, old.id) == 0
    assert await notices(worker_database, client.id or "", "goal_interest_not_saved") == 1


async def test_a_goal_closed_after_maturity_takes_no_saving(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    await choose(app_database, client, investment.id, goal_id)
    await mature(worker_database, investment.id)
    old = await reload(worker_database, investment.id)
    saving_id = await interest_saving(worker_database, old.id)
    assert saving_id is not None
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal_id, at(day(91)))

    # The saving carries on as the client's own investment, as a closed goal's savings do.
    assert await link(worker_database, old, saving_id) is False
    assert (await reload(worker_database, saving_id)).status is ACTIVE


# Refusals ---------------------------------------------------------------------------------------


async def test_the_goal_must_be_an_open_one_of_the_clients_own(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    other = await approved_client(app_database, PRESENT)
    theirs = await new_goal(app_database, other)
    closed = await new_goal(app_database, client, "Old goal")
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, closed, PRESENT)

    for goal_id, code in (
        (None, "required"),
        (theirs, "not_usable"),
        (push_id(), "not_usable"),
        (closed, "goal_closed"),
    ):
        with pytest.raises(ApplicationError) as error:
            await choose(app_database, client, investment.id, goal_id)
        assert refused(error) == [("goal_id", code)], goal_id

    with pytest.raises(ApplicationError) as rolling:
        async with app_database.actor_transaction(client) as session:
            await investments.choose_at_maturity(
                session,
                client,
                investment.id,
                at_maturity=AtMaturity.ROLL_OVER,
                destination_id=None,
                goal_id=await new_goal(app_database, client, "Another"),
                clock=PRESENT,
            )
    assert refused(rolling) == [("goal_id", "not_used")]


async def test_a_saving_does_not_save_its_interest_to_its_own_goal(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    goal_id = await new_goal(app_database, client)
    async with app_database.actor_transaction(client) as session:
        saving = await goals.save_to_goal(
            session, client, goal_id, idempotency_key=key(), amount=ugx(1_000_000), clock=PRESENT
        )
    with pytest.raises(ApplicationError) as error:
        await choose(app_database, client, saving.id, goal_id)
    assert refused(error) == [("goal_id", "already_saved_to_goal")]


async def test_the_goal_is_chosen_on_a_running_investment_not_a_new_one(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, PRESENT)
    with pytest.raises(ApplicationError) as error:
        await request(app_database, client, PRESENT, at_maturity=AtMaturity.INTEREST_TO_GOAL)
    assert refused(error) == [("at_maturity", "chosen_later")]


async def test_the_database_refuses_a_goal_of_another_client(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    other = await approved_client(app_database, PRESENT)
    theirs = await new_goal(app_database, other)
    with pytest.raises(DBAPIError, match="fk_investments_interest_goal"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "UPDATE investments SET at_maturity = 'interest_to_goal', "
                    "interest_goal_id = :goal WHERE id = :id"
                ),
                {"goal": theirs, "id": investment.id},
            )


async def test_only_maturity_saves_interest_and_only_exactly_the_interest(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    await choose(app_database, client, investment.id, goal_id)
    insert = text(
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
        "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at, "
        "source_type, source_id) SELECT :id, :client, p.id, 'UGX', :amount, r.annual_rate_bps, "
        "r.tax_bps, 91, 'roll_over', now(), now() + interval '72 hours', now(), "
        "'goal_interest', :source FROM investment_products p JOIN investment_rates r "
        "ON r.product_id = p.id WHERE p.code = 'TREA-91' ORDER BY r.effective_from DESC LIMIT 1"
    )
    values = {"client": client.id, "source": investment.id}
    # The client never makes one, and the worker not before the term has earned its last day.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client) as session:
            await session.execute(insert, {**values, "id": push_id(), "amount": 1})
    with pytest.raises(DBAPIError, match="only maturity saves"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(insert, {**values, "id": push_id(), "amount": 1})


# Concurrency ------------------------------------------------------------------------------------


async def test_two_runs_maturing_at_once_save_the_interest_once(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    await choose(app_database, client, investment.id, goal_id)

    async def run() -> None:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await investments.mature_investment(session, investment.id, day(91), at(day(91)))

    await asyncio.gather(run(), run())

    async with worker_database.actor_transaction(SYSTEM) as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM investments WHERE source_type = 'goal_interest' "
                "AND source_id = :id"
            ),
            {"id": investment.id},
        )
    assert count == 1
    saving_id = await interest_saving(worker_database, investment.id)
    assert saving_id is not None
    assert (
        await account_balance(worker_database, await reload(worker_database, saving_id), HOLDING)
        == INTEREST
    )


async def test_closing_the_goal_while_it_is_linked_never_leaves_half_a_saving(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await running(app_database, worker_database, client)
    goal_id = await new_goal(app_database, client)
    await choose(app_database, client, investment.id, goal_id)
    await mature(worker_database, investment.id)
    old = await reload(worker_database, investment.id)
    saving_id = await interest_saving(worker_database, old.id)
    assert saving_id is not None

    async def close() -> None:
        async with app_database.actor_transaction(client) as session:
            await goals.close_goal(session, client, goal_id, at(day(91)))

    linked, _ = await asyncio.gather(link(worker_database, old, saving_id), close())

    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.scalar(
            text("SELECT count(*) FROM goal_savings WHERE investment_id = :id"), {"id": saving_id}
        )
    # Either it was linked before the goal closed, or not at all.
    assert rows == (1 if linked else 0)
