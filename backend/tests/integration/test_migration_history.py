"""Migrations run forwards on a fresh database, backwards to nothing, and forwards again."""

import os
from collections.abc import Iterator

import pytest

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration


@pytest.fixture
def scratch_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_history_{os.getpid()}")
    try:
        yield database
    finally:
        harness.drop(database)


def test_upgrade_downgrade_upgrade(scratch_database: TestDatabase) -> None:
    for arguments in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        result = harness.alembic(scratch_database, *arguments)
        assert result.returncode == 0, result.stderr
    current = harness.alembic(scratch_database, "current")
    assert "(head)" in current.stdout


def test_the_roles_script_can_be_run_again(test_database: TestDatabase) -> None:
    harness.psql(
        test_database.admin.set(database=test_database.name), "-f", str(harness.ROLES_SCRIPT)
    )
