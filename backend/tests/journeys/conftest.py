"""The end-to-end journeys: one database of their own, the whole system on it, checks after each.

Journeys run in a database no other suite touches, migrated exactly as production is and
without the integration suite's test-only ledger source. That lets the day close sign off a
real day on the real cash pots, which can never be undone, without breaking any other test.

After every scenario the whole ledger must balance and every staff money movement must have
its audit event. A scenario adds its own audit expectations on top.
"""

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest

from app.infrastructure.database import Database
from tests.journeys.money import TrustBank
from tests.support import database as harness
from tests.support.books import ledger_problems, staff_entries_without_audit
from tests.support.database import TestDatabase
from tests.support.journey import Collected, World, running_world


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # A day close is permanent: the ledger refuses anything dated on or before it. The
    # scenarios that close a day run after every other journey, in the order they are written.
    items.sort(key=lambda item: item.get_closest_marker("closes_a_day") is not None)


@pytest.fixture(scope="session")
def test_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_journey_{os.getpid()}")
    try:
        result = harness.migrate(database)
        if result.returncode != 0:
            pytest.fail(f"migrations failed:\n{result.stdout}\n{result.stderr}")
        yield database
    finally:
        harness.drop(database)


@pytest.fixture(scope="session")
def bank() -> TrustBank:
    """The trust bank account's statements, shared by every journey, as the real one is."""
    return TrustBank()


@pytest.fixture(scope="session")
def collected() -> dict[str, Collected]:
    """Every collection the stand-in ioTec confirmed, across journeys: its wallet statement."""
    return {}


@pytest.fixture
async def api_database(test_database: TestDatabase) -> AsyncIterator[Database]:
    database = Database(test_database.settings("finvest_app"))
    yield database
    await database.close()


@pytest.fixture
async def worker_database(test_database: TestDatabase) -> AsyncIterator[Database]:
    database = Database(test_database.settings("finvest_worker"))
    yield database
    await database.close()


@pytest.fixture
async def migrator_database(test_database: TestDatabase) -> AsyncIterator[Database]:
    """Table owner, for the checks only. Nothing in a journey writes through it."""
    database = Database(test_database.settings("finvest_migrator"))
    yield database
    await database.close()


@pytest.fixture
async def world(
    test_database: TestDatabase,
    api_database: Database,
    worker_database: Database,
    migrator_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    collected: dict[str, Collected],
) -> AsyncIterator[World]:
    started = datetime.now(UTC)
    async with running_world(
        test_database, api_database, worker_database, monkeypatch, collected
    ) as w:
        yield w
        # The books, after every scenario, whatever it did.
        assert await ledger_problems(migrator_database, worker_database) == []
        assert await staff_entries_without_audit(migrator_database, started) == []
