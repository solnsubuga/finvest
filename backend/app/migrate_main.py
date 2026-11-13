"""The migration process: `python -m app.migrate_main`. Connect it as `finvest_migrator`.

It refuses to run as any other role, so tables can never end up owned by a role the API or
worker could use.
"""

import asyncio
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config

from app.infrastructure.database import Database
from app.infrastructure.database.roles import MIGRATOR, check_role
from app.infrastructure.logging import configure_logging
from app.infrastructure.settings import Settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


async def preflight(settings: Settings) -> None:
    database = Database(settings)
    try:
        await check_role(database, MIGRATOR)
    finally:
        await database.close()


def main() -> None:
    settings = Settings()
    configure_logging(settings, "migrate")
    asyncio.run(preflight(settings))
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    structlog.get_logger().info("migrations_applied")


if __name__ == "__main__":
    main()
