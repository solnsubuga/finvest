"""A client saves towards a goal, and sets up a plan that invests on a schedule."""

from datetime import UTC, datetime, time, timedelta

import pytest

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.autoinvest import plans
from app.modules.autoinvest.domain.plans import Frequency, RunStatus, Schedule
from app.modules.autoinvest.jobs import OPEN_DUE_KIND
from app.modules.investments.holdings import HOLDING
from app.modules.payments.domain.deposits import Network
from app.shared.actors import SYSTEM
from app.shared.business_day import KAMPALA
from tests.journeys.money import (
    balance,
    idempotency,
    investment,
    is_active,
    pay_by_mobile_money,
    today,
    ugx,
)
from tests.journeys.onboarding import onboarded
from tests.support.books import audit_for, missing
from tests.support.journey import World

pytestmark = pytest.mark.journey


async def test_a_saving_towards_a_goal_is_an_investment_that_counts_towards_it(
    world: World,
) -> None:
    client = await onboarded(world)
    target_date = today(world) + timedelta(days=365)
    plan = await client.http.get(
        "/client/goals/plan",
        params={"target_minor": 5_000_000, "target_date": target_date.isoformat()},
    )
    assert plan.status_code == 200, plan.text
    created = await client.http.post(
        "/client/goals",
        json={
            "name": "School fees for 2028",
            "category": "school_fees",
            "target_minor": 5_000_000,
            "target_date": target_date.isoformat(),
        },
        headers=idempotency(),
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["goal_id"]

    saving = await client.http.post(
        f"/client/goals/{goal_id}/savings", json={"amount_minor": 500_000}, headers=idempotency()
    )
    assert saving.status_code == 201, saving.text
    investment_id = saving.json()["investment_id"]
    waiting = (await client.http.get(f"/client/goals/{goal_id}")).json()
    assert waiting["progress"]["awaiting_payment_minor"] == 500_000

    payment = await pay_by_mobile_money(world, client, investment_id)

    assert payment["status"] == "confirmed", payment
    assert is_active(await investment(world, investment_id))
    assert await balance(world, HOLDING, owner=client.id, reference=investment_id) == 500_000
    goal = (await client.http.get(f"/client/goals/{goal_id}")).json()
    assert goal["progress"]["value_minor"] == 500_000
    assert goal["progress"]["awaiting_payment_minor"] == 0
    assert goal["progress"]["percent"] == 10
    assert [item["investment_id"] for item in goal["savings"]] == [investment_id]
    # Staff see the same goal and saving.
    staff = await world.staff("operations")
    seen = await staff.http.get(f"/staff/goals/{goal_id}")
    assert seen.status_code == 200, seen.text


async def test_an_automatic_investing_plan_opens_its_run_on_its_day(
    world: World, migrator_database: Database
) -> None:
    client = await onboarded(world)
    assert client.phone is not None
    async with world.api_database.actor_transaction(client.actor) as session:
        plan = await plans.create_plan(
            session,
            client.actor,
            plans.PlanTerms(
                product_code="TREA-91",
                amount=ugx(200_000),
                schedule=Schedule(Frequency.WEEKLY, today(world).isoweekday()),
                network=Network.MTN,
                phone=client.phone,
            ),
            idempotency_key=f"key-{plan_key(client.id)}",
            account_phone=client.phone,
            clock=world.clock,
        )
    # Weekly on today's weekday: the first run is a week from today, never today itself.
    assert plan.next_run_on == today(world) + timedelta(days=7)

    # On that day, in the prompt hours, the worker opens the run.
    world.clock.current = datetime.combine(plan.next_run_on, time(9), KAMPALA).astimezone(UTC)
    async with world.worker_database.transaction() as session:
        await enqueue(
            session,
            JobRequest(kind=OPEN_DUE_KIND, idempotency_key=f"open:{plan.id}", payload={}),
            now=world.clock.now(),
        )
    await world.drain()

    async with world.worker_database.actor_transaction(SYSTEM) as session:
        runs = await plans.plan_runs(session, plan.id)
        moved = await plans.get_plan(session, plan.id)
    assert [(run.due_on, run.status) for run in runs] == [(plan.next_run_on, RunStatus.DUE)]
    assert moved.next_run_on == plan.next_run_on + timedelta(days=7)
    # Asking for the run's investment and payment is not built yet: the run opens and waits.
    assert runs[0].investment_id is None and runs[0].deposit_id is None
    trail = await audit_for(migrator_database, [plan.id])
    assert missing(trail, "autoinvest.plan.created") == []


def plan_key(client_id: str) -> str:
    return f"plan-{client_id}"
