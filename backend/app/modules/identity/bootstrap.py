"""Add a member of staff from the command line and print their one-time enrolment link.

Staff are normally invited from the console. This adds the first administrator, or sends a
new link to someone who cannot sign in. Run it with the API's settings (as `finvest_app`):

    python -m app.modules.identity.bootstrap --email ada@finvestug.com --name "Ada Nakato"

The link is shown once. Whoever opens it sets the password and adds the passkey, so give it to
that person only. It works once and stops working after 72 hours.
"""

import argparse
import asyncio
from typing import get_args

from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.modules.identity.domain.accounts import normalize_email
from app.modules.identity.domain.permissions import Role
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import PasskeyService
from app.modules.identity.service import IdentityService
from app.shared.clock import Clock, SystemClock

ENROL_PATH = "/enrol"


async def invite(
    settings: Settings, database: Database, clock: Clock, *, email: str, name: str, role: Role
) -> str:
    """Create the person if they are new, and return their enrolment link."""
    if not settings.staff_allowed_origins:
        raise SystemExit("Set FINVEST_STAFF_ALLOWED_ORIGINS to the console's address first.")
    hasher = PasswordHasher(
        memory_kib=settings.password_memory_kib, time_cost=settings.password_time_cost
    )
    identity = IdentityService(database, clock, settings, hasher)
    passkeys = PasskeyService(database, clock, settings, identity, hasher)
    async with database.transaction() as session:
        staff_id = await session.scalar(
            text("SELECT id FROM identity_staff WHERE email = :email"),
            {"email": normalize_email(email)},
        )
    if staff_id is None:
        staff_id = await identity.invite_staff(email=email, name=name, role=role, created_by=None)
    token = await passkeys.create_enrolment(staff_id)
    # In the fragment, so the token is not sent to any server or written to a proxy log.
    return f"{settings.staff_allowed_origins[0]}{ENROL_PATH}#{token}"


async def run(email: str, name: str, role: Role) -> str:
    settings = Settings()
    database = Database(settings)
    try:
        return await invite(settings, database, SystemClock(), email=email, name=name, role=role)
    finally:
        await database.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=get_args(Role), default="admin")
    arguments = parser.parse_args(argv)
    link = asyncio.run(run(arguments.email, arguments.name, arguments.role))
    print(link)  # noqa: T201 - the link is this command's output, shown once


if __name__ == "__main__":
    main()
