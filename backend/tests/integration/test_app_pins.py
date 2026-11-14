"""The phone app's PIN: set after logging in, it opens the app after five unused minutes."""

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.domain.pins import LOCK_AFTER, MAX_WRONG_PINS
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import RECENT_SIGN_IN
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
ORIGIN = "https://app.finvestug.com"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
PIN = "48151"
_numbers = itertools.count(770_000_000)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    owner: Database


@pytest.fixture
async def api(
    test_database: TestDatabase, app_database: Database, migrator_database: Database
) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"client_allowed_origins": [ORIGIN]}
    )
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, clock, migrator_database)


async def a_phone(api: Api) -> tuple[str, dict[str, str]]:
    """A client signed in on the phone app, with a bearer token."""
    client_id = await api.identity.create_client(
        phone=f"+256{next(_numbers)}", email=None, password=PASSWORD
    )
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="token"
    )
    return client_id, {"Authorization": f"Bearer {issued.token}"}


async def set_pin(api: Api, headers: dict[str, str], pin: str = PIN) -> httpx.Response:
    return await api.http.put("/client/pin", json={"pin": pin}, headers=headers)


async def unlock(api: Api, headers: dict[str, str], pin: str = PIN) -> httpx.Response:
    return await api.http.post("/client/pin/unlock", json={"pin": pin}, headers=headers)


def idle(api: Api) -> None:
    api.clock.advance(seconds=LOCK_AFTER.total_seconds() + 1)


async def test_a_pin_locks_the_app_after_five_unused_minutes(api: Api) -> None:
    client_id, phone = await a_phone(api)
    assert (await set_pin(api, phone)).status_code == 204
    session = (await api.http.get("/client/session", headers=phone)).json()
    assert (session["pin_set"], session["locked"]) == (True, False)

    idle(api)
    session = (await api.http.get("/client/session", headers=phone)).json()
    assert (session["pin_set"], session["locked"]) == (True, True)
    # Everything else waits for the PIN.
    refused = await api.http.get("/client/second-steps", headers=phone)
    assert (refused.status_code, refused.json()["error"]["code"]) == (423, "app_locked")

    opened = await unlock(api, phone)
    assert opened.json() == {"unlocked": True, "tries_left": MAX_WRONG_PINS}
    assert (await api.http.get("/client/second-steps", headers=phone)).status_code == 200
    async with api.owner.transaction() as session_:
        actions = list(
            await session_.scalars(
                text("SELECT action FROM audit_events WHERE actor_id = :id"), {"id": client_id}
            )
        )
    assert "identity.pin.set" in actions


async def test_without_a_pin_the_app_never_locks(api: Api) -> None:
    _, phone = await a_phone(api)
    idle(api)
    assert (await api.http.get("/client/second-steps", headers=phone)).status_code == 200
    # Unlocking a session with no PIN is a conflict, not a wrong guess.
    assert (await unlock(api, phone)).status_code == 409


async def test_five_wrong_pins_in_a_row_sign_the_phone_out(api: Api) -> None:
    client_id, phone = await a_phone(api)
    await set_pin(api, phone)
    idle(api)
    for left in range(MAX_WRONG_PINS - 1, 0, -1):
        wrong = await unlock(api, phone, "90817")
        assert wrong.json() == {"unlocked": False, "tries_left": left}
    last = await unlock(api, phone, "90817")
    assert last.status_code == 401
    assert (await api.http.get("/client/session", headers=phone)).status_code == 401
    async with api.owner.transaction() as session:
        reason = await session.scalar(
            text("SELECT revoked_reason FROM identity_sessions WHERE actor_id = :id"),
            {"id": client_id},
        )
    assert reason == "pin_tries"


async def test_a_right_pin_starts_the_count_again(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    for _ in range(MAX_WRONG_PINS - 1):
        await unlock(api, phone, "90817")
    assert (await unlock(api, phone)).json()["unlocked"] is True
    assert (await unlock(api, phone, "90817")).json()["tries_left"] == MAX_WRONG_PINS - 1


async def test_parallel_guesses_cannot_pass_the_limit(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    answers = await asyncio.gather(*(unlock(api, phone, "90817") for _ in range(12)))
    refused = [answer for answer in answers if answer.status_code == 200]
    # Each guess was charged before it was checked, so at most the limit were ever looked at.
    assert len(refused) <= MAX_WRONG_PINS - 1
    assert all(not answer.json()["unlocked"] for answer in refused)
    assert (await api.http.get("/client/session", headers=phone)).status_code == 401


async def test_a_simple_or_short_pin_is_refused(api: Api) -> None:
    _, phone = await a_phone(api)
    for pin, problem in (("12345", "too_simple"), ("11111", "too_simple"), ("1234", "format")):
        refused = await set_pin(api, phone, pin)
        assert refused.status_code == 422
        assert refused.json()["error"]["fields"][0]["code"] == problem


async def test_a_new_pin_needs_a_recent_log_in(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    api.clock.advance(seconds=RECENT_SIGN_IN.total_seconds() + 1)
    await unlock(api, phone)
    stale = await set_pin(api, phone, "24680")
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "sign_in_again")


async def test_a_browser_has_no_pin(api: Api) -> None:
    client_id = await api.identity.create_client(
        phone=f"+256{next(_numbers)}", email=None, password=PASSWORD
    )
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="cookie"
    )
    browser = {"Origin": ORIGIN}
    api.http.cookies.set("finvest_client_session", issued.token)
    try:
        refused = await api.http.put("/client/pin", json={"pin": PIN}, headers=browser)
        assert refused.status_code == 403
        session = (await api.http.get("/client/session")).json()
        assert (session["pin_set"], session["locked"]) == (False, False)
    finally:
        api.http.cookies.clear()
