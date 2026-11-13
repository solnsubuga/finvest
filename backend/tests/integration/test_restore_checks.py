"""db/restore-checks.sql against a real database with postings in it.

The database is shared with other tests. The checks are invariants, so they must hold whatever
those tests posted.
"""

import os
import subprocess

import pytest

from app.infrastructure.database import Database
from app.shared.push_ids import validate_push_id
from tests.support import database as harness
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import funded_books

pytestmark = pytest.mark.integration

SCRIPT = harness.BACKEND / "db" / "restore-checks.sql"


def run_checks(database: TestDatabase) -> subprocess.CompletedProcess[str]:
    target = database.admin.set(database=database.name, password=None)
    return subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "-v",
            "ON_ERROR_STOP=1",
            target.render_as_string(hide_password=False),
            "-f",
            str(SCRIPT),
        ],
        env={**os.environ, "PGPASSWORD": database.admin.password or ""},
        capture_output=True,
        text=True,
        check=False,
    )


async def test_a_sound_ledger_passes(test_database: TestDatabase, app_database: Database) -> None:
    await funded_books(app_database, FakeClock(), 5_000)
    result = run_checks(test_database)
    assert result.returncode == 0, result.stderr
    assert "every entry balances" in result.stdout
    assert "migration_head" in result.stdout


def shift_balance(database: TestDatabase, account_id: str, by: int) -> None:
    validate_push_id(account_id)
    # Only a restore gone wrong could do this: the posting trigger is bypassed on purpose, as the
    # administrator, inside one transaction.
    statement = (
        "BEGIN; SET LOCAL session_replication_role = replica; "  # noqa: S608 - a push ID and an int
        f"UPDATE ledger_balances SET balance_minor = balance_minor + {int(by)} "
        f"WHERE account_id = '{account_id}'; COMMIT;"
    )
    harness.psql(database.admin.set(database=database.name), "-c", statement)


async def test_a_balance_that_differs_from_its_lines_fails(
    test_database: TestDatabase, app_database: Database
) -> None:
    books = await funded_books(app_database, FakeClock(), 5_000)
    shift_balance(test_database, books.cash, 1)
    try:
        result = run_checks(test_database)
        assert result.returncode != 0
        assert "ledger balances differ from their lines" in result.stderr
    finally:
        shift_balance(test_database, books.cash, -1)
    assert run_checks(test_database).returncode == 0
