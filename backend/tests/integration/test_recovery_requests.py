"""Someone locked out asks for a call back, without signing in, and staff close it."""

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.api import COOKIES
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.recovery import REQUESTS_PER_NUMBER
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
CONSOLE = "https://admin.finvestug.com"
APP = "https://app.finvestug.com"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = itertools.count(780_000_000)
_addresses = itertools.count()


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    database: Database
    worker: Database
    owner: Database


@pytest.fixture
async def api(
    test_database: TestDatabase,
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"staff_allowed_origins": [CONSOLE], "client_allowed_origins": [APP]}
    )
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)
    # Each test calls from its own address, so the limit per address is not shared between tests.
    n = next(_addresses)
    address = (f"10.0.{n // 250}.{n % 250 + 1}", 50000)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=address)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api", headers={"Origin": CONSOLE}
    ) as http:
        yield Api(http, app.state.identity, clock, app_database, worker_database, migrator_database)


def a_phone() -> str:
    return f"+256{next(_numbers)}"


async def a_staff_member(api: Api, role: str = "operations") -> dict[str, str]:
    """A signed-in member of staff, as the cookie header their browser would send."""
    staff_id = await api.identity.invite_staff(
        email=f"staff{next(_numbers)}@finvestug.com",
        name="Ada Nakato",
        role=role,  # type: ignore[arg-type]
        created_by=None,
    )
    # Test set-up only: enrolment is covered in test_second_steps.
    async with api.owner.transaction() as session:
        await session.execute(
            text(
                "UPDATE identity_staff SET password_hash = 'x', status = 'active', "
                "password_changed_at = created_at WHERE id = :id"
            ),
            {"id": staff_id},
        )
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    return {"Cookie": f"{COOKIES['staff']}={issued.token}", "X-Staff-Id": staff_id}


async def a_client(api: Api) -> tuple[str, str, str]:
    phone = a_phone()
    email = f"client{next(_numbers)}@example.com"
    client_id = await api.identity.create_client(phone=phone, email=email, password=PASSWORD)
    return phone, email, client_id


async def ask(api: Api, phone: str, contact: str, reason: str = "lost_phone") -> httpx.Response:
    return await api.http.post(
        "/client/recovery-requests",
        json={"phone": phone, "contact_phone": contact, "reason": reason},
        headers={"Origin": APP},
    )


async def messages(api: Api, recipient: str) -> list[str]:
    async with api.worker.transaction() as session:
        rows = await session.scalars(
            text("SELECT template FROM notification_messages WHERE recipient = :r ORDER BY id"),
            {"r": recipient},
        )
        return list(rows)


async def test_a_request_reaches_staff_and_tells_the_account(api: Api) -> None:
    phone, _, client_id = await a_client(api)
    contact = a_phone()
    asked = await ask(api, "0" + phone[4:], contact)
    assert asked.status_code == 202
    # The owner hears of it on the account's own number, in case it was not them.
    assert "recovery_requested" in await messages(api, phone)

    staff = await a_staff_member(api)
    listed = await api.http.get("/staff/recovery-requests", headers=staff)
    assert listed.status_code == 200
    [item] = [i for i in listed.json()["items"] if i["client_id"] == client_id]
    assert (item["account_phone"], item["contact_phone"], item["reason"]) == (
        phone,
        contact,
        "lost_phone",
    )
    closed = await api.http.post(
        f"/staff/recovery-requests/{item['id']}/close",
        json={"outcome": "number_changed"},
        headers=staff,
    )
    assert closed.status_code == 204
    again = await api.http.post(
        f"/staff/recovery-requests/{item['id']}/close", json={"outcome": "helped"}, headers=staff
    )
    assert again.status_code == 404
    listed = await api.http.get("/staff/recovery-requests", headers=staff)
    assert all(i["id"] != item["id"] for i in listed.json()["items"])
    async with api.owner.transaction() as session:
        actions = list(
            await session.scalars(
                text("SELECT action FROM audit_events WHERE resource_id = :id"), {"id": client_id}
            )
        )
    assert "identity.client.recovery_closed" in actions


async def test_a_number_without_an_account_gets_the_same_answer_and_keeps_nothing(
    api: Api,
) -> None:
    nobody, contact = a_phone(), a_phone()
    asked = await ask(api, nobody, contact)
    assert asked.status_code == 202
    assert await messages(api, nobody) == []
    async with api.owner.transaction() as session:
        stored = await session.scalar(
            text("SELECT count(*) FROM identity_recovery_requests WHERE contact_phone = :c"),
            {"c": contact},
        )
    assert stored == 0


async def test_asking_is_limited_per_number(api: Api) -> None:
    phone, _, _ = await a_client(api)
    for _ in range(REQUESTS_PER_NUMBER):
        assert (await ask(api, phone, a_phone())).status_code == 202
    assert (await ask(api, phone, a_phone())).status_code == 429


async def test_a_bad_number_or_reason_is_refused(api: Api) -> None:
    assert (await ask(api, "not a number", a_phone())).status_code == 422
    assert (await ask(api, a_phone(), a_phone(), "something else")).status_code == 422


async def test_only_staff_with_the_permission_see_requests(api: Api) -> None:
    support = await a_staff_member(api, "support")
    assert (await api.http.get("/staff/recovery-requests", headers=support)).status_code == 403
    phone, _, _ = await a_client(api)
    await ask(api, phone, a_phone())
    # The app's role sees no request outside a staff transaction, and cannot write one.
    async with api.database.transaction() as session:
        seen = await session.scalar(text("SELECT count(*) FROM identity_recovery_requests"))
    assert seen == 0
    with pytest.raises(DBAPIError):
        async with api.database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO identity_recovery_requests (id, reason, status, created_at) "
                    "VALUES ('-Nabc00000000000000R', 'other', 'no_account', now())"
                )
            )


async def open_requests(api: Api, client_id: str) -> int:
    async with api.owner.transaction() as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM identity_recovery_requests "
                "WHERE client_id = :id AND status = 'open'"
            ),
            {"id": client_id},
        )
    return int(count or 0)


async def test_the_account_is_told_once_while_its_request_is_open(api: Api) -> None:
    phone, _, client_id = await a_client(api)
    # Two asks, then two more after the limit's window: the same answer each time.
    for _ in range(2):
        for _ in range(REQUESTS_PER_NUMBER):
            assert (await ask(api, phone, a_phone())).status_code == 202
        api.clock.advance(minutes=16)
    assert await messages(api, phone) == ["recovery_requested"]
    assert await open_requests(api, client_id) == 1

    # Once staff close it, the next ask opens a new request and tells the account again.
    staff = await a_staff_member(api)
    [item] = [
        i
        for i in (await api.http.get("/staff/recovery-requests", headers=staff)).json()["items"]
        if i["client_id"] == client_id
    ]
    closed = await api.http.post(
        f"/staff/recovery-requests/{item['id']}/close", json={"outcome": "helped"}, headers=staff
    )
    assert closed.status_code == 204
    assert (await ask(api, phone, a_phone())).status_code == 202
    assert await messages(api, phone) == ["recovery_requested", "recovery_requested"]
    assert await open_requests(api, client_id) == 1


async def test_parallel_asks_open_one_request_and_tell_the_account_once(api: Api) -> None:
    phone, _, client_id = await a_client(api)
    answers = await asyncio.gather(
        *(ask(api, phone, a_phone()) for _ in range(REQUESTS_PER_NUMBER))
    )
    assert [answer.status_code for answer in answers] == [202] * REQUESTS_PER_NUMBER
    assert await open_requests(api, client_id) == 1
    assert await messages(api, phone) == ["recovery_requested"]
