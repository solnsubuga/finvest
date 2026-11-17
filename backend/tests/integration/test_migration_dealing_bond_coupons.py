"""Migration 0533 adds the bond coupons the desk receives, lets staff post them, and its
downgrade leaves the database as 0532 left it."""

import os
import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import URL

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

BEFORE, COUPONS = "0532_dealing_bonds", "0533_dealing_bond_coupons"


def query(url: URL, sql: str) -> str:
    target = url.set(drivername="postgresql").render_as_string(hide_password=False)
    found = subprocess.run(
        ["psql", "--no-psqlrc", "-At", "-v", "ON_ERROR_STOP=1", target, "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return found.stdout.strip()


def present(url: URL) -> list[str]:
    return [
        query(url, "SELECT count(*) FROM pg_tables WHERE tablename = 'dealing_bond_coupons'"),
        query(
            url,
            "SELECT count(*) FROM ledger_staff_source_types "
            "WHERE source_type = 'dealing.bond_coupon'",
        ),
        query(
            url, "SELECT count(*) FROM pg_proc WHERE proname = 'finvest_dealing_bond_coupons_guard'"
        ),
    ]


@pytest.fixture
def scratch_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_bond_coupons_{os.getpid()}")
    try:
        yield database
    finally:
        harness.drop(database)


def test_the_coupons_table_comes_and_goes_with_its_rules(scratch_database: TestDatabase) -> None:
    url = scratch_database.admin.set(database=scratch_database.name)
    assert harness.alembic(scratch_database, "upgrade", COUPONS).returncode == 0
    assert present(url) == ["1", "1", "1"]
    assert query(
        url,
        "SELECT tgname FROM pg_trigger WHERE tgrelid = 'dealing_bond_coupons'::regclass "
        "AND NOT tgisinternal ORDER BY tgname",
    ).splitlines() == ["dealing_bond_coupons_guard", "dealing_bond_coupons_no_truncate"]

    result = harness.alembic(scratch_database, "downgrade", BEFORE)
    assert result.returncode == 0, result.stderr
    assert present(url) == ["0", "0", "0"]
