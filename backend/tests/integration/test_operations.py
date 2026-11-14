"""The operations health check against real PostgreSQL, as the API role.

The database is shared with other tests, so each test compares counts before and after its own
rows, at a time no other test uses.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.api.health import OPERATIONS_CACHE_FOR
from app.infrastructure.database import Database
from app.infrastructure.operations import SIGNALS, operations_signals
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

QUIET = datetime(2001, 1, 1, tzinfo=UTC)
NOW = datetime(2002, 6, 1, 12, tzinfo=UTC)


async def add_job(
    database: Database,
    *,
    status: str,
    run_at: datetime,
    lease_expires_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    running = status == "running"
    async with database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, queue, kind, idempotency_key, payload, status, attempts, "
                "max_attempts, run_at, expires_at, lease_owner, lease_expires_at, created_at, "
                "updated_at, finished_at) VALUES (:id, 'q_operations', 'test.operations', :key, "
                "'{}', :status, :attempts, 5, :run_at, :expires_at, :owner, :lease, :run_at, "
                ":run_at, :finished)"
            ),
            {
                "id": push_id(),
                "key": push_id(),
                "status": status,
                "attempts": 1 if status != "pending" else 0,
                "run_at": run_at,
                "expires_at": run_at + timedelta(days=30),
                "owner": "worker-1" if running else None,
                "lease": lease_expires_at if running else None,
                "finished": finished_at,
            },
        )


async def test_the_api_role_reads_counts_only(app_database: Database) -> None:
    assert set(await operations_signals(app_database, QUIET)) == set(SIGNALS)
    async with app_database.transaction() as session:
        with pytest.raises(Exception, match="permission denied"):
            await session.execute(text("SELECT count(*) FROM schedule_progress"))


async def test_each_kind_of_stopped_work_is_counted(
    app_database: Database, migrator_database: Database
) -> None:
    before = await operations_signals(app_database, NOW)
    # Counted.
    await add_job(migrator_database, status="pending", run_at=NOW - timedelta(minutes=11))
    await add_job(
        migrator_database,
        status="running",
        run_at=NOW - timedelta(hours=1),
        lease_expires_at=NOW - timedelta(minutes=6),
    )
    await add_job(
        migrator_database,
        status="failed",
        run_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
    )
    # Not counted: due recently, a lease just expired, a failure from yesterday or the future.
    await add_job(migrator_database, status="pending", run_at=NOW - timedelta(minutes=9))
    await add_job(
        migrator_database,
        status="running",
        run_at=NOW - timedelta(hours=1),
        lease_expires_at=NOW - timedelta(minutes=4),
    )
    for finished in (NOW - timedelta(hours=25), NOW + timedelta(minutes=1)):
        await add_job(
            migrator_database,
            status="failed",
            run_at=finished - timedelta(hours=1),
            finished_at=finished,
        )
    after = await operations_signals(app_database, NOW)
    assert {name: after[name] - before[name] for name in SIGNALS} == {
        "jobs_overdue": 1,
        "jobs_stalled": 1,
        "jobs_failed": 1,
        "schedules_late": 0,
        "payouts_waiting_on_wallet": 0,
        "interest_behind": 0,
    }


async def test_a_late_schedule_is_counted(
    app_database: Database, migrator_database: Database
) -> None:
    before = await operations_signals(app_database, NOW)
    async with migrator_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO schedule_progress (name, next_fire_at, updated_at) "
                "VALUES (:name, :fire, :fire)"
            ),
            {"name": f"test.late_{uuid4().hex}", "fire": NOW - timedelta(minutes=11)},
        )
    after = await operations_signals(app_database, NOW)
    assert after["schedules_late"] - before["schedules_late"] == 1


async def test_the_endpoint_answers_ok_or_unavailable(
    test_database: TestDatabase, app_database: Database
) -> None:
    clock = FakeClock(current=QUIET)
    app = create_app(test_database.settings("finvest_app"), app_database, clock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        assert (await client.get("/health/operations")).status_code == 200
        clock.current = NOW
        response = await client.get("/health/operations")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"


async def test_one_reading_answers_every_call_for_a_few_seconds(
    test_database: TestDatabase, app_database: Database
) -> None:
    clock = FakeClock(current=QUIET)
    app = create_app(test_database.settings("finvest_app"), app_database, clock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        assert (await client.get("/health/operations")).status_code == 200
        # Work that stopped after the reading is not seen until the reading is old.
        await add_job(app_database, status="pending", run_at=QUIET - timedelta(minutes=11))
        clock.current = QUIET + OPERATIONS_CACHE_FOR - timedelta(seconds=1)
        assert (await client.get("/health/operations")).status_code == 200
        clock.current = QUIET + OPERATIONS_CACHE_FOR
        assert (await client.get("/health/operations")).status_code == 503
