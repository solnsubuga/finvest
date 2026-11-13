"""A downgrade must never delete the books, the issued numbers, the audit trail, the job
history, the receipts that stop a command running twice or the identity records."""

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
    "0002_jobs": (
        "INSERT INTO outbox_events (id, topic, idempotency_key, payload, created_at) "
        "VALUES ('-Nabc0000000000000A1', 'test.sent', 'k', '{}', now())",
        "refusing to drop job tables",
    ),
    "0003_idempotency": (
        "INSERT INTO operation_receipts (actor_kind, actor_id, operation, idempotency_key, "
        "request_digest, status_code, response, created_at) VALUES ('client', "
        "'-Nabc0000000000000A1', 'test.run', 'key-0001', repeat('0', 64), 200, '{}', now())",
        "refusing to drop operation receipts",
    ),
    "0050_kyc": (
        "INSERT INTO kyc_checks (id, client_id, document_type, document_country, created_at, "
        "updated_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'passport', 'UG', "
        "now(), now())",
        "refusing to drop identity checks",
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
