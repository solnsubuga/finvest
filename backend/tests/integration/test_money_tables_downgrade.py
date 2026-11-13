"""A downgrade must never delete the books, the issued numbers or the audit trail."""

import os
from collections.abc import Iterator

import pytest

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

ROWS = {
    "0010_ledger": (
        "INSERT INTO ledger_accounts (id, code, kind, currency, name, created_at) "
        "VALUES ('-Nabc0000000000000A1', 'test.cash', 'asset', 'UGX', 'Cash', now())",
        "refusing to drop a ledger",
    ),
    "0011_account_numbers": (
        "INSERT INTO account_numbers (number, issued_at) VALUES ('1001004827', now())",
        "refusing to drop the registry",
    ),
    "0012_audit_events": (
        "INSERT INTO audit_events (id, occurred_at, category, action, outcome, actor_kind, "
        "resource_type) VALUES ('-Nabc0000000000000A1', now(), 'change', 'test.changed', "
        "'succeeded', 'system', 'test')",
        "refusing to drop an audit trail",
    ),
}


@pytest.fixture
def scratch_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_downgrade_{os.getpid()}")
    try:
        yield database
    finally:
        harness.drop(database)


@pytest.mark.parametrize("revision", ROWS)
def test_a_downgrade_refuses_to_drop_a_table_that_holds_rows(
    scratch_database: TestDatabase, revision: str
) -> None:
    statement, refusal = ROWS[revision]
    assert harness.alembic(scratch_database, "upgrade", revision).returncode == 0
    migrator = scratch_database.admin.set(
        username="finvest_migrator",
        password=harness.PASSWORDS["finvest_migrator"],
        database=scratch_database.name,
    )
    harness.psql(migrator, "-c", statement)

    result = harness.alembic(scratch_database, "downgrade", "-1")
    assert result.returncode != 0
    assert refusal in result.stderr
    current = harness.alembic(scratch_database, "current")
    assert revision in current.stdout
    # The row is still there.
    table = statement.split()[2]
    harness.psql(
        migrator,
        "-c",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM {table}) THEN "
        "RAISE EXCEPTION 'row was lost'; END IF; END $$",
    )
