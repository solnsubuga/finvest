# ruff: noqa: S608 - the SQL is built from this file's own constants
"""Migration 0532 lets dealing orders deal bonds: existing orders stay bills, the settlement and
maturity checks read the bonds asset for bond orders, and the downgrade puts both checks back
as 0081 and 0082 made them, refusing while any bond order exists."""

import os
import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import URL

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

BEFORE, BONDS = "0531_payout_purpose", "0532_dealing_bonds"
CHECKS = "('finvest_dealing_settlement_matches', 'finvest_dealing_maturity_guard')"


def query(url: URL, sql: str) -> str:
    target = url.set(drivername="postgresql").render_as_string(hide_password=False)
    found = subprocess.run(
        ["psql", "--no-psqlrc", "-At", "-v", "ON_ERROR_STOP=1", target, "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return found.stdout.strip()


def reading_bonds(url: URL) -> list[str]:
    return query(
        url,
        "SELECT proname || ':' || (prosrc LIKE '%dealing.bonds%')::text FROM pg_proc "
        f"WHERE proname IN {CHECKS} ORDER BY proname",
    ).splitlines()


@pytest.fixture
def scratch_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_dealing_bonds_{os.getpid()}")
    try:
        yield database
    finally:
        harness.drop(database)


def test_orders_say_what_they_deal_and_the_checks_read_the_bonds_asset(
    scratch_database: TestDatabase,
) -> None:
    url = scratch_database.admin.set(database=scratch_database.name)
    assert harness.alembic(scratch_database, "upgrade", BONDS).returncode == 0
    assert (
        query(
            url,
            "SELECT column_default || ':' || is_nullable FROM information_schema.columns "
            "WHERE table_name = 'dealing_orders' AND column_name = 'instrument'",
        )
        == "'bill'::character varying:NO"
    )
    assert reading_bonds(url) == [
        "finvest_dealing_maturity_guard:true",
        "finvest_dealing_settlement_matches:true",
    ]

    result = harness.alembic(scratch_database, "downgrade", BEFORE)
    assert result.returncode == 0, result.stderr
    assert (
        query(
            url,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'dealing_orders' AND column_name = 'instrument'",
        )
        == "0"
    )
    assert reading_bonds(url) == [
        "finvest_dealing_maturity_guard:false",
        "finvest_dealing_settlement_matches:false",
    ]
