"""The API over HTTP for integration tests, with a signed-in client or member of staff."""

import itertools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.api import COOKIES
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = itertools.count(1)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    database: Database

    async def client(self) -> Actor:
        """A new client, signed in on the phone app from here on."""
        tail = f"{abs(hash(push_id())) % 10**6:06d}{next(_numbers) % 100:02d}"
        client_id = await self.identity.create_client(
            phone=f"+2567{tail}", email=None, password=PASSWORD
        )
        actor = Actor("client", client_id)
        await self.as_client(actor)
        return actor

    async def as_client(self, actor: Actor) -> None:
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="token")
        self.http.cookies.clear()
        self.http.headers.pop("Origin", None)
        self.http.headers["Authorization"] = f"Bearer {issued.token}"

    async def staff(self, role: str = "admin", name: str = "Grace Auma") -> Actor:
        """A new member of staff, signed in to the console from here on."""
        staff_id = await self.identity.invite_staff(
            email=f"staff{next(_numbers)}.{abs(hash(push_id())) % 10**8}@finvestug.com",
            name=name,
            role=role,  # type: ignore[arg-type]
            created_by=None,
        )
        # Test set-up only: active, with a password. In the application only enrolment, which
        # also sets a second step, does this.
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                    "password_changed_at = created_at WHERE id = :id"
                ),
                {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
            )
        actor = Actor("staff", staff_id)
        await self.as_staff(actor)
        return actor

    async def as_staff(self, actor: Actor) -> None:
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="cookie")
        self.http.headers.pop("Authorization", None)
        self.http.cookies.clear()
        self.http.cookies.set(COOKIES["staff"], issued.token)
        self.http.headers["Origin"] = STAFF_ORIGIN


@asynccontextmanager
async def running_api(test_database: TestDatabase, database: Database) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
        }
    )
    clock = FakeClock()
    app = create_app(settings, database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, clock, database)
