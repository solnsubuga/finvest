"""New passwords are refused when a breach list holds them, and stand alone when it cannot say."""

import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.own_changes import OwnChangeService
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.phones import unique_phone

pytestmark = pytest.mark.integration

ORIGIN = "https://app.finvestug.com"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
# Long and unusual enough for the built-in rules, so only the breach list can refuse them.
BREACHED = "marble-copper-giraffe-41"
CLEAN = "walnut-lantern-orbit"
_numbers = itertools.count(778_000_000)


@dataclass
class FakeBreachList:
    breached: set[str] = field(default_factory=lambda: {BREACHED})
    reachable: bool = True
    asked: list[str] = field(default_factory=list)

    async def seen(self, password: str) -> bool | None:
        self.asked.append(password)
        if not self.reachable:
            return None
        return password in self.breached


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    worker: Database
    breach_list: FakeBreachList
    own_changes: OwnChangeService


@pytest.fixture
async def api(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"client_allowed_origins": [ORIGIN]}
    )
    breach_list = FakeBreachList()
    app = create_app(settings, app_database, FakeClock(), HASHER, breached_passwords=breach_list)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    number = next(_numbers)
    address = {"X-Forwarded-For": f"10.9.{number // 256 % 256}.{number % 256}"}
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api", headers=address
    ) as http:
        yield Api(http, app.state.identity, worker_database, breach_list, app.state.own_changes)


def a_phone() -> str:
    return unique_phone()


async def texted(api: Api, recipient: str) -> list[str]:
    async with api.worker.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT variables->>'code' AS code FROM notification_messages "
                "WHERE recipient = :recipient ORDER BY id"
            ),
            {"recipient": recipient},
        )
        return [row.code for row in rows]


async def a_ticket(api: Api) -> str:
    phone = a_phone()
    started = await api.http.post("/client/signup/start", json={"phone": phone})
    confirmed = await api.http.post(
        "/client/signup/confirm",
        json={
            "challenge_id": started.json()["challenge_id"],
            "code": (await texted(api, phone))[-1],
        },
    )
    return str(confirmed.json()["ticket"])


async def finish(api: Api, ticket: str, password: str) -> httpx.Response:
    return await api.http.post(
        "/client/signup/complete",
        json={"ticket": ticket, "password": password, "accepted_terms": True},
    )


async def test_a_breached_password_is_refused_and_the_ticket_kept(api: Api) -> None:
    ticket = await a_ticket(api)
    refused = await finish(api, ticket, BREACHED)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "leaked_password"
    assert refused.json()["error"]["fields"] == [
        {"field_path": ["body", "password"], "code": "leaked_password"}
    ]
    assert (await finish(api, ticket, CLEAN)).status_code == 200


async def test_the_built_in_rules_answer_first_and_the_list_is_not_asked(api: Api) -> None:
    ticket = await a_ticket(api)
    weak = await finish(api, ticket, "password123")
    assert weak.json()["error"]["code"] == "leaked_password"
    assert api.breach_list.asked == []


async def test_an_unreachable_list_leaves_the_built_in_rules_alone(api: Api) -> None:
    api.breach_list.reachable = False
    ticket = await a_ticket(api)
    assert (await finish(api, ticket, BREACHED)).status_code == 200
    assert api.breach_list.asked == [BREACHED]


async def test_a_reset_to_a_breached_password_spends_no_try(api: Api) -> None:
    phone = a_phone()
    await api.identity.create_client(phone=phone, email=None, password=CLEAN)
    started = await api.http.post("/client/password/reset/start", json={"phone": phone})
    [sent] = await texted(api, phone)
    body = {"challenge_id": started.json()["challenge_id"], "code": sent}
    refused = await api.http.post(
        "/client/password/reset/complete", json={**body, "password": BREACHED}
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "leaked_password"
    done = await api.http.post(
        "/client/password/reset/complete", json={**body, "password": "harbour-quill-meadow"}
    )
    assert done.status_code == 204, done.text


async def test_creating_an_account_directly_checks_the_list(api: Api) -> None:
    with pytest.raises(ApplicationError) as raised:
        await api.identity.create_client(phone=a_phone(), email=None, password=BREACHED)
    assert raised.value.code == "leaked_password"


async def test_changing_to_a_breached_password_is_refused(api: Api) -> None:
    client_id = await api.identity.create_client(phone=a_phone(), email=None, password=CLEAN)
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="token"
    )
    client = await api.identity.client_session(issued.token)
    assert client is not None
    with pytest.raises(ApplicationError) as raised:
        await api.own_changes.change_password(client, CLEAN, BREACHED)
    assert raised.value.code == "validation_failed"
    assert [(f.field_path, f.code) for f in raised.value.fields] == [
        (["new_password"], "leaked_password")
    ]
