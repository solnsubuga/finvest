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
from app.modules.identity.domain.pins import CONFIRMATION_LASTS, LOCK_AFTER, MAX_WRONG_PINS
from app.modules.identity.domain.sessions import token_digest
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import RECENT_SIGN_IN
from app.modules.identity.pins import PinService
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
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
    pins: PinService
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
        yield Api(http, app.state.identity, app.state.pins, clock, migrator_database)


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


async def confirm(
    api: Api, headers: dict[str, str], pin: str = PIN, action: str = "withdrawal"
) -> httpx.Response:
    return await api.http.post(
        "/client/pin/confirm", json={"pin": pin, "action": action}, headers=headers
    )


def token_of(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


async def test_a_right_pin_confirms_one_action_once(api: Api) -> None:
    _, phone = await a_phone(api)
    assert (await set_pin(api, phone)).status_code == 204
    client = await api.identity.client_session(token_of(phone))
    assert client is not None and api.pins.needs_confirmation(client)

    # Without a confirmation, the action is refused.
    with pytest.raises(ApplicationError) as missing:
        await api.pins.spend(client, "withdrawal", None)
    assert missing.value.code == "pin_required"

    wrong = await confirm(api, phone, "90817")
    assert wrong.json() == {
        "confirmed": False,
        "tries_left": MAX_WRONG_PINS - 1,
        "confirmation": None,
    }
    right = (await confirm(api, phone)).json()
    assert right["confirmed"] is True
    assert right["tries_left"] == MAX_WRONG_PINS

    # It is for its own action only, and works once.
    with pytest.raises(ApplicationError):
        await api.pins.spend(client, "payout_account", right["confirmation"])
    await api.pins.spend(client, "withdrawal", right["confirmation"])
    with pytest.raises(ApplicationError):
        await api.pins.spend(client, "withdrawal", right["confirmation"])


async def test_a_confirmation_expires_and_belongs_to_its_session(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    _, other = await a_phone(api)
    await set_pin(api, other)
    stranger = await api.identity.client_session(token_of(other))
    assert stranger is not None

    given = (await confirm(api, phone)).json()["confirmation"]
    with pytest.raises(ApplicationError):
        await api.pins.spend(stranger, "withdrawal", given)

    late = (await confirm(api, phone)).json()["confirmation"]
    api.clock.advance(seconds=CONFIRMATION_LASTS.total_seconds())
    client = await api.identity.client_session(token_of(phone))
    assert client is not None
    with pytest.raises(ApplicationError):
        await api.pins.spend(client, "withdrawal", late)


async def test_wrong_confirmations_count_with_unlocking_and_sign_the_phone_out(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    # Wrong PINs share one count, whether they were typed to unlock or to confirm.
    for attempt in range(MAX_WRONG_PINS - 1):
        if attempt % 2:
            await unlock(api, phone, "90817")
        else:
            await confirm(api, phone, "90817")
    last = await confirm(api, phone, "90817")
    assert last.status_code == 401
    assert (await api.http.get("/client/session", headers=phone)).status_code == 401


async def test_parallel_spends_use_a_confirmation_once(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    given = (await confirm(api, phone)).json()["confirmation"]
    client = await api.identity.client_session(token_of(phone))
    assert client is not None

    async def spend() -> bool:
        try:
            await api.pins.spend(client, "withdrawal", given)
        except ApplicationError:
            return False
        return True

    assert sorted(await asyncio.gather(*(spend() for _ in range(6)))) == [False] * 5 + [True]


async def test_nothing_is_asked_without_a_pin(api: Api) -> None:
    _, phone = await a_phone(api)
    client = await api.identity.client_session(token_of(phone))
    assert client is not None and not api.pins.needs_confirmation(client)
    await api.pins.spend(client, "withdrawal", None)
    refused = await confirm(api, phone)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "conflict")


async def test_a_confirmation_spent_in_a_failed_action_can_be_used_again(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    given = (await confirm(api, phone)).json()["confirmation"]
    client = await api.identity.client_session(token_of(phone))
    assert client is not None

    # The action fails after spending, so its transaction rolls back and the confirmation stays.
    with pytest.raises(RuntimeError):
        async with api.owner.transaction() as session:
            await api.pins.spend(client, "withdrawal", given, session=session)
            raise RuntimeError("the withdrawal failed")

    # The retry, in a transaction that commits, spends it; a third try is refused.
    async with api.owner.transaction() as session:
        await api.pins.spend(client, "withdrawal", given, session=session)
    with pytest.raises(ApplicationError) as again:
        await api.pins.spend(client, "withdrawal", given)
    assert again.value.code == "pin_required"


# Fingerprint or face unlock -------------------------------------------------------------------


async def turn_on(api: Api, headers: dict[str, str], pin: str = PIN) -> httpx.Response:
    return await api.http.put("/client/pin/unlock-key", json={"pin": pin}, headers=headers)


async def unlock_with(api: Api, headers: dict[str, str], key: str) -> httpx.Response:
    return await api.http.post("/client/pin/unlock-with-key", json={"key": key}, headers=headers)


async def stored_key(api: Api, headers: dict[str, str]) -> bytes | None:
    async with api.owner.transaction() as session:
        found: bytes | None = await session.scalar(
            text(
                "SELECT p.unlock_key_hash FROM identity_app_pins p "
                "JOIN identity_sessions s ON s.id = p.session_id WHERE s.token_hash = :hash"
            ),
            {"hash": token_digest(token_of(headers))},
        )
    return found


async def test_the_unlock_key_opens_a_locked_app_and_only_its_digest_is_kept(api: Api) -> None:
    client_id, phone = await a_phone(api)
    await set_pin(api, phone)
    made = (await turn_on(api, phone)).json()
    assert (made["enabled"], made["tries_left"]) == (True, MAX_WRONG_PINS)
    key = made["key"]
    assert isinstance(key, str) and len(key) >= 40
    assert await stored_key(api, phone) == token_digest(key)

    idle(api)
    assert (await api.http.get("/client/second-steps", headers=phone)).status_code == 423
    opened = await unlock_with(api, phone, key)
    assert opened.json() == {"unlocked": True, "tries_left": MAX_WRONG_PINS}
    assert (await api.http.get("/client/second-steps", headers=phone)).status_code == 200
    async with api.owner.transaction() as session:
        actions = list(
            await session.scalars(
                text("SELECT action FROM audit_events WHERE actor_id = :id"), {"id": client_id}
            )
        )
    assert "identity.unlock_key.set" in actions


async def test_turning_it_on_needs_the_right_pin_and_a_wrong_one_counts(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    refused = (await turn_on(api, phone, "90817")).json()
    assert refused == {"enabled": False, "tries_left": MAX_WRONG_PINS - 1, "key": None}
    assert await stored_key(api, phone) is None
    assert (await unlock(api, phone, "90817")).json()["tries_left"] == MAX_WRONG_PINS - 2


async def test_a_wrong_key_opens_nothing_and_turns_the_key_off(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    key = (await turn_on(api, phone)).json()["key"]
    idle(api)
    wrong = await unlock_with(api, phone, "not-the-key")
    assert wrong.json() == {"unlocked": False, "tries_left": MAX_WRONG_PINS}
    assert await stored_key(api, phone) is None
    # The right key no longer works either: only the PIN opens the app now.
    assert (await unlock_with(api, phone, key)).json()["unlocked"] is False
    assert (await api.http.get("/client/second-steps", headers=phone)).status_code == 423
    assert (await unlock(api, phone)).json()["unlocked"] is True


async def test_a_key_belongs_to_its_phone(api: Api) -> None:
    _, phone = await a_phone(api)
    _, other = await a_phone(api)
    await set_pin(api, phone)
    await set_pin(api, other)
    key = (await turn_on(api, phone)).json()["key"]
    idle(api)
    assert (await unlock_with(api, other, key)).json()["unlocked"] is False
    assert (await unlock_with(api, phone, key)).json()["unlocked"] is True


async def test_a_new_pin_or_turning_it_off_forgets_the_key(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    key = (await turn_on(api, phone)).json()["key"]
    assert (await set_pin(api, phone, "62830")).status_code == 204
    assert await stored_key(api, phone) is None
    assert (await unlock_with(api, phone, key)).json()["unlocked"] is False

    key = (await turn_on(api, phone, "62830")).json()["key"]
    assert (await api.http.delete("/client/pin/unlock-key", headers=phone)).status_code == 204
    assert await stored_key(api, phone) is None
    assert (await unlock_with(api, phone, key)).json()["unlocked"] is False
    # Turning off what is already off is fine.
    assert (await api.http.delete("/client/pin/unlock-key", headers=phone)).status_code == 204


async def test_a_signed_out_phone_cannot_use_its_key(api: Api) -> None:
    _, phone = await a_phone(api)
    await set_pin(api, phone)
    key = (await turn_on(api, phone)).json()["key"]
    idle(api)
    for _ in range(MAX_WRONG_PINS):
        await unlock(api, phone, "90817")
    assert (await unlock_with(api, phone, key)).status_code == 401


async def test_no_unlock_key_without_a_pin(api: Api) -> None:
    _, phone = await a_phone(api)
    assert (await turn_on(api, phone)).status_code == 409
    assert (await unlock_with(api, phone, "anything")).status_code == 409
