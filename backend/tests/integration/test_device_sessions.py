"""A person sees their signed-in devices, signs out the ones they do not want, and is told of
a sign-in from a new kind of device."""

import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
PHONE_APP = "Finvest/1.0 (iPhone; iOS 18.1)"
LAPTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
_numbers = itertools.count(760_000_000)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    worker: Database
    owner: Database


@pytest.fixture
async def api(
    test_database: TestDatabase,
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
) -> AsyncIterator[Api]:
    clock = FakeClock()
    app = create_app(test_database.settings("finvest_app"), app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, clock, worker_database, migrator_database)


async def sign_in(api: Api, phone: str, agent: str) -> dict[str, str]:
    """A full client sign-in through the API from one device, with the code fallback."""
    started = await api.http.post(
        "/client/login/start",
        json={"phone": phone, "password": PASSWORD},
        headers={"User-Agent": agent},
    )
    assert started.status_code == 200, started.text
    login_id = started.json()["login_id"]
    await api.http.post(
        "/client/login/code/send", json={"login_id": login_id}, headers={"User-Agent": agent}
    )
    async with api.worker.transaction() as session:
        code = await session.scalar(
            text(
                "SELECT variables->>'code' FROM notification_messages WHERE recipient = :r "
                "AND template = 'sign_in_code' ORDER BY id DESC LIMIT 1"
            ),
            {"r": phone},
        )
    done = await api.http.post(
        "/client/login/code/verify",
        json={"login_id": login_id, "code": code},
        headers={"User-Agent": agent},
    )
    assert done.status_code == 200, done.text
    return {"Authorization": f"Bearer {done.json()['token']}", "User-Agent": agent}


async def a_client(api: Api) -> tuple[str, str]:
    phone = f"+256{next(_numbers)}"
    client_id = await api.identity.create_client(phone=phone, email=None, password=PASSWORD)
    return phone, client_id


async def notices(api: Api, phone: str) -> list[dict[str, str]]:
    async with api.worker.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT variables FROM notification_messages WHERE recipient = :r "
                "AND template = 'new_sign_in' ORDER BY id"
            ),
            {"r": phone},
        )
        return [row.variables for row in rows]


async def test_a_client_sees_their_devices_and_signs_out_the_others(api: Api) -> None:
    phone, _ = await a_client(api)
    phone_app = await sign_in(api, phone, PHONE_APP)
    laptop = await sign_in(api, phone, LAPTOP)
    listed = await api.http.get("/client/sessions", headers=laptop)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert {(i["device"], i["current"]) for i in items} == {
        ("Finvest app on iPhone", False),
        ("Chrome on Windows", True),
    }

    ended = await api.http.post("/client/sessions/sign-out-others", headers=laptop)
    assert ended.json() == {"ended": 1}
    assert (await api.http.get("/client/session", headers=phone_app)).status_code == 401
    assert (await api.http.get("/client/session", headers=laptop)).status_code == 200


async def test_one_device_can_be_signed_out_but_not_someone_elses(api: Api) -> None:
    phone, _ = await a_client(api)
    first = await sign_in(api, phone, PHONE_APP)
    second = await sign_in(api, phone, LAPTOP)
    other_phone, _ = await a_client(api)
    stranger = await sign_in(api, other_phone, LAPTOP)
    [target] = [
        i["id"]
        for i in (await api.http.get("/client/sessions", headers=second)).json()["items"]
        if not i["current"]
    ]
    refused = await api.http.delete(f"/client/sessions/{target}", headers=stranger)
    assert refused.status_code == 404
    assert (await api.http.get("/client/session", headers=first)).status_code == 200
    assert (await api.http.delete(f"/client/sessions/{target}", headers=second)).status_code == 204
    assert (await api.http.get("/client/session", headers=first)).status_code == 401


async def test_a_new_kind_of_device_is_reported_once(api: Api) -> None:
    phone, _ = await a_client(api)
    await sign_in(api, phone, PHONE_APP)
    # The very first session is not news.
    assert await notices(api, phone) == []
    await sign_in(api, phone, PHONE_APP)
    assert await notices(api, phone) == []
    await sign_in(api, phone, LAPTOP)
    [notice] = await notices(api, phone)
    assert notice["device"] == "Chrome on Windows"
    assert "Kampala time" in notice["when"]
    await sign_in(api, phone, LAPTOP)
    assert len(await notices(api, phone)) == 1


async def test_a_crafted_user_agent_never_reaches_the_notice(api: Api) -> None:
    phone, _ = await a_client(api)
    await sign_in(api, phone, PHONE_APP)
    await sign_in(api, phone, "Chrome/1 visit https://evil.example to keep your account")
    [notice] = await notices(api, phone)
    assert notice["device"] == "Chrome"
    assert "evil" not in str(notice)


async def test_email_hears_of_a_new_device_only_at_a_proved_address(api: Api) -> None:
    phone = f"+256{next(_numbers)}"
    email = f"client{next(_numbers)}@example.com"
    client_id = await api.identity.create_client(phone=phone, email=email, password=PASSWORD)
    await sign_in(api, phone, PHONE_APP)
    # Not proved: a stranger could own a mistyped address, so only the phone hears.
    await sign_in(api, phone, LAPTOP)
    assert len(await notices(api, phone)) == 1
    assert await notices(api, email) == []
    async with api.owner.transaction() as session:
        await session.execute(
            text("UPDATE identity_clients SET email_verified_at = now() WHERE id = :id"),
            {"id": client_id},
        )
    await sign_in(api, phone, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari")
    by_sms = await notices(api, phone)
    assert len(by_sms) == 2
    [by_email] = await notices(api, email)
    assert by_email == by_sms[-1]
