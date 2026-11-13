"""The release preflight reads the real schema as finvest_migrator and changes nothing."""

import pytest

from app.infrastructure.database import Database
from app.release_preflight import read_revisions, run, scripts
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration


async def test_a_migrated_database_passes_and_is_left_as_it_was(
    test_database: TestDatabase, migrator_database: Database
) -> None:
    head = set(scripts().get_heads())
    assert await read_revisions(migrator_database) == head
    assert await run(test_database.settings("finvest_migrator")) == 0
    assert await read_revisions(migrator_database) == head


async def test_it_refuses_to_run_as_any_role_but_the_migrator(test_database: TestDatabase) -> None:
    with pytest.raises(RuntimeError, match="Database role preflight failed"):
        await run(test_database.settings("finvest_app"))
