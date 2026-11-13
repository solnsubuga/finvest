"""The three database roles, and a preflight that refuses a connection outside its boundary.

Roles are created by `db/init-roles.sql`, never by a migration. The migrator owns the schema and
every table. The API and worker roles own nothing and cannot bypass row security, which is what
lets the ledger be insert-only for them.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Literal, get_args

from sqlalchemy import text

from app.infrastructure.database.engine import Database
from app.infrastructure.settings import Settings

Role = Literal["finvest_migrator", "finvest_app", "finvest_worker"]
MIGRATOR: Role = "finvest_migrator"
APP: Role = "finvest_app"
WORKER: Role = "finvest_worker"


@dataclass(frozen=True, slots=True)
class RoleFacts:
    current_user: str
    superuser: bool
    bypass_rls: bool
    create_role: bool
    create_database: bool
    create_in_public: bool
    owns_tables: bool


def problems(role: Role, facts: RoleFacts) -> list[str]:
    """Every way the connection differs from `role`'s boundary, named without any secret."""
    found = []
    if facts.current_user != role:
        found.append(f"connected as {facts.current_user!r}, expected {role!r}")
    for granted, attribute in (
        (facts.superuser, "SUPERUSER"),
        (facts.bypass_rls, "BYPASSRLS"),
        (facts.create_role, "CREATEROLE"),
        (facts.create_database, "CREATEDB"),
    ):
        if granted:
            found.append(f"{facts.current_user} has {attribute}")
    if role == MIGRATOR and not facts.create_in_public:
        found.append(f"{MIGRATOR} cannot create in schema public; run db/init-roles.sql")
    if role != MIGRATOR and facts.create_in_public:
        found.append(f"{facts.current_user} can create in schema public")
    if role != MIGRATOR and facts.owns_tables:
        found.append(f"{facts.current_user} owns tables or is a member of {MIGRATOR}")
    return found


_FACTS = text(
    "SELECT current_user, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
    "has_schema_privilege(current_user, 'public', 'CREATE'), "
    "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND pg_has_role(current_user, c.relowner, 'MEMBER')) "
    "OR (current_user <> :migrator AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :migrator) "
    "AND pg_has_role(current_user, :migrator, 'MEMBER')) "
    "FROM pg_roles WHERE rolname = current_user"
)


async def check_role(database: Database, role: Role) -> None:
    async with database.engine.connect() as connection:
        row = (await connection.execute(_FACTS, {"migrator": MIGRATOR})).one()
    found = problems(role, RoleFacts(*row))
    if found:
        raise RuntimeError("Database role preflight failed: " + "; ".join(found))


async def _main(role: Role) -> None:
    database = Database(Settings())
    try:
        await check_role(database, role)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=get_args(Role))
    asyncio.run(_main(parser.parse_args().role))
    sys.stdout.write("Database role preflight passed.\n")


if __name__ == "__main__":
    main()
