"""Transaction monitoring through the real worker and database: deposits and payouts are
recorded once, rules open monitoring cases once, and two people close them."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.payments.domain.outcomes import Accepted, Confirmed
from app.modules.payments.jobs import DepositJobs
from app.modules.payouts.domain.payouts import PayoutStatus
from app.modules.screening import monitoring
from app.modules.screening.domain.monitoring import (
    LARGE_AMOUNT,
    QUICK_FLOOR,
    Kind,
    Rule,
    Transaction,
)
from app.modules.screening.monitor_jobs import MonitoringJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.clock import Clock
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.jobs import run_due
from tests.support.payments import FakeCollections, phone, start_deposit, ugx
from tests.support.payouts import World

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


@pytest.fixture
async def second_worker(test_database: TestDatabase) -> AsyncIterator[Database]:
    database = Database(test_database.settings("finvest_worker"))
    yield database
    await database.close()


def deposit(amount: int, clock: Clock, *, ago: timedelta = timedelta(0)) -> Transaction:
    return Transaction(push_id(), Kind.DEPOSIT, amount, Currency.UGX, clock.now() - ago)


async def record(
    database: Database, client_id: str, transaction: Transaction, clock: Clock
) -> list[str] | None:
    async with database.actor_transaction(SYSTEM) as session:
        return await monitoring.record(
            session, client_id=client_id, transaction=transaction, clock=clock
        )


async def cases_of(database: Database, client_id: str) -> list[tuple[str, str, str]]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT source, code, status FROM compliance_cases WHERE client_id = :c "
                "ORDER BY opened_at, id"
            ),
            {"c": client_id},
        )
        return [(row.source, row.code, row.status) for row in rows]


async def run_all(worker: Worker) -> None:
    await run_due(worker)


async def test_a_large_confirmed_deposit_opens_one_case(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider, msisdn = FakeCollections(), phone()
    provider.on_send(msisdn, Accepted("ref-large"))
    provider.on_status(msisdn, Confirmed("ref-large", ugx(LARGE_AMOUNT)))
    started = await start_deposit(app_database, fake_clock, msisdn=msisdn, amount=LARGE_AMOUNT)
    worker = Worker(
        worker_database,
        {
            **DepositJobs(worker_database, provider, fake_clock).handlers(),
            **MonitoringJobs(worker_database, fake_clock).handlers(),
        },
        WorkerConfig(),
        fake_clock,
    )
    for _ in range(3):
        fake_clock.advance(seconds=10)
        await run_all(worker)

    assert await cases_of(worker_database, started.client_id) == [
        ("monitoring", Rule.LARGE_DEPOSIT.value, "open")
    ]
    async with worker_database.actor_transaction(SYSTEM) as session:
        row = (
            await session.execute(
                text(
                    "SELECT c.dedupe_key, c.detail, t.amount_minor FROM compliance_cases c "
                    "JOIN monitoring_transactions t ON t.source_id = c.dedupe_key "
                    "WHERE c.client_id = :c"
                ),
                {"c": started.client_id},
            )
        ).one()
    assert row.dedupe_key == started.id
    assert row.detail == {
        "deposit_id": started.id,
        "amount_minor": LARGE_AMOUNT,
        "threshold": LARGE_AMOUNT,
    }
    assert row.amount_minor == LARGE_AMOUNT
    # Read again, as a retried job would: recorded once, and no second case.
    confirmed = Transaction(started.id, Kind.DEPOSIT, LARGE_AMOUNT, Currency.UGX, fake_clock.now())
    assert await record(worker_database, started.client_id, confirmed, fake_clock) is None
    assert len(await cases_of(worker_database, started.client_id)) == 1


async def test_deposits_split_over_a_week_open_one_case(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    client_id = push_id()
    half = LARGE_AMOUNT // 2
    assert (
        await record(
            worker_database, client_id, deposit(half, fake_clock, ago=timedelta(days=3)), fake_clock
        )
        == []
    )
    assert (
        await record(
            worker_database,
            client_id,
            deposit(half - 1, fake_clock, ago=timedelta(days=2)),
            fake_clock,
        )
        == []
    )
    opened = await record(worker_database, client_id, deposit(1, fake_clock), fake_clock)
    assert opened is not None and len(opened) == 1
    # More in the same week raises nothing new.
    assert await record(worker_database, client_id, deposit(1_000, fake_clock), fake_clock) == []
    assert await cases_of(worker_database, client_id) == [
        ("monitoring", Rule.SPLIT_DEPOSITS.value, "open")
    ]


async def test_two_deposits_recorded_together_still_see_each_other(
    worker_database: Database, second_worker: Database, fake_clock: FakeClock
) -> None:
    client_id = push_id()
    half = LARGE_AMOUNT // 2
    results = await asyncio.gather(
        record(
            worker_database,
            client_id,
            deposit(half, fake_clock, ago=timedelta(hours=2)),
            fake_clock,
        ),
        record(
            second_worker, client_id, deposit(half, fake_clock, ago=timedelta(hours=1)), fake_clock
        ),
    )
    # One of them crossed the amount, whichever was recorded second.
    assert sorted(len(opened or []) for opened in results) == [0, 1]
    assert await cases_of(worker_database, client_id) == [
        ("monitoring", Rule.SPLIT_DEPOSITS.value, "open")
    ]


async def test_a_quick_withdrawal_opens_a_case_and_does_not_hold_the_payout(
    api: Api, worker_database: Database
) -> None:
    world = await World(api.database, worker_database, api.clock).build(2 * QUICK_FLOOR)
    for ago in (timedelta(days=2), timedelta(days=1)):
        await record(
            worker_database, world.client_id, deposit(QUICK_FLOOR, api.clock, ago=ago), api.clock
        )
    payout = await world.request(QUICK_FLOOR)
    assert payout.status is PayoutStatus.QUEUED
    await run_all(
        Worker(
            worker_database,
            MonitoringJobs(worker_database, api.clock).handlers(),
            WorkerConfig(),
            api.clock,
        )
    )

    assert await cases_of(worker_database, world.client_id) == [
        ("monitoring", Rule.QUICK_WITHDRAWAL.value, "open")
    ]
    # A monitoring case is for staff to look into: it holds no payout.
    assert (await world.request(1_000)).status is PayoutStatus.QUEUED


async def test_two_people_close_a_monitoring_case_with_a_report_owed(
    api: Api, worker_database: Database
) -> None:
    client_id = push_id()
    await record(worker_database, client_id, deposit(LARGE_AMOUNT, api.clock), api.clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        case_id = await session.scalar(
            text("SELECT id FROM compliance_cases WHERE client_id = :c"), {"c": client_id}
        )

    await api.staff("compliance")
    listed = (await api.http.get("/staff/compliance/cases?source=monitoring")).json()
    assert case_id in [item["id"] for item in listed]
    wrong = await api.http.post(
        f"/staff/compliance/cases/{case_id}/proposals",
        json={"outcome": "not_a_match", "reason": "Not a listed person"},
    )
    assert wrong.status_code == 422, wrong.text
    proposed = await api.http.post(
        f"/staff/compliance/cases/{case_id}/proposals",
        json={"outcome": "report_to_fia", "reason": "Filed on goAML, reference STR-0001"},
    )
    assert proposed.status_code == 201, proposed.text
    proposal = proposed.json()["case"]["waiting"]["id"]
    # The proposer may not approve their own proposal.
    assert (
        await api.http.post(f"/staff/compliance/proposals/{proposal}/approve")
    ).status_code == 403
    await api.staff("compliance")
    approved = await api.http.post(f"/staff/compliance/proposals/{proposal}/approve")
    assert approved.status_code == 200, approved.text
    assert await cases_of(worker_database, client_id) == [
        ("monitoring", Rule.LARGE_DEPOSIT.value, "closed")
    ]


async def test_only_the_worker_records_and_nothing_changes_a_record(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client_id = push_id()
    await record(worker_database, client_id, deposit(1_000, fake_clock), fake_clock)
    insert = text(
        "INSERT INTO monitoring_transactions (id, client_id, kind, source_id, currency, "
        "amount_minor, occurred_at, recorded_at) VALUES (:id, :c, 'deposit', :s, 'UGX', 1, "
        "now(), now())"
    )
    for actor in (Actor("client", client_id), Actor("staff", push_id())):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(insert, {"id": push_id(), "c": client_id, "s": push_id()})
    for statement in (
        "UPDATE monitoring_transactions SET amount_minor = 2 WHERE client_id = :c",
        "DELETE FROM monitoring_transactions WHERE client_id = :c",
    ):
        with pytest.raises(DBAPIError):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"c": client_id})
    # A client reads nothing of it.
    async with app_database.actor_transaction(Actor("client", client_id)) as session:
        assert await session.scalar(text("SELECT count(*) FROM monitoring_transactions")) == 0
