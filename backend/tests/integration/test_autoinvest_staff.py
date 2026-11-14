"""What staff read about automatic investing, against real PostgreSQL and over HTTP."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.autoinvest import service as autoinvest
from app.modules.payments.domain.deposits import Network
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import approved_client
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

PHONE = "0772 123 456"
# A day no other test opens runs on, so this file's runs are easy to find.
DUE = date(2027, 2, 17)


def at(day: date, hour: int = 9) -> FakeClock:
    """`hour` in Kampala on `day`."""
    return FakeClock(datetime(day.year, day.month, day.day, hour - 3, tzinfo=UTC))


async def due_run(
    app_database: Database, worker_database: Database
) -> tuple[Actor, autoinvest.Plan]:
    """A weekly plan set up the day before `DUE`, whose run the daily job has opened."""
    client = await approved_client(app_database, at(DUE - timedelta(days=1)))
    async with app_database.actor_transaction(client) as session:
        plan = await autoinvest.create_plan(
            session,
            client,
            autoinvest.PlanTerms(
                product_code="TREA-91",
                amount=ugx(150_000),
                schedule=autoinvest.Schedule(autoinvest.Frequency.WEEKLY, DUE.isoweekday()),
                network=Network.AIRTEL,
                phone=PHONE,
            ),
            idempotency_key=key(),
            account_phone=PHONE,
            clock=at(DUE - timedelta(days=1)),
        )
    async with worker_database.actor_transaction(SYSTEM) as session:
        await autoinvest.open_due_runs(session, at(DUE))
    return client, plan


async def test_staff_see_each_run_with_its_plan_terms(
    app_database: Database, worker_database: Database
) -> None:
    client, plan = await due_run(app_database, worker_database)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        runs = await autoinvest.staff_runs(session, staff, date_from=DUE, date_to=DUE)
        counts = await autoinvest.plan_counts(session, staff)
    mine = next(item for item in runs if item.run.plan_id == plan.id)
    assert (mine.run.client_id, mine.run.due_on, mine.run.status) == (
        client.id,
        DUE,
        autoinvest.RunStatus.DUE,
    )
    assert (mine.product_code, mine.amount, mine.frequency, mine.day, mine.network) == (
        "TREA-91",
        ugx(150_000),
        autoinvest.Frequency.WEEKLY,
        DUE.isoweekday(),
        Network.AIRTEL,
    )
    assert counts.active >= 1
    # Newest first, and a page carries on below the last one seen.
    async with app_database.actor_transaction(staff) as session:
        after = await autoinvest.staff_runs(
            session, staff, date_from=DUE, date_to=DUE, before_id=mine.run.id
        )
    assert all(item.run.id < mine.run.id for item in after)


async def test_only_staff_read_them(app_database: Database) -> None:
    client = await approved_client(app_database, at(DUE))
    async with app_database.actor_transaction(client) as session:
        for read in (
            autoinvest.plan_counts(session, client),
            autoinvest.staff_runs(session, client, date_from=DUE, date_to=DUE),
        ):
            with pytest.raises(ApplicationError) as refused:
                await read
            assert refused.value.code == "forbidden"


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        yield running


async def test_the_routes_need_the_permission_and_reading_runs_is_audited(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    _, plan = await due_run(app_database, worker_database)
    staff = await api.staff("operations")
    counts = await api.http.get("/staff/autoinvest/plans/counts")
    assert counts.status_code == 200
    assert set(counts.json()) == {"active", "paused"}
    listed = await api.http.get(
        "/staff/autoinvest/runs",
        params={"date_from": DUE.isoformat(), "date_to": DUE.isoformat()},
    )
    assert listed.status_code == 200, listed.text
    run = next(item for item in listed.json()["runs"] if item["plan_id"] == plan.id)
    assert (run["product_name"], run["amount_minor"], run["currency"], run["network"]) == (
        "91 Day Treasury Bill",
        150_000,
        "UGX",
        "airtel",
    )
    async with app_database.actor_transaction(new_staff()) as session:
        audited = await session.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE action = 'autoinvest.runs_viewed' "
                "AND actor_id = :staff"
            ),
            {"staff": staff.id},
        )
    assert audited == 1
    too_long = await api.http.get(
        "/staff/autoinvest/runs",
        params={"date_from": "2027-01-01", "date_to": "2027-06-01"},
    )
    assert too_long.status_code == 422
    await api.staff("support")
    for path in (
        "/staff/autoinvest/plans/counts",
        "/staff/autoinvest/runs?date_from=2027-02-17&date_to=2027-02-17",
    ):
        assert (await api.http.get(path)).status_code == 403, path
