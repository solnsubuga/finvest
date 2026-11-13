"""Check the database revision before a deployment: `python -m app.release_preflight`.

Run as finvest_migrator from the new release's image, before it migrates (infra/deployment/
deploy.sh). It only reads. It refuses a database at a revision this image does not know, which
means a newer release already migrated it: deploy that release's image, or restore its backup.

A deployment migrates while the previous release still serves, so each migration must leave a
schema that release can use. One that cannot (it drops or renames what the running code reads)
says `zero_downtime = False`. The output then includes `STOP_PREVIOUS`, and the deployment stops
the previous release before migrating. Ported from Fidiba.
"""

import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.database.roles import MIGRATOR, check_role
from app.infrastructure.settings import Settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
# deploy.sh looks for this exact line.
STOP_PREVIOUS = "Migrations need the previous release stopped first."


def scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def schema_relation(revisions: set[str]) -> str:
    """What this image makes of a database at `revisions`. Raises ValueError if it cannot use it."""
    script = scripts()
    if not revisions:
        return "The database has no schema yet; migrations will create it."
    known = {revision.revision for revision in script.walk_revisions()}
    if revisions - known:
        raise ValueError(
            "Unknown database revision: deploy its matching image or restore its backup."
        )
    heads = set(script.get_heads())
    if revisions == heads:
        return "The database is at this image's schema head."
    message = "The database needs this image's forward migrations."
    pending = script.iterate_revisions(tuple(heads), tuple(revisions))
    blocking = sorted(
        migration.revision
        for migration in pending
        if getattr(migration.module, "zero_downtime", True) is False
    )
    if blocking:
        message += (
            f"\n{', '.join(blocking)} cannot run beside the previous release.\n{STOP_PREVIOUS}"
        )
    return message


async def read_revisions(database: Database) -> set[str]:
    async with database.engine.connect() as connection:
        if not await connection.scalar(text("SELECT to_regclass('public.alembic_version')")):
            return set()
        return set(await connection.scalars(text("SELECT version_num FROM alembic_version")))


async def run(settings: Settings) -> int:
    database = Database(settings)
    try:
        await check_role(database, MIGRATOR)
        revisions = await read_revisions(database)
    finally:
        await database.close()
    try:
        message = schema_relation(revisions)
    except ValueError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"{message}\nRelease preflight passed.\n")
    return 0


def main() -> None:
    sys.exit(asyncio.run(run(Settings())))


if __name__ == "__main__":
    main()
