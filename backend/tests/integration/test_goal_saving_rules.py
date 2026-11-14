"""Goal saving rules against real PostgreSQL: a monthly automatic plan that saves towards a goal.

The rule is an automatic investing plan in the goal's product. Each run's investment is linked to
the goal as a saving when autoinvest publishes `autoinvest.run_requested`.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.autoinvest import requests
from app.modules.autoinvest import runs as autoinvest_runs
from app.modules.autoinvest import service as autoinvest
from app.modules.goals import reminders, saving_rules
from app.modules.goals import service as goals
from app.modules.goals.domain.goals import GoalCategory
from app.modules.goals.domain.plans import add_months
from app.modules.goals.domain.reminders import next_due
from app.modules.identity import service as identity_service
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.payments.domain.deposits import Network
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.investments import PRESENT, approved_client, at, today
from tests.support.ledger import key, ugx
from tests.support.payments import phone

pytestmark = pytest.mark.integration

AMOUNT = ugx(150_000)


class Contacts:
    def __init__(self) -> None:
        self.phones: dict[str, str] = {}

    async def __call__(
        self, session: AsyncSession, client_id: str
    ) -> identity_service.ClientContact | None:
        found = self.phones.get(client_id)
        return None if found is None else identity_service.ClientContact(found, None, True)


@pytest.fixture
def contacts(monkeypatch: pytest.MonkeyPatch) -> Iterator[Contacts]:
    fake = Contacts()
    monkeypatch.setattr(identity_service, "client_contact", fake)
    # A run at the real time, whatever the hour the tests run at.
    monkeypatch.setattr(requests, "may_prompt_at", lambda moment: True)
    monkeypatch.setattr(autoinvest_runs, "may_prompt_at", lambda moment: True)
    yield fake


async def goal_for(database: Database, contacts: Contacts) -> tuple[Actor, goals.ClientGoal, str]:
    client = await approved_client(database, PRESENT)
    assert client.id is not None
    number = phone()
    contacts.phones[client.id] = number
    async with database.actor_transaction(client) as session:
        goal = await goals.create_goal(
            session,
            client,
            idempotency_key=key(),
            name="Land in Mukono",
            category=GoalCategory.SCHOOL_FEES,
            target=ugx(5_000_000),
            target_date=add_months(today(), 12),
            clock=PRESENT,
        )
    return client, goal, number


async def set_up(
    database: Database,
    client: Actor,
    goal_id: str,
    number: str,
    *,
    day: int | None = None,
    clock: FakeClock = PRESENT,
    idempotency_key: str | None = None,
) -> saving_rules.ClientSavingRule:
    async with database.actor_transaction(client) as session:
        return await saving_rules.set_up_rule(
            session,
            client,
            goal_id,
            amount=AMOUNT,
            day=day or min(today().day, 28),
            network=Network.MTN,
            idempotency_key=idempotency_key or key(),
            account_phone=number,
            clock=clock,
        )


async def view(database: Database, client: Actor, goal_id: str) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.client_goal(session, client, goal_id, PRESENT)


# Setting a rule up ------------------------------------------------------------------------------


async def test_a_rule_is_a_monthly_plan_in_the_goals_product_that_rolls_over(
    app_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    idempotency_key = key()
    rule = await set_up(
        app_database, client, goal.id, number, day=5, idempotency_key=idempotency_key
    )
    assert (rule.amount, rule.day, rule.status) == (AMOUNT, 5, autoinvest.PlanStatus.ACTIVE)
    assert rule.next_run_on is not None and rule.next_run_on > today()
    assert rule.next_run_on.day == 5
    async with app_database.actor_transaction(client) as session:
        plan = await autoinvest.client_plan(session, client, rule.plan_id)
    assert plan.product_code == goal.product_code
    assert plan.schedule == autoinvest.Schedule(autoinvest.Frequency.MONTHLY, 5)
    assert plan.at_maturity is AtMaturity.ROLL_OVER
    assert (await view(app_database, client, goal.id)).saving_rule == rule

    # The same key gives the same rule. A second rule for the goal is refused.
    again = await set_up(
        app_database, client, goal.id, number, day=5, idempotency_key=idempotency_key
    )
    assert again.plan_id == rule.plan_id
    with pytest.raises(ApplicationError) as refused:
        await set_up(app_database, client, goal.id, number)
    assert refused.value.code == "conflict"


async def test_another_clients_goal_has_no_rule_for_them(
    app_database: Database, contacts: Contacts
) -> None:
    _, goal, number = await goal_for(app_database, contacts)
    other = await approved_client(app_database, PRESENT)
    with pytest.raises(ApplicationError) as refused:
        await set_up(app_database, other, goal.id, number)
    assert refused.value.code == "not_found"


# Ending it --------------------------------------------------------------------------------------


async def test_ending_a_rule_stops_its_plan_and_a_new_one_can_follow(
    app_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    rule = await set_up(app_database, client, goal.id, number)
    async with app_database.actor_transaction(client) as session:
        await saving_rules.end_rule(session, client, goal.id, PRESENT)
        await saving_rules.end_rule(session, client, goal.id, PRESENT)
        plan = await autoinvest.client_plan(session, client, rule.plan_id)
    assert plan.status is autoinvest.PlanStatus.STOPPED
    assert (await view(app_database, client, goal.id)).saving_rule is None
    second = await set_up(app_database, client, goal.id, number)
    assert second.plan_id != rule.plan_id


async def test_closing_a_goal_ends_its_rule(app_database: Database, contacts: Contacts) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    rule = await set_up(app_database, client, goal.id, number)
    async with app_database.actor_transaction(client) as session:
        await goals.close_goal(session, client, goal.id, PRESENT)
        plan = await autoinvest.client_plan(session, client, rule.plan_id)
    assert plan.status is autoinvest.PlanStatus.STOPPED
    with pytest.raises(ApplicationError) as refused:
        await set_up(app_database, client, goal.id, number)
    assert refused.value.code == "conflict"


async def test_a_plan_stopped_from_automatic_investing_leaves_the_goal_free_for_a_new_rule(
    app_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    rule = await set_up(app_database, client, goal.id, number)
    async with app_database.actor_transaction(client) as session:
        await autoinvest.stop_plan(session, client, rule.plan_id, PRESENT)
    assert (await view(app_database, client, goal.id)).saving_rule is None
    assert (await set_up(app_database, client, goal.id, number)).plan_id != rule.plan_id


# A run saves towards the goal -------------------------------------------------------------------


async def test_a_runs_investment_is_a_saving_towards_the_goal_and_is_linked_once(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    # Set up yesterday for today's day of the month, so its first run is due now.
    yesterday = FakeClock(datetime.now(UTC) - timedelta(days=1))
    if today().day > 28:
        pytest.skip("A monthly day after the 28th falls on the month's end, not tomorrow")
    rule = await set_up(app_database, client, goal.id, number, day=today().day, clock=yesterday)
    assert rule.next_run_on == today()

    clock = FakeClock(datetime.now(UTC))
    async with worker_database.actor_transaction(SYSTEM) as session:
        opened = await autoinvest.open_due_runs(session, clock)
        [run] = await autoinvest.plan_runs(session, rule.plan_id)
        assert run.id in opened.due
        assert await requests.request_run(session, run.id, provider="fake", clock=clock) == (
            "requested"
        )
        [asked] = await autoinvest.plan_runs(session, rule.plan_id)
        event = await session.scalar(
            text(
                "SELECT count(*) FROM outbox_events WHERE topic = 'autoinvest.run_requested' "
                "AND idempotency_key = :key"
            ),
            {"key": run.id},
        )
    assert event == 1
    assert asked.investment_id is not None

    for _ in range(2):
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert await saving_rules.link_plan_run(
                session,
                plan_id=rule.plan_id,
                client_id=str(client.id),
                investment_id=asked.investment_id,
                clock=clock,
            )
    # A run said to be another client's links nothing.
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert not await saving_rules.link_plan_run(
            session,
            plan_id=rule.plan_id,
            client_id=push_id(),
            investment_id=push_id(),
            clock=clock,
        )
    seen = await view(app_database, client, goal.id)
    assert [(s.id, s.status) for s in seen.savings] == [
        (asked.investment_id, InvestmentStatus.AWAITING_PAYMENT)
    ]
    assert seen.progress.awaiting_payment == AMOUNT

    # A goal saved by its rule gets no reminder to pay by hand.
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await saving_rules.goals_with_rules(session, [goal.id]) == {goal.id: rule.plan_id}


async def test_a_plan_that_is_no_goals_rule_links_nothing(worker_database: Database) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert not await saving_rules.link_plan_run(
            session, plan_id=push_id(), client_id=push_id(), investment_id=push_id(), clock=PRESENT
        )


# Reminders --------------------------------------------------------------------------------------


async def test_a_goal_saved_by_its_rule_is_not_reminded_until_its_plan_is_paused(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    rule = await set_up(app_database, client, goal.id, number)
    due = next_due(today(), today())
    await remind(worker_database, due - timedelta(days=2))
    assert await top_ups(worker_database, client) == []
    async with app_database.actor_transaction(client) as session:
        await autoinvest.pause_plan(session, client, rule.plan_id, PRESENT)
    await remind(worker_database, due - timedelta(days=1))
    assert await top_ups(worker_database, client) == [f"goal-top-up:{goal.id}:{due.isoformat()}"]


async def test_a_goal_whose_plan_was_stopped_elsewhere_is_reminded_again(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    rule = await set_up(app_database, client, goal.id, number)
    async with app_database.actor_transaction(client) as session:
        await autoinvest.stop_plan(session, client, rule.plan_id, PRESENT)
    due = next_due(today(), today())
    await remind(worker_database, due - timedelta(days=2))
    assert await top_ups(worker_database, client) == [f"goal-top-up:{goal.id}:{due.isoformat()}"]


async def remind(database: Database, day: date) -> None:
    async with database.actor_transaction(SYSTEM) as session:
        await reminders.send_reminders(session, at(day, hour=5))


async def top_ups(database: Database, client: Actor) -> list[str]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT idempotency_key FROM notifications WHERE client_id = :client_id "
                "AND event = 'goal_top_up_due' ORDER BY created_at"
            ),
            {"client_id": client.id},
        )
    return [row.idempotency_key for row in rows]


# The fences -------------------------------------------------------------------------------------


async def test_only_the_worker_records_a_rules_saving_and_only_for_that_rules_goal(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    _, other_goal, _ = await goal_for(app_database, contacts)
    rule = await set_up(app_database, client, goal.id, number)
    async with app_database.actor_transaction(SYSTEM) as session:
        found = (
            await session.execute(
                text("SELECT id FROM goal_saving_rules WHERE plan_id = :plan_id"),
                {"plan_id": rule.plan_id},
            )
        ).scalar_one_or_none()
    assert found is None  # the API never trusts the system actor
    async with worker_database.actor_transaction(SYSTEM) as session:
        rule_id = (
            await session.execute(
                text("SELECT id FROM goal_saving_rules WHERE plan_id = :plan_id"),
                {"plan_id": rule.plan_id},
            )
        ).scalar_one()
    insert = text(
        "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at, rule_id) "
        "VALUES (:investment_id, :goal_id, :client_id, now(), :rule_id)"
    )
    # The client cannot name a rule on their own saving.
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(
                insert,
                {
                    "investment_id": push_id(),
                    "goal_id": goal.id,
                    "client_id": client.id,
                    "rule_id": rule_id,
                },
            )
    # The worker cannot put a rule's saving on another goal.
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(DBAPIError, match="foreign key"):
            await session.execute(
                insert,
                {
                    "investment_id": push_id(),
                    "goal_id": other_goal.id,
                    "client_id": client.id,
                    "rule_id": rule_id,
                },
            )


async def test_a_rule_only_ends_once_and_only_by_its_client(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    client, goal, number = await goal_for(app_database, contacts)
    await set_up(app_database, client, goal.id, number)
    other = await approved_client(app_database, PRESENT)
    end = text("UPDATE goal_saving_rules SET ended_at = now() WHERE goal_id = :goal_id")
    async with app_database.actor_transaction(other) as session:
        result = await session.execute(end, {"goal_id": goal.id})
        assert result.rowcount == 0  # type: ignore[attr-defined]
    async with app_database.actor_transaction(client) as session:
        await session.execute(end, {"goal_id": goal.id})
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(DBAPIError, match="only ends, once"):
            await session.execute(end, {"goal_id": goal.id})
        with pytest.raises(DBAPIError):
            await session.execute(
                text("DELETE FROM goal_saving_rules WHERE goal_id = :goal_id"),
                {"goal_id": goal.id},
            )
