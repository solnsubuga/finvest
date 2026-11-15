"""One migrated database per test session, reached only through the application roles."""

import os
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from tests.support import database as harness
from tests.support.database import TestDatabase


@pytest.fixture(scope="session")
def test_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_{os.getpid()}")
    try:
        result = harness.migrate(database)
        if result.returncode != 0:
            pytest.fail(f"migrations failed:\n{result.stdout}\n{result.stderr}")
        harness.allow_test_entries_for_staff(database)
        yield database
    finally:
        harness.drop(database)


@pytest.fixture
async def app_database(test_database: TestDatabase) -> AsyncIterator[Database]:
    """The API's view of the database, as finvest_app."""
    database = Database(test_database.settings("finvest_app"))
    yield database
    await database.close()


@pytest.fixture
async def worker_database(test_database: TestDatabase) -> AsyncIterator[Database]:
    """The worker's view of the database, as finvest_worker."""
    database = Database(test_database.settings("finvest_worker"))
    yield database
    await database.close()


@pytest.fixture
async def migrator_database(test_database: TestDatabase) -> AsyncIterator[Database]:
    """Table owner. For test setup and inspection only; application code never connects as it."""
    database = Database(test_database.settings("finvest_migrator"))
    yield database
    await database.close()


# When a job left by an earlier test is set aside: far before any clock a test sets, so it never
# counts as a recent failure and lists of failed jobs, newest first, show it last.
SET_ASIDE_AT = datetime(2000, 1, 1, tzinfo=UTC)
SET_ASIDE = text(
    "UPDATE jobs SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL, "
    "finished_at = :at, last_error_code = 'left_by_an_earlier_test' "
    "WHERE status IN ('pending', 'running')"
)


@pytest.fixture(autouse=True)
async def own_job_queue(migrator_database: Database) -> None:
    """Start each test with no job waiting.

    The job queue is shared by every test in this database, and a test's worker claims any due
    job of the kinds it handles. A job an earlier test left waiting would then run through this
    test's fakes, or a stale one would use up a claim and end a drain early. So the jobs earlier
    tests left are failed, under their own error code, before each test, and a test's worker
    only ever runs the jobs that test made.
    """
    async with migrator_database.transaction() as session:
        await session.execute(SET_ASIDE, {"at": SET_ASIDE_AT})


@pytest.fixture(params=["finvest_app", "finvest_worker"])
async def application_database(
    request: pytest.FixtureRequest, test_database: TestDatabase
) -> AsyncIterator[Database]:
    """Each application role in turn, for boundaries that must hold for both."""
    database = Database(test_database.settings(request.param))
    yield database
    await database.close()


KAMPALA = ZoneInfo("Africa/Kampala")
# Longer than any one test takes.
MIDNIGHT_MARGIN = timedelta(seconds=90)


@pytest.fixture(autouse=True)
def clear_of_midnight() -> None:
    """Start each test only when it can finish before midnight in Kampala.

    The database decides "today" from its own clock, and a test reads the same day in Python.
    A test that started just before midnight would see two different days, so this waits until
    the new day has begun.
    """
    now = datetime.now(KAMPALA)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), KAMPALA)
    if midnight - now < MIDNIGHT_MARGIN:
        time.sleep((midnight - now).total_seconds() + 1)
        assert datetime.now(UTC).astimezone(KAMPALA).date() == midnight.date()
