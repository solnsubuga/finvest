"""While a client's request to close their account waits for staff, nothing new starts."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.autoinvest import service as autoinvest
from app.modules.closures import service as closures
from app.modules.goals import service as goals
from app.modules.goals.domain.goals import GoalCategory
from app.modules.payments.domain.deposits import Network
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import PRESENT, approved_client, request
from tests.support.kyc import verify
from tests.support.ledger import key, ugx
from tests.support.payments import start_deposit

pytestmark = pytest.mark.integration

PHONE = "0772 123 456"


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


def closing(error: pytest.ExceptionInfo[ApplicationError]) -> bool:
    return error.value.code == "conflict" and [
        (problem.field_path, problem.code) for problem in error.value.fields
    ] == [(["account"], "closing")]


async def ask(api: Api) -> httpx.Response:
    return await api.http.post("/client/account-closure", json={"reason": "Moving abroad"})


async def pending(database: Database, client: Actor) -> str:
    """A waiting request, saved straight away. Asking through the app is refused while a plan,
    a goal or an investment is live, so this reaches the guards that sit behind those checks."""
    request_id = push_id()
    async with database.actor_transaction(client) as session:
        await session.execute(
            text(
                "INSERT INTO closure_requests (id, client_id, requested_at) "
                "VALUES (:id, :client_id, now())"
            ),
            {"id": request_id, "client_id": client.id},
        )
    return request_id


async def new_goal(database: Database, client: Actor) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.create_goal(
            session,
            client,
            idempotency_key=key(),
            name="School fees",
            category=GoalCategory.SCHOOL_FEES,
            target=ugx(5_000_000),
            target_date=None,
            clock=PRESENT,
        )


def plan_terms(day: int) -> autoinvest.PlanTerms:
    return autoinvest.PlanTerms(
        product_code="TREA-91",
        amount=ugx(200_000),
        schedule=autoinvest.Schedule(autoinvest.Frequency.MONTHLY, day),
        network=Network.MTN,
        phone=PHONE,
    )


async def new_plan(database: Database, client: Actor, clock: FakeClock, day: int) -> str:
    async with database.actor_transaction(client) as session:
        plan = await autoinvest.create_plan(
            session,
            client,
            plan_terms(day),
            idempotency_key=key(),
            account_phone=PHONE,
            clock=clock,
        )
    return plan.id


async def test_a_waiting_request_refuses_a_new_investment_until_it_is_cancelled(api: Api) -> None:
    client = await api.client()
    await verify(api.database, client, api.clock)
    await api.as_client(client)
    asked = await ask(api)
    assert asked.status_code == 201, asked.text
    request_id = asked.json()["request"]["id"]

    with pytest.raises(ApplicationError) as refused:
        await request(api.database, client, PRESENT)
    assert closing(refused)
    with pytest.raises(ApplicationError) as refused:
        await new_goal(api.database, client)
    assert closing(refused)
    with pytest.raises(ApplicationError) as refused:
        await new_plan(api.database, client, PRESENT, day=12)
    assert closing(refused)

    cancelled = await api.http.post(f"/client/account-closure/{request_id}/cancellation")
    assert cancelled.status_code == 200, cancelled.text
    assert (await request(api.database, client, PRESENT)).id


async def test_the_refusal_reaches_the_app_as_a_conflict(api: Api) -> None:
    client = await api.client()
    await verify(api.database, client, api.clock)
    await api.as_client(client)
    assert (await ask(api)).status_code == 201
    started = await api.http.post(
        "/client/investments",
        json={"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"},
        headers={"Idempotency-Key": key()},
    )
    assert started.status_code == 409, started.text
    assert {"field_path": ["account"], "code": "closing"} in [
        {"field_path": problem["field_path"], "code": problem["code"]}
        for problem in started.json()["error"]["fields"]
    ]


async def test_no_payment_in_starts_while_a_request_waits(app_database: Database) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await request(app_database, client, PRESENT)
    await pending(app_database, client)
    with pytest.raises(ApplicationError) as refused:
        await start_deposit(app_database, PRESENT, actor=client, purpose_id=investment.id)
    assert closing(refused)


async def test_a_plan_cannot_be_resumed_while_a_request_waits(app_database: Database) -> None:
    client = await approved_client(app_database, PRESENT)
    plan_id = await new_plan(app_database, client, PRESENT, day=14)
    async with app_database.actor_transaction(client) as session:
        await autoinvest.pause_plan(session, client, plan_id, PRESENT)
    await pending(app_database, client)
    with pytest.raises(ApplicationError) as refused:
        async with app_database.actor_transaction(client) as session:
            await autoinvest.resume_plan(session, client, plan_id, PRESENT)
    assert closing(refused)


async def test_the_daily_run_misses_a_plan_whose_owner_is_closing(
    app_database: Database, worker_database: Database
) -> None:
    day = date(2027, 2, 9)
    eve = FakeClock(datetime(2027, 2, 8, 9, tzinfo=UTC))
    client = await approved_client(app_database, PRESENT)
    plan_id = await new_plan(app_database, client, eve, day=day.day)
    await pending(app_database, client)

    async with worker_database.actor_transaction(SYSTEM) as session:
        opened = await autoinvest.open_due_runs(session, FakeClock(eve.now() + timedelta(days=1)))
    async with app_database.actor_transaction(client) as session:
        runs = await autoinvest.plan_runs(session, plan_id)
    [run] = runs
    assert run.id not in opened.due
    assert (run.status, run.due_on) == (autoinvest.RunStatus.MISSED, day)
    assert run.code == "closing_account"


async def test_an_investment_racing_the_approval_waits_and_is_refused(api: Api) -> None:
    client = await api.client()
    await verify(api.database, client, api.clock)
    await api.as_client(client)
    request_id = (await ask(api)).json()["request"]["id"]
    await api.staff("operations")
    assert (
        await api.http.post(f"/staff/closures/{request_id}/preparation", json={"note": "Checked"})
    ).status_code == 200
    approver = await api.staff("compliance")
    approving = asyncio.Event()

    async def approve() -> None:
        async with api.database.actor_transaction(approver) as session:
            await closures.approve(session, approver, request_id, "Checked", api.clock)
            approving.set()
            await asyncio.sleep(0.3)

    async def invest() -> str:
        await approving.wait()
        try:
            await request(api.database, client, PRESENT)
        except ApplicationError as error:
            return error.code
        return "started"

    _, outcome = await asyncio.gather(approve(), invest())
    assert outcome == "conflict"


async def test_the_worker_may_ask_whether_a_client_is_closing(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await session.scalar(text("SELECT closure_pending(:id)"), {"id": client.id}) is False
    await pending(app_database, client)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await session.scalar(text("SELECT closure_pending(:id)"), {"id": client.id}) is True
