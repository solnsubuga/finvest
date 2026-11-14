"""Migration 0333 changes the investments guard the database has, not an older copy of it, so a
rule another migration added to the guard survives both its upgrade and its downgrade."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import URL

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

BEFORE, PAY_INTEREST = "0303_goal_locks", "0333_maturity_pay_interest"
MARK = "a rule a later migration added"

# Stands in for a later change to the guard, such as the staff withdrawal clause.
ADD_MARK = f"""
DO $$
DECLARE definition text;
BEGIN
    SELECT pg_get_functiondef('public.finvest_investments_guard()'::regprocedure)
        INTO definition;
    EXECUTE regexp_replace(definition, 'BEGIN', E'BEGIN\\n    -- {MARK}');
END
$$
"""


# Fails unless the guard's text holds NEEDLE as often as WANT says: at least once, or never.
CHECK = """
DO $$
BEGIN
    IF (position('NEEDLE' IN (SELECT prosrc FROM pg_proc
            WHERE proname = 'finvest_investments_guard')) > 0) <> WANT THEN
        RAISE EXCEPTION 'the guard is not as expected';
    END IF;
END
$$
"""


def guard_has(url: URL, needle: str, present: bool) -> None:
    harness.psql(url, "-c", CHECK.replace("NEEDLE", needle).replace("WANT", str(present).lower()))


@pytest.fixture
def scratch_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_pay_interest_guard_{os.getpid()}")
    try:
        yield database
    finally:
        harness.drop(database)


def test_the_guard_keeps_what_other_migrations_added(scratch_database: TestDatabase) -> None:
    url = scratch_database.admin.set(database=scratch_database.name)
    assert harness.alembic(scratch_database, "upgrade", BEFORE).returncode == 0
    harness.psql(url, "-c", ADD_MARK)

    result = harness.alembic(scratch_database, "upgrade", PAY_INTEREST)
    assert result.returncode == 0, result.stderr
    guard_has(url, MARK, present=True)
    guard_has(url, "pay_interest", present=True)

    result = harness.alembic(scratch_database, "downgrade", BEFORE)
    assert result.returncode == 0, result.stderr
    guard_has(url, MARK, present=True)
    guard_has(url, "pay_interest", present=False)
