"""Passkeys, authenticator apps and staff enrolment, through the API as finvest_app."""

import asyncio
import base64
import itertools
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.modules.identity import bootstrap
from app.modules.identity.api import COOKIES
from app.modules.identity.authenticators import CODES_PER_ACCOUNT
from app.modules.identity.domain import totp
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import PasskeyService
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.passkeys import Device

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
CLIENT_SITE = "finvestug.com"
STAFF_SITE = "admin.finvestug.com"
STAFF = {"Origin": STAFF_ORIGIN}
BROWSER = {"Origin": CLIENT_ORIGIN}
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = itertools.count(710_000_000)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    passkeys: PasskeyService
    clock: FakeClock
    database: Database
    settings: Settings


def settings_for(test_database: TestDatabase, **changes: object) -> Settings:
    values = {
        **test_database.settings("finvest_app").model_dump(),
        "client_allowed_origins": [CLIENT_ORIGIN],
        "staff_allowed_origins": [STAFF_ORIGIN],
        "client_passkey_site": CLIENT_SITE,
        "staff_passkey_site": STAFF_SITE,
        # Made up for this run. A real key lives only in the API's environment.
        "authenticator_key": secrets.token_urlsafe(32),
        **changes,
    }
    return Settings(**values)


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    settings = settings_for(test_database)
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    # Each test calls from its own address, so one test's attempts never count against another.
    number = next(_numbers)
    address = {
        "X-Forwarded-For": f"10.{number // 65536 % 256}.{number // 256 % 256}.{number % 256}"
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api", headers=address
    ) as http:
        yield Api(http, app.state.identity, app.state.passkeys, clock, app_database, settings)


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def invited(api: Api, role: str = "operations") -> tuple[str, str, str]:
    """A new member of staff and their enrolment token: (email, staff_id, token)."""
    email = f"staff{next(_numbers)}@finvestug.com"
    staff_id = await api.identity.invite_staff(
        email=email,
        name="Ada Nakato",
        role=role,  # type: ignore[arg-type]
        created_by=None,
    )
    return email, staff_id, await api.passkeys.create_enrolment(staff_id)


async def enrol_with_passkey(api: Api, token: str, device: Device) -> httpx.Response:
    options = await api.http.post(
        "/staff/enrolment/passkey/options", json={"token": token}, headers=STAFF
    )
    assert options.status_code == 200, options.text
    return await api.http.post(
        "/staff/enrolment/passkey/verify",
        json={
            "token": token,
            "password": PASSWORD,
            "credential": device.create(options.json()["public_key"]),
            "passkey_name": "Work laptop",
        },
        headers=STAFF,
    )


async def enrol_with_app(api: Api, token: str) -> bytes:
    """Enrol with an authenticator app and return its secret, as the app would hold it."""
    setup = await api.http.post(
        "/staff/enrolment/authenticator/options", json={"token": token}, headers=STAFF
    )
    assert setup.status_code == 200, setup.text
    shown = setup.json()["secret"]
    secret = base64.b32decode(shown + "=" * (-len(shown) % 8))
    response = await api.http.post(
        "/staff/enrolment/authenticator/verify",
        json={"token": token, "password": PASSWORD, "code": app_code(api, secret)},
        headers=STAFF,
    )
    assert response.status_code == 200, response.text
    return secret


def app_code(api: Api, secret: bytes, drift: int = 0) -> str:
    return totp.code_at(secret, totp.step_at(api.clock.now()) + drift)


async def staff_login(api: Api, email: str) -> dict[str, Any]:
    api.http.cookies.clear()
    response = await api.http.post(
        "/staff/login/start", json={"email": email, "password": PASSWORD}, headers=STAFF
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# Staff enrolment with a passkey


async def test_a_new_member_of_staff_enrols_with_a_passkey_and_is_signed_in(api: Api) -> None:
    email, staff_id, token = await invited(api)
    seen = await api.http.post("/staff/enrolment", json={"token": token}, headers=STAFF)
    assert seen.json() == {
        "name": "Ada Nakato",
        "email": email,
        "role": "operations",
        "methods": ["passkey", "authenticator"],
    }
    device = Device(STAFF_SITE, STAFF_ORIGIN)
    response = await enrol_with_passkey(api, token, device)
    assert response.status_code == 200, response.text
    assert response.json()["staff_id"] == staff_id
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(COOKIES["staff"] + "=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert (await api.http.get("/staff/session")).json()["role"] == "operations"
    async with api.database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT s.status, s.password_hash, p.name, p.public_key "
                    "FROM identity_staff s JOIN identity_passkeys p ON p.actor_id = s.id "
                    "WHERE s.id = :id"
                ),
                {"id": staff_id},
            )
        ).one()
    assert (row.status, row.name) == ("active", "Work laptop")
    assert row.password_hash.startswith("$argon2id$")


async def test_an_enrolment_link_works_once(api: Api) -> None:
    _, _, token = await invited(api)
    assert (await enrol_with_passkey(api, token, Device(STAFF_SITE, STAFF_ORIGIN))).is_success
    for path in ("/staff/enrolment", "/staff/enrolment/passkey/options"):
        again = await api.http.post(path, json={"token": token}, headers=STAFF)
        assert (again.status_code, code(again)) == (410, "expired")


async def test_unknown_and_out_of_date_links_look_the_same(api: Api) -> None:
    _, staff_id, token = await invited(api)
    unknown = await api.http.post(
        "/staff/enrolment", json={"token": secrets.token_urlsafe(32)}, headers=STAFF
    )
    assert (unknown.status_code, code(unknown)) == (410, "expired")
    api.clock.advance(hours=72, seconds=1)
    late = await api.http.post("/staff/enrolment", json={"token": token}, headers=STAFF)
    assert (late.status_code, code(late)) == (410, "expired")
    # A newer link replaces an older one.
    first = await api.passkeys.create_enrolment(staff_id)
    second = await api.passkeys.create_enrolment(staff_id)
    replaced = await api.http.post("/staff/enrolment", json={"token": first}, headers=STAFF)
    assert replaced.status_code == 410
    assert (
        await api.http.post("/staff/enrolment", json={"token": second}, headers=STAFF)
    ).status_code == 200


async def test_only_a_digest_of_the_link_is_stored(api: Api) -> None:
    _, staff_id, token = await invited(api)
    async with api.database.transaction() as session:
        stored = await session.scalar(
            text("SELECT token_hash FROM identity_staff_enrolments WHERE staff_id = :id"),
            {"id": staff_id},
        )
    assert len(stored) == 32
    assert token.encode() not in bytes(stored)


async def test_a_weak_password_is_refused_and_the_passkey_answer_still_works(api: Api) -> None:
    _, _, token = await invited(api)
    device = Device(STAFF_SITE, STAFF_ORIGIN)
    options = await api.http.post(
        "/staff/enrolment/passkey/options", json={"token": token}, headers=STAFF
    )
    credential = device.create(options.json()["public_key"])
    body = {"token": token, "password": "password123", "credential": credential}
    weak = await api.http.post("/staff/enrolment/passkey/verify", json=body, headers=STAFF)
    assert (weak.status_code, code(weak)) == (422, "leaked_password")
    assert weak.json()["error"]["fields"] == [
        {"field_path": ["body", "password"], "code": "leaked_password"}
    ]
    good = await api.http.post(
        "/staff/enrolment/passkey/verify", json={**body, "password": PASSWORD}, headers=STAFF
    )
    assert good.status_code == 200


@pytest.mark.parametrize("fault", ["site", "origin", "unverified", "replayed", "other_link"])
async def test_a_passkey_that_does_not_prove_itself_is_refused(api: Api, fault: str) -> None:
    _, staff_id, token = await invited(api)
    device = Device(
        "evil.example" if fault == "site" else STAFF_SITE,
        "https://evil.example" if fault == "origin" else STAFF_ORIGIN,
    )
    device.user_verified = fault != "unverified"
    options = await api.http.post(
        "/staff/enrolment/passkey/options", json={"token": token}, headers=STAFF
    )
    credential = device.create(options.json()["public_key"])
    if fault == "replayed":
        # The challenge was already spent on another answer.
        other = Device(STAFF_SITE, STAFF_ORIGIN)
        other.user_verified = False
        await api.http.post(
            "/staff/enrolment/passkey/verify",
            json={
                "token": token,
                "password": PASSWORD,
                "credential": other.create(options.json()["public_key"]),
            },
            headers=STAFF,
        )
    if fault == "other_link":
        # An answer made for one link is offered with another person's link.
        _, _, token = await invited(api)
    response = await api.http.post(
        "/staff/enrolment/passkey/verify",
        json={"token": token, "password": PASSWORD, "credential": credential},
        headers=STAFF,
    )
    expected = (410, "expired") if fault in ("replayed", "other_link") else (401, "passkey_failed")
    assert (response.status_code, code(response)) == expected
    assert "set-cookie" not in response.headers
    async with api.database.transaction() as session:
        status = await session.scalar(
            text("SELECT status FROM identity_staff WHERE id = :id"), {"id": staff_id}
        )
    assert status == "invited"


async def test_enrolment_is_refused_from_any_other_site(api: Api) -> None:
    _, _, token = await invited(api)
    for headers in ({}, BROWSER, {"Origin": "https://evil.example"}):
        response = await api.http.post("/staff/enrolment", json={"token": token}, headers=headers)
        assert (response.status_code, code(response)) == (403, "forbidden")


# Staff sign-in with a passkey


async def test_staff_sign_in_with_password_then_passkey(api: Api) -> None:
    email, staff_id, token = await invited(api, "admin")
    device = Device(STAFF_SITE, STAFF_ORIGIN)
    await enrol_with_passkey(api, token, device)
    started = await staff_login(api, email)
    assert started["methods"] == ["passkey"]
    assert (await api.http.get("/staff/session")).status_code == 401

    options = await api.http.post(
        "/staff/login/passkey/options", json={"login_id": started["login_id"]}, headers=STAFF
    )
    public_key = options.json()["public_key"]
    assert public_key["rpId"] == STAFF_SITE
    assert public_key["userVerification"] == "required"
    assert len(public_key["allowCredentials"]) == 1
    verified = await api.http.post(
        "/staff/login/passkey/verify",
        json={"login_id": started["login_id"], "credential": device.get(public_key)},
        headers=STAFF,
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["staff_id"] == staff_id
    assert (await api.http.get("/staff/session")).status_code == 200
    # The same answer cannot open a second session.
    api.http.cookies.clear()
    replay = await api.http.post(
        "/staff/login/passkey/verify",
        json={"login_id": started["login_id"], "credential": device.get(public_key)},
        headers=STAFF,
    )
    assert (replay.status_code, code(replay)) == (410, "expired")


async def test_another_persons_passkey_does_not_finish_a_sign_in(api: Api) -> None:
    email, _, token = await invited(api)
    await enrol_with_passkey(api, token, Device(STAFF_SITE, STAFF_ORIGIN))
    _, _, other_token = await invited(api)
    intruder = Device(STAFF_SITE, STAFF_ORIGIN)
    await enrol_with_passkey(api, other_token, intruder)

    started = await staff_login(api, email)
    options = await api.http.post(
        "/staff/login/passkey/options", json={"login_id": started["login_id"]}, headers=STAFF
    )
    response = await api.http.post(
        "/staff/login/passkey/verify",
        json={
            "login_id": started["login_id"],
            "credential": intruder.get(options.json()["public_key"]),
        },
        headers=STAFF,
    )
    assert (response.status_code, code(response)) == (401, "passkey_failed")
    assert (await api.http.get("/staff/session")).status_code == 401


async def test_a_cloned_passkey_is_refused(api: Api) -> None:
    email, _, token = await invited(api)
    device = Device(STAFF_SITE, STAFF_ORIGIN)
    await enrol_with_passkey(api, token, device)

    async def sign_in() -> httpx.Response:
        started = await staff_login(api, email)
        options = await api.http.post(
            "/staff/login/passkey/options", json={"login_id": started["login_id"]}, headers=STAFF
        )
        return await api.http.post(
            "/staff/login/passkey/verify",
            json={
                "login_id": started["login_id"],
                "credential": device.get(options.json()["public_key"]),
            },
            headers=STAFF,
        )

    assert (await sign_in()).status_code == 200
    # A copy of the key made earlier has a counter that is behind the server's.
    device.sign_count = 0
    stale = await sign_in()
    assert (stale.status_code, code(stale)) == (401, "passkey_failed")


async def test_a_new_link_replaces_a_lost_passkey_and_ends_sessions(api: Api) -> None:
    email, staff_id, token = await invited(api)
    lost = Device(STAFF_SITE, STAFF_ORIGIN)
    old = await enrol_with_passkey(api, token, lost)
    old_cookie = old.cookies[COOKIES["staff"]]

    replacement = Device(STAFF_SITE, STAFF_ORIGIN)
    again = await api.passkeys.create_enrolment(staff_id)
    assert (await enrol_with_passkey(api, again, replacement)).status_code == 200
    assert await api.identity.staff_session(old_cookie) is None

    started = await staff_login(api, email)
    options = await api.http.post(
        "/staff/login/passkey/options", json={"login_id": started["login_id"]}, headers=STAFF
    )
    allowed = options.json()["public_key"]["allowCredentials"]
    assert len(allowed) == 1
    refused = await api.http.post(
        "/staff/login/passkey/verify",
        json={
            "login_id": started["login_id"],
            "credential": lost.get(options.json()["public_key"]),
        },
        headers=STAFF,
    )
    assert (refused.status_code, code(refused)) == (401, "passkey_failed")


# Staff with an authenticator app


async def test_staff_can_enrol_and_sign_in_with_an_authenticator_app(api: Api) -> None:
    email, staff_id, token = await invited(api, "finance")
    setup = await api.http.post(
        "/staff/enrolment/authenticator/options", json={"token": token}, headers=STAFF
    )
    assert setup.json()["uri"].startswith("otpauth://totp/Finvest%3Astaff")
    secret = await enrol_with_app(api, token)
    assert (await api.http.get("/staff/session")).json()["staff_id"] == staff_id

    api.clock.advance(minutes=2)
    started = await staff_login(api, email)
    assert started["methods"] == ["authenticator"]
    response = await api.http.post(
        "/staff/login/authenticator/verify",
        json={"login_id": started["login_id"], "code": app_code(api, secret)},
        headers=STAFF,
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "finance"
    assert COOKIES["staff"] in response.headers["set-cookie"]
    async with api.database.transaction() as session:
        step = await session.scalar(
            text(
                "SELECT second_step FROM identity_sessions WHERE actor_id = :id "
                "AND revoked_at IS NULL ORDER BY id DESC LIMIT 1"
            ),
            {"id": staff_id},
        )
    assert step == "authenticator"


async def test_the_app_secret_is_stored_encrypted(api: Api) -> None:
    _, staff_id, token = await invited(api)
    secret = await enrol_with_app(api, token)
    async with api.database.transaction() as session:
        sealed = bytes(
            await session.scalar(
                text(
                    "SELECT secret_sealed FROM identity_authenticators WHERE actor_id = :id "
                    "AND confirmed_at IS NOT NULL"
                ),
                {"id": staff_id},
            )
        )
    assert secret not in sealed
    assert base64.b64encode(secret) not in sealed
    # The shared sealer's format: version, key id, nonce, the secret as base64 and a tag.
    assert sealed[:1] == b"\x02"
    assert len(sealed) == 1 + 1 + 12 + 28 + 16


async def test_a_code_works_once_even_when_sent_twice_at_the_same_moment(api: Api) -> None:
    email, _, token = await invited(api)
    secret = await enrol_with_app(api, token)
    api.clock.advance(minutes=2)
    first, second = await staff_login(api, email), await staff_login(api, email)
    one_code = app_code(api, secret)
    responses = await asyncio.gather(
        *(
            api.http.post(
                "/staff/login/authenticator/verify",
                json={"login_id": login["login_id"], "code": one_code},
                headers=STAFF,
            )
            for login in (first, second)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 401]


async def test_a_code_from_enrolment_cannot_be_used_to_sign_in(api: Api) -> None:
    email, _, token = await invited(api)
    secret = await enrol_with_app(api, token)
    started = await staff_login(api, email)
    response = await api.http.post(
        "/staff/login/authenticator/verify",
        json={"login_id": started["login_id"], "code": app_code(api, secret)},
        headers=STAFF,
    )
    assert (response.status_code, code(response)) == (401, "invalid_code")


async def test_wrong_codes_are_limited(api: Api) -> None:
    email, _, token = await invited(api)
    secret = await enrol_with_app(api, token)
    api.clock.advance(minutes=20)
    started = await staff_login(api, email)
    for _ in range(CODES_PER_ACCOUNT):
        wrong = await api.http.post(
            "/staff/login/authenticator/verify",
            json={"login_id": started["login_id"], "code": "000000"},
            headers=STAFF,
        )
        assert (wrong.status_code, code(wrong)) == (401, "invalid_code")
    limited = await api.http.post(
        "/staff/login/authenticator/verify",
        json={"login_id": started["login_id"], "code": app_code(api, secret)},
        headers=STAFF,
    )
    assert (limited.status_code, code(limited)) == (429, "rate_limited")
    api.clock.advance(minutes=16)
    started = await staff_login(api, email)
    again = await api.http.post(
        "/staff/login/authenticator/verify",
        json={"login_id": started["login_id"], "code": app_code(api, secret)},
        headers=STAFF,
    )
    assert again.status_code == 200


async def test_a_wrong_code_does_not_finish_enrolment(api: Api) -> None:
    _, staff_id, token = await invited(api)
    await api.http.post(
        "/staff/enrolment/authenticator/options", json={"token": token}, headers=STAFF
    )
    response = await api.http.post(
        "/staff/enrolment/authenticator/verify",
        json={"token": token, "password": PASSWORD, "code": "000000"},
        headers=STAFF,
    )
    assert (response.status_code, code(response)) == (401, "invalid_code")
    async with api.database.transaction() as session:
        status = await session.scalar(
            text("SELECT status FROM identity_staff WHERE id = :id"), {"id": staff_id}
        )
    assert status == "invited"


async def test_staff_have_one_method_after_switching(api: Api) -> None:
    email, staff_id, token = await invited(api)
    await enrol_with_passkey(api, token, Device(STAFF_SITE, STAFF_ORIGIN))
    await enrol_with_app(api, await api.passkeys.create_enrolment(staff_id))
    api.clock.advance(minutes=2)
    assert (await staff_login(api, email))["methods"] == ["authenticator"]


async def test_the_app_option_is_off_without_its_key(
    test_database: TestDatabase, app_database: Database
) -> None:
    settings = settings_for(test_database, authenticator_key=None)
    app = create_app(settings, app_database, FakeClock(), HASHER)
    staff_id = await app.state.identity.invite_staff(
        email=f"staff{next(_numbers)}@finvestug.com", name="Ada", role="admin", created_by=None
    )
    token = await app.state.passkeys.create_enrolment(staff_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        seen = await http.post("/staff/enrolment", json={"token": token}, headers=STAFF)
        assert seen.json()["methods"] == ["passkey"]
        off = await http.post(
            "/staff/enrolment/authenticator/options", json={"token": token}, headers=STAFF
        )
        assert (off.status_code, code(off)) == (503, "unavailable")


# Clients


async def a_signed_in_client(api: Api) -> tuple[str, str, str]:
    phone = f"+256{next(_numbers)}"
    client_id = await api.identity.create_client(phone=phone, email=None, password=PASSWORD)
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="signup", transport="token"
    )
    return phone, client_id, issued.token


async def add_passkey(api: Api, token: str, device: Device) -> httpx.Response:
    bearer = {"Authorization": f"Bearer {token}"}
    options = await api.http.post("/client/passkeys/options", headers=bearer)
    assert options.status_code == 200, options.text
    return await api.http.post(
        "/client/passkeys",
        json={"credential": device.create(options.json()["public_key"]), "name": "My phone"},
        headers=bearer,
    )


async def test_a_client_adds_a_passkey_and_signs_in_with_it(api: Api) -> None:
    phone, client_id, token = await a_signed_in_client(api)
    device = Device(CLIENT_SITE, CLIENT_ORIGIN)
    added = await add_passkey(api, token, device)
    assert added.status_code == 201, added.text
    listed = await api.http.get("/client/passkeys", headers={"Authorization": f"Bearer {token}"})
    assert [item["name"] for item in listed.json()] == ["My phone"]

    # In a browser: the session arrives as a cookie and the token is not in the body.
    started = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}, headers=BROWSER
    )
    assert started.json()["methods"] == ["passkey", "code"]
    login_id = started.json()["login_id"]
    options = await api.http.post(
        "/client/login/passkey/options", json={"login_id": login_id}, headers=BROWSER
    )
    verified = await api.http.post(
        "/client/login/passkey/verify",
        json={"login_id": login_id, "credential": device.get(options.json()["public_key"])},
        headers=BROWSER,
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["token"] is None
    assert verified.json()["session"] == {
        "client_id": client_id,
        "phone": phone,
        "restricted_until": None,
        "email": None,
        "email_verified": False,
    }
    assert COOKIES["client"] in verified.headers["set-cookie"]
    assert (await api.http.get("/client/session")).status_code == 200


async def test_the_phone_app_is_given_a_token_and_no_cookie(api: Api) -> None:
    phone, _, token = await a_signed_in_client(api)
    device = Device(CLIENT_SITE, CLIENT_ORIGIN)
    await add_passkey(api, token, device)
    started = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}
    )
    login_id = started.json()["login_id"]
    options = await api.http.post("/client/login/passkey/options", json={"login_id": login_id})
    verified = await api.http.post(
        "/client/login/passkey/verify",
        json={"login_id": login_id, "credential": device.get(options.json()["public_key"])},
    )
    assert verified.status_code == 200, verified.text
    assert "set-cookie" not in verified.headers
    bearer = {"Authorization": f"Bearer {verified.json()['token']}"}
    assert (await api.http.get("/client/session", headers=bearer)).status_code == 200


async def test_adding_or_removing_a_passkey_needs_a_recent_sign_in(api: Api) -> None:
    _, _, token = await a_signed_in_client(api)
    bearer = {"Authorization": f"Bearer {token}"}
    added = await add_passkey(api, token, Device(CLIENT_SITE, CLIENT_ORIGIN))
    passkey_id = added.json()["id"]
    api.clock.advance(minutes=10, seconds=1)
    for response in (
        await api.http.post("/client/passkeys/options", headers=bearer),
        await api.http.delete(f"/client/passkeys/{passkey_id}", headers=bearer),
    ):
        assert (response.status_code, code(response)) == (403, "sign_in_again")
    assert len((await api.http.get("/client/passkeys", headers=bearer)).json()) == 1


async def test_a_client_removes_only_their_own_passkey(api: Api) -> None:
    phone, _, token = await a_signed_in_client(api)
    _, _, other = await a_signed_in_client(api)
    added = await add_passkey(api, token, Device(CLIENT_SITE, CLIENT_ORIGIN))
    path = f"/client/passkeys/{added.json()['id']}"
    theirs = await api.http.delete(path, headers={"Authorization": f"Bearer {other}"})
    assert (theirs.status_code, code(theirs)) == (404, "not_found")
    mine = await api.http.delete(path, headers={"Authorization": f"Bearer {token}"})
    assert mine.status_code == 204
    started = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}
    )
    assert started.json()["methods"] == ["code"]


async def test_the_same_device_is_not_added_twice(api: Api) -> None:
    _, _, token = await a_signed_in_client(api)
    device = Device(CLIENT_SITE, CLIENT_ORIGIN)
    assert (await add_passkey(api, token, device)).status_code == 201
    again = await add_passkey(api, token, device)
    assert (again.status_code, code(again)) == (409, "conflict")


async def test_passkeys_are_off_without_a_site(
    test_database: TestDatabase, app_database: Database
) -> None:
    settings = settings_for(test_database, client_passkey_site=None)
    app = create_app(settings, app_database, FakeClock(), HASHER)
    client_id = await app.state.identity.create_client(
        phone=f"+256{next(_numbers)}", email=None, password=PASSWORD
    )
    issued = await app.state.identity.issue_session(
        Actor("client", client_id), second_step="signup", transport="token"
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        response = await http.post(
            "/client/passkeys/options", headers={"Authorization": f"Bearer {issued.token}"}
        )
    assert (response.status_code, code(response)) == (503, "unavailable")


# The command that adds the first administrator


async def test_the_bootstrap_command_gives_a_working_link(api: Api) -> None:
    email = f"first{next(_numbers)}@finvestug.com"
    link = await bootstrap.invite(
        api.settings, api.database, api.clock, email=email, name="First Admin", role="admin"
    )
    address, _, token = link.partition("#")
    assert address == f"{STAFF_ORIGIN}/enrol"
    seen = await api.http.post("/staff/enrolment", json={"token": token}, headers=STAFF)
    assert seen.json()["role"] == "admin"
    # Run again for the same person: a fresh link, and the first stops working.
    second = await bootstrap.invite(
        api.settings, api.database, api.clock, email=email, name="First Admin", role="admin"
    )
    assert second != link
    gone = await api.http.post("/staff/enrolment", json={"token": token}, headers=STAFF)
    assert gone.status_code == 410


async def add_app(api: Api, token: str) -> bytes:
    bearer = {"Authorization": f"Bearer {token}"}
    setup = await api.http.post("/client/authenticator/options", headers=bearer)
    assert setup.status_code == 200, setup.text
    shown = setup.json()["secret"]
    secret = base64.b32decode(shown + "=" * (-len(shown) % 8))
    confirmed = await api.http.post(
        "/client/authenticator", json={"code": app_code(api, secret)}, headers=bearer
    )
    assert confirmed.status_code == 204, confirmed.text
    return secret


async def test_a_client_adds_an_authenticator_app_and_signs_in_with_it(api: Api) -> None:
    phone, client_id, token = await a_signed_in_client(api)
    bearer = {"Authorization": f"Bearer {token}"}
    assert (await api.http.get("/client/authenticator", headers=bearer)).json() == {
        "enabled": False
    }
    secret = await add_app(api, token)
    assert (await api.http.get("/client/authenticator", headers=bearer)).json() == {"enabled": True}
    await add_passkey(api, token, Device(CLIENT_SITE, CLIENT_ORIGIN))

    api.clock.advance(minutes=2)
    started = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}
    )
    # Every method the client has set up, to finish with any one of them.
    assert started.json()["methods"] == ["passkey", "authenticator", "code"]
    verified = await api.http.post(
        "/client/login/authenticator/verify",
        json={"login_id": started.json()["login_id"], "code": app_code(api, secret)},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["session"]["client_id"] == client_id
    # An authenticator app is not a texted code, so the account is not restricted.
    assert verified.json()["session"]["restricted_until"] is None
    assert verified.json()["token"]


async def test_a_client_app_is_not_on_until_a_code_confirms_it(api: Api) -> None:
    phone, _, token = await a_signed_in_client(api)
    bearer = {"Authorization": f"Bearer {token}"}
    await api.http.post("/client/authenticator/options", headers=bearer)
    wrong = await api.http.post("/client/authenticator", json={"code": "000000"}, headers=bearer)
    assert (wrong.status_code, code(wrong)) == (401, "invalid_code")
    started = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}
    )
    assert started.json()["methods"] == ["code"]


async def test_a_client_removes_their_authenticator_app(api: Api) -> None:
    phone, _, token = await a_signed_in_client(api)
    bearer = {"Authorization": f"Bearer {token}"}
    secret = await add_app(api, token)
    assert (await api.http.delete("/client/authenticator", headers=bearer)).status_code == 204
    assert (await api.http.delete("/client/authenticator", headers=bearer)).status_code == 404
    api.clock.advance(minutes=2)
    started = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}
    )
    assert started.json()["methods"] == ["code"]
    refused = await api.http.post(
        "/client/login/authenticator/verify",
        json={"login_id": started.json()["login_id"], "code": app_code(api, secret)},
    )
    assert (refused.status_code, code(refused)) == (401, "invalid_code")


async def test_changing_a_client_app_needs_a_recent_sign_in(api: Api) -> None:
    _, _, token = await a_signed_in_client(api)
    bearer = {"Authorization": f"Bearer {token}"}
    await add_app(api, token)
    api.clock.advance(minutes=10, seconds=1)
    for response in (
        await api.http.post("/client/authenticator/options", headers=bearer),
        await api.http.delete("/client/authenticator", headers=bearer),
    ):
        assert (response.status_code, code(response)) == (403, "sign_in_again")


async def test_a_client_sees_the_second_steps_they_have_strongest_first(api: Api) -> None:
    _, _, token = await a_signed_in_client(api)
    bearer = {"Authorization": f"Bearer {token}"}

    async def methods() -> object:
        response = await api.http.get("/client/second-steps", headers=bearer)
        assert response.status_code == 200
        return response.json()["methods"]

    assert await methods() == ["code"]
    await add_app(api, token)
    assert await methods() == ["authenticator", "code"]
    await add_passkey(api, token, Device(CLIENT_SITE, CLIENT_ORIGIN))
    assert await methods() == ["passkey", "authenticator", "code"]
    assert (await api.http.get("/client/second-steps")).status_code == 401


async def test_changes_to_how_someone_signs_in_are_on_the_audit_trail(api: Api) -> None:
    _, staff_id, token = await invited(api)
    await enrol_with_passkey(api, token, Device(STAFF_SITE, STAFF_ORIGIN))
    await enrol_with_app(api, await api.passkeys.create_enrolment(staff_id))
    _, client_id, client_token = await a_signed_in_client(api)
    added = await add_passkey(api, client_token, Device(CLIENT_SITE, CLIENT_ORIGIN))
    await api.http.delete(
        f"/client/passkeys/{added.json()['id']}",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    async with api.database.actor_transaction(Actor("staff", staff_id)) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action, actor_kind, actor_id, category, details::text AS details, "
                    "correlation_id FROM audit_events WHERE actor_id = ANY(:ids) ORDER BY id"
                ),
                {"ids": [staff_id, client_id]},
            )
        ).all()
    assert [(row.actor_kind, row.action) for row in rows] == [
        ("staff", "identity.staff.enrolment_link_issued"),
        ("staff", "identity.passkey.added"),
        ("staff", "identity.staff.enrolled"),
        ("staff", "identity.session.opened"),
        ("staff", "identity.staff.enrolment_link_issued"),
        ("staff", "identity.authenticator.added"),
        ("staff", "identity.staff.enrolled"),
        ("staff", "identity.session.opened"),
        ("client", "identity.session.opened"),
        ("client", "identity.passkey.added"),
        ("client", "identity.passkey.removed"),
    ]
    assert {row.category for row in rows} == {"security"}
    # Tied to the request that made the change, and holding no secret.
    outside_a_request = {"identity.session.opened", "identity.staff.enrolment_link_issued"}
    assert all(row.correlation_id for row in rows if row.action not in outside_a_request)
    # The second link was for someone who could already sign in, and the record says so.
    links = [row.details for row in rows if row.action.endswith("enrolment_link_issued")]
    assert ["true" in details for details in links] == [False, True]
    assert all("secret" not in row.details and "otpauth" not in row.details for row in rows)
