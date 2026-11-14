"""Setting a platform setting in a test, the way the console does it: two people."""

from typing import Any

from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.platform_settings import service
from app.modules.platform_settings.domain.catalogue import Key
from tests.support.clock import FakeClock
from tests.support.ledger import staff as new_staff


async def set_setting(
    database: Database, key: Key, value: Any, clock: FakeClock, *, address: str | None = None
) -> None:
    """Propose `value` as one member of staff and approve it as another."""
    maker, checker = new_staff(), new_staff()
    check = service.ApprovalCheck(address)
    async with database.actor_transaction(maker) as session:
        proposal = await service.propose(
            session, maker, key, value, "Set by the test", clock, check=check
        )
    clock.advance(seconds=1)
    async with database.actor_transaction(checker) as session:
        await service.decide(
            session, checker, proposal.id, "approved", "Agreed", clock, check=check
        )


async def clear_settings(migrator_database: Database) -> None:
    """Back to the defaults. Settings are shared by every test using the database."""
    async with migrator_database.transaction() as session:
        await session.execute(text("TRUNCATE platform_settings"))
