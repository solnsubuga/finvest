"""Sign-in step one, sessions and permissions against real PostgreSQL as finvest_app."""

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity import team as team_changes
from app.modules.identity.api import COOKIES, require_permission
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import (
    ACCOUNT_FROM_ADDRESS_ATTEMPTS,
    ClientContact,
    IdentityService,
    StaffSession,
    client_contact,
)
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = itertools.count(700_000_000)


def a_phone() -> str:
    return f"+256{next(_numbers)}"


def an_email() -> str:
    return f"staff{next(_numbers)}@finvestug.com"


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    database: Database


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
            "trusted_proxies": ["127.0.0.1"],
        }
    )
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)

    @app.get("/staff/team-test")
    async def team(
        staff: Annotated[StaffSession, Depends(require_permission("team.view"))],
    ) -> dict[str, str]:
        return {"staff_id": staff.staff_id}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, clock, app_database)


async def a_client(api: Api) -> tuple[str, str]:
    phone = a_phone()
    return phone, await api.identity.create_client(phone=phone, email=None, password=PASSWORD)


async def a_staff_member(api: Api, role: str = "admin") -> tuple[str, str]:
    email = an_email()
    staff_id = await api.identity.invite_staff(
        email=email,
        name="Ada Nakato",
        role=role,  # type: ignore[arg-type]
        created_by=None,
    )
    # Test set-up only: a password with no second step. Enrolment, which is the only way the
    # application activates staff, is covered in test_second_steps.
    async with api.database.transaction() as session:
        await session.execute(
            text(
                "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                "password_changed_at = created_at WHERE id = :id"
            ),
            {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
        )
    return email, staff_id


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


# Accounts


async def test_a_client_account_stores_a_hash_and_one_number_has_one_account(api: Api) -> None:
    phone, client_id = await a_client(api)
    async with api.database.transaction() as session:
        row = (
            await session.execute(
                text("SELECT phone, password_hash FROM identity_clients WHERE id = :id"),
                {"id": client_id},
            )
        ).one()
    assert row.phone == phone
    assert row.password_hash.startswith("$argon2id$")
    assert PASSWORD not in row.password_hash
    with pytest.raises(ApplicationError) as raised:
        await api.identity.create_client(phone="0" + phone[4:], email=None, password=PASSWORD)
    assert raised.value.code == "phone_taken"


async def test_a_weak_password_is_refused_with_its_reason(api: Api) -> None:
    with pytest.raises(ApplicationError) as raised:
        await api.identity.create_client(phone=a_phone(), email=None, password="password123")
    assert raised.value.code == "leaked_password"
    assert raised.value.fields[0].field_path == ["body", "password"]
    with pytest.raises(ApplicationError) as raised:
        await api.identity.create_client(phone=a_phone(), email=None, password="short")
    assert (raised.value.code, raised.value.fields[0].code) == ("validation_failed", "too_short")


async def test_an_invited_staff_member_cannot_sign_in_until_enrolled(api: Api) -> None:
    email = an_email()
    await api.identity.invite_staff(email=email, name="Ada", role="finance", created_by=None)
    response = await api.http.post(
        "/staff/login/start",
        json={"email": email, "password": PASSWORD},
        headers={"Origin": STAFF_ORIGIN},
    )
    assert (response.status_code, code(response)) == (401, "invalid_credentials")


# Step one


async def test_the_right_password_starts_sign_in_but_opens_nothing(api: Api) -> None:
    phone, _ = await a_client(api)
    response = await api.http.post(
        "/client/login/start", json={"phone": "0" + phone[4:], "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["methods"] == ["code"]
    assert len(response.json()["login_id"]) == 20
    assert "set-cookie" not in response.headers
    assert (await api.http.get("/client/session")).status_code == 401


async def test_every_sign_in_failure_looks_the_same(api: Api) -> None:
    phone, _ = await a_client(api)
    wrong = await api.http.post("/client/login/start", json={"phone": phone, "password": "x" * 12})
    unknown = await api.http.post(
        "/client/login/start", json={"phone": a_phone(), "password": PASSWORD}
    )
    malformed = await api.http.post(
        "/client/login/start", json={"phone": "not a number", "password": PASSWORD}
    )
    for response in (wrong, unknown, malformed):
        assert response.status_code == 401
        body = response.json()
        body.pop("request_id")
        assert body == {
            "error": {
                "code": "invalid_credentials",
                "message": "Those sign-in details do not match an account.",
                "fields": [],
            }
        }


async def test_staff_with_no_second_step_cannot_sign_in(api: Api) -> None:
    email, _ = await a_staff_member(api)
    response = await api.http.post(
        "/staff/login/start",
        json={"email": email.upper(), "password": PASSWORD},
        headers={"Origin": STAFF_ORIGIN},
    )
    assert (response.status_code, code(response)) == (403, "second_step_missing")
    # Said only to someone who knows the password. A wrong one looks like any other failure.
    wrong = await api.http.post(
        "/staff/login/start",
        json={"email": email, "password": "wrong-password-1"},
        headers={"Origin": STAFF_ORIGIN},
    )
    assert (wrong.status_code, code(wrong)) == (401, "invalid_credentials")


async def test_the_no_second_step_answer_is_limited_like_a_wrong_guess(api: Api) -> None:
    # The answer confirms the password, so it must not be free to ask for.
    email, _ = await a_staff_member(api)
    body = {"email": email, "password": PASSWORD}
    headers = {"Origin": STAFF_ORIGIN, "X-Forwarded-For": "203.0.113.44"}
    for _ in range(ACCOUNT_FROM_ADDRESS_ATTEMPTS):
        response = await api.http.post("/staff/login/start", json=body, headers=headers)
        assert (response.status_code, code(response)) == (403, "second_step_missing")
    limited = await api.http.post("/staff/login/start", json=body, headers=headers)
    assert (limited.status_code, code(limited)) == (429, "rate_limited")


async def test_staff_sign_in_is_refused_from_any_other_site(api: Api) -> None:
    email, _ = await a_staff_member(api)
    body = {"email": email, "password": PASSWORD}
    for headers in ({}, {"Origin": CLIENT_ORIGIN}, {"Origin": "https://evil.example"}):
        response = await api.http.post("/staff/login/start", json=body, headers=headers)
        assert (response.status_code, code(response)) == (403, "forbidden")


async def test_wrong_guesses_are_limited_and_the_limit_passes(api: Api) -> None:
    phone, _ = await a_client(api)
    guess = {"phone": phone, "password": "wrong-password-1"}
    headers = {"X-Forwarded-For": "203.0.113.7"}
    for _ in range(ACCOUNT_FROM_ADDRESS_ATTEMPTS):
        response = await api.http.post("/client/login/start", json=guess, headers=headers)
        assert response.status_code == 401
    limited = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}, headers=headers
    )
    assert (limited.status_code, code(limited)) == (429, "rate_limited")
    assert 0 < int(limited.headers["Retry-After"]) <= 15 * 60 + 1
    # The owner, calling from somewhere else, is not locked out by those guesses.
    elsewhere = await api.http.post(
        "/client/login/start",
        json={"phone": phone, "password": PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.9"},
    )
    assert elsewhere.status_code == 200
    api.clock.advance(minutes=16)
    again = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}, headers=headers
    )
    assert again.status_code == 200


async def test_concurrent_guesses_cannot_overshoot_the_limit(api: Api) -> None:
    phone, _ = await a_client(api)
    guess = {"phone": phone, "password": "wrong-password-1"}
    headers = {"X-Forwarded-For": "203.0.113.40"}
    responses = await asyncio.gather(
        *(api.http.post("/client/login/start", json=guess, headers=headers) for _ in range(12))
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses.count(401) == ACCOUNT_FROM_ADDRESS_ATTEMPTS
    assert statuses.count(429) == 12 - ACCOUNT_FROM_ADDRESS_ATTEMPTS


async def test_a_right_password_clears_the_wrong_guess_count(api: Api) -> None:
    phone, _ = await a_client(api)
    headers = {"X-Forwarded-For": "203.0.113.50"}
    for _ in range(3):
        for password in ["wrong-password-1"] * (ACCOUNT_FROM_ADDRESS_ATTEMPTS - 1) + [PASSWORD]:
            response = await api.http.post(
                "/client/login/start", json={"phone": phone, "password": password}, headers=headers
            )
            assert response.status_code != 429


async def test_a_forwarded_address_is_believed_only_from_our_proxy(
    test_database: TestDatabase, app_database: Database
) -> None:
    app = create_app(test_database.settings("finvest_app"), app_database, FakeClock(), HASHER)
    phone = a_phone()
    await app.state.identity.create_client(phone=phone, email=None, password=PASSWORD)
    # This caller is not a trusted proxy, so a new X-Forwarded-For each time buys nothing.
    transport = httpx.ASGITransport(app=app, client=("192.0.2.77", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        statuses = [
            (
                await http.post(
                    "/client/login/start",
                    json={"phone": phone, "password": "wrong-password-1"},
                    headers={"X-Forwarded-For": f"203.0.113.{n}"},
                )
            ).status_code
            for n in range(ACCOUNT_FROM_ADDRESS_ATTEMPTS + 1)
        ]
    assert statuses == [401] * ACCOUNT_FROM_ADDRESS_ATTEMPTS + [429]


# The second step and sessions


async def start(api: Api, phone: str) -> str:
    challenge = await api.identity.start_login("client", phone, PASSWORD, address=None)
    return challenge.id


async def test_a_challenge_opens_one_session_once(api: Api) -> None:
    phone, client_id = await a_client(api)
    login_id = await start(api, phone)
    opened = await asyncio.gather(
        *(
            api.identity.open_session(login_id, "client", second_step="code", transport="token")
            for _ in range(4)
        ),
        return_exceptions=True,
    )
    sessions = [result for result in opened if not isinstance(result, BaseException)]
    refused = [result for result in opened if isinstance(result, ApplicationError)]
    assert len(sessions) == 1
    assert [error.code for error in refused] == ["expired"] * 3
    session = await api.identity.client_session(sessions[0].token)
    assert session is not None
    assert session.client_id == client_id


async def test_a_challenge_expires_and_belongs_to_its_audience(api: Api) -> None:
    phone, _ = await a_client(api)
    login_id = await start(api, phone)
    with pytest.raises(ApplicationError, match="expired"):
        await api.identity.open_session(
            login_id, "staff", second_step="passkey", transport="cookie"
        )
    api.clock.advance(minutes=5, seconds=1)
    with pytest.raises(ApplicationError, match="expired"):
        await api.identity.open_session(login_id, "client", second_step="code", transport="token")


async def test_a_code_restricts_the_account_for_a_day_and_a_passkey_does_not(api: Api) -> None:
    phone, _ = await a_client(api)
    with_passkey = await api.identity.open_session(
        await start(api, phone), "client", second_step="passkey", transport="token"
    )
    session = await api.identity.client_session(with_passkey.token)
    assert session is not None
    assert session.restricted_until is None

    with_code = await api.identity.open_session(
        await start(api, phone), "client", second_step="code", transport="token"
    )
    response = await api.http.get(
        "/client/session", headers={"Authorization": f"Bearer {with_code.token}"}
    )
    assert response.status_code == 200
    assert response.json()["phone"] == phone
    assert response.json()["restricted_until"] == "2026-01-02T00:00:00Z"
    api.clock.advance(hours=24, seconds=1)
    session = await api.identity.client_session(with_code.token)
    assert session is not None
    assert session.restricted_until is None


async def test_only_a_digest_of_the_token_is_stored(api: Api) -> None:
    _, client_id = await a_client(api)
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="signup", transport="token"
    )
    async with api.database.transaction() as session:
        stored = await session.scalar(
            text("SELECT token_hash FROM identity_sessions WHERE actor_id = :id"),
            {"id": client_id},
        )
    assert len(stored) == 32
    assert issued.token.encode() not in bytes(stored)


async def test_a_web_session_ends_when_idle_and_use_keeps_it_alive(api: Api) -> None:
    _, client_id = await a_client(api)
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["client"], issued.token)
    for _ in range(4):
        api.clock.advance(minutes=10)
        assert (await api.http.get("/client/session")).status_code == 200
    api.clock.advance(minutes=15, seconds=1)
    assert (await api.http.get("/client/session")).status_code == 401


async def test_a_session_ends_at_its_absolute_limit_however_busy(api: Api) -> None:
    _, staff_id = await a_staff_member(api)
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    for _ in range(12 * 60 // 20 - 1):
        api.clock.advance(minutes=20)
        assert (await api.http.get("/staff/session")).status_code == 200
    api.clock.advance(minutes=20)
    assert (await api.http.get("/staff/session")).status_code == 401


async def test_a_client_session_is_not_a_staff_session(api: Api) -> None:
    _, client_id = await a_client(api)
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    assert (await api.http.get("/staff/session")).status_code == 401
    # Staff are never given a bearer token, and one is never read for them.
    response = await api.http.get(
        "/staff/session", headers={"Authorization": f"Bearer {issued.token}"}
    )
    assert response.status_code == 401
    with pytest.raises(ApplicationError):
        await api.identity.issue_session(
            Actor("staff", client_id), second_step="passkey", transport="token"
        )


async def test_a_staff_cookie_sent_from_the_client_site_is_refused(api: Api) -> None:
    # The two sites share a registrable domain, so SameSite does not keep them apart. The
    # Origin check does, on reads as well as writes.
    _, staff_id = await a_staff_member(api)
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    refused = await api.http.get("/staff/session", headers={"Origin": CLIENT_ORIGIN})
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    allowed = await api.http.get("/staff/session", headers={"Origin": STAFF_ORIGIN})
    assert allowed.status_code == 200


async def test_the_database_refuses_a_staff_session_without_a_passkey_or_an_app(
    api: Api,
) -> None:
    _, staff_id = await a_staff_member(api)
    for second_step in ("code", "signup"):
        with pytest.raises(DBAPIError, match="ck_identity_sessions_staff_second_step"):
            await api.identity.issue_session(
                Actor("staff", staff_id),
                second_step=second_step,
                transport="cookie",
            )
    for second_step in ("passkey", "authenticator"):
        issued = await api.identity.issue_session(
            Actor("staff", staff_id),
            second_step=second_step,  # type: ignore[arg-type]
            transport="cookie",
        )
        assert issued.token


async def test_staff_session_lists_the_role_and_its_permissions(api: Api) -> None:
    _, staff_id = await a_staff_member(api, "support")
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    response = await api.http.get("/staff/session")
    assert response.json() == {
        "staff_id": staff_id,
        "name": "Ada Nakato",
        "role": "support",
        "permissions": [
            "overview.view",
            "support.view",
            "support.reply",
            "complaints.view",
            "complaints.manage",
            "clients.view",
            "email_suppressions.lift",
        ],
    }
    refused = await api.http.get("/staff/team-test")
    assert (refused.status_code, code(refused)) == (403, "forbidden")


async def test_a_permission_is_checked_on_the_server(api: Api) -> None:
    _, staff_id = await a_staff_member(api, "admin")
    assert (await api.http.get("/staff/team-test")).status_code == 401
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    assert (await api.http.get("/staff/team-test")).json() == {"staff_id": staff_id}


async def test_a_disabled_staff_member_loses_access_at_once(
    api: Api, migrator_database: Database
) -> None:
    _, staff_id = await a_staff_member(api)
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    assert (await api.http.get("/staff/session")).status_code == 200
    # Two admins: one proposes, the other approves, and access ends with the approval.
    proposer = Actor("staff", (await a_staff_member(api))[1])
    decider = Actor("staff", (await a_staff_member(api))[1])
    async with api.database.actor_transaction(proposer) as session:
        change = await team_changes.propose(
            session,
            proposer,
            team_changes.Proposed(kind="deactivate", reason="Left Finvest", staff_id=staff_id),
            api.clock,
        )
    async with api.database.actor_transaction(decider) as session:
        await team_changes.decide(
            session, decider, change.id, "approved", "Confirmed", api.clock, console_origin=None
        )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    assert (await api.http.get("/staff/session")).status_code == 401


async def test_sign_out_ends_the_session_and_clears_the_cookie(api: Api) -> None:
    _, staff_id = await a_staff_member(api)
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    api.http.cookies.set(COOKIES["staff"], issued.token)
    # A cookie on an unsafe request must come from the console's own origin.
    for headers in ({}, {"Origin": "https://evil.example"}):
        refused = await api.http.post("/staff/sign-out", headers=headers)
        assert (refused.status_code, code(refused)) == (403, "forbidden")
    assert (await api.http.get("/staff/session")).status_code == 200

    response = await api.http.post("/staff/sign-out", headers={"Origin": STAFF_ORIGIN})
    assert response.status_code == 204
    cleared = response.headers["set-cookie"]
    assert cleared.startswith(f'{COOKIES["staff"]}="";')
    assert "HttpOnly" in cleared
    assert "Secure" in cleared
    assert "SameSite=strict" in cleared
    assert await api.identity.staff_session(issued.token) is None
    # Signing out twice, or with no session, is not an error.
    api.http.cookies.clear()
    assert (
        await api.http.post("/staff/sign-out", headers={"Origin": STAFF_ORIGIN})
    ).status_code == 204


async def test_revoking_ends_every_session_of_one_person_only(api: Api) -> None:
    _, first = await a_client(api)
    _, second = await a_client(api)
    tokens = [
        (
            await api.identity.issue_session(
                Actor("client", client_id), second_step="passkey", transport="token"
            )
        ).token
        for client_id in (first, first, second)
    ]
    assert await api.identity.revoke_sessions(Actor("client", first), "password_changed") == 2
    assert await api.identity.client_session(tokens[0]) is None
    assert await api.identity.client_session(tokens[1]) is None
    assert await api.identity.client_session(tokens[2]) is not None


async def test_browsers_are_told_which_origins_may_call(api: Api) -> None:
    allowed = await api.http.options(
        "/staff/login/start",
        headers={"Origin": STAFF_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers["access-control-allow-origin"] == STAFF_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    refused = await api.http.options(
        "/staff/login/start",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in refused.headers


# Roles


async def test_the_worker_cannot_touch_sign_in_tables(worker_database: Database) -> None:
    for table in (
        "identity_staff",
        "identity_login_challenges",
        "identity_sessions",
        "identity_throttles",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.transaction() as session:
                await session.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))  # noqa: S608


async def test_the_worker_can_read_how_to_reach_a_client_and_nothing_more(
    api: Api, worker_database: Database
) -> None:
    phone, client_id = await a_client(api)
    async with worker_database.transaction() as session:
        contact = await client_contact(session, client_id)
        assert contact == ClientContact(phone, None, claimed=True)
        assert await client_contact(session, "-Nabc0000000000000Z9") is None
    for column in ("password_hash", "restricted_until", "created_at"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.transaction() as session:
                await session.execute(text(f"SELECT {column} FROM identity_clients"))  # noqa: S608
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.transaction() as session:
            await session.execute(
                text("UPDATE identity_clients SET status = 'closed' WHERE id = :id"),
                {"id": client_id},
            )


async def test_the_api_cannot_delete_accounts_or_sessions(api: Api) -> None:
    for table in ("identity_clients", "identity_staff", "identity_sessions"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with api.database.transaction() as session:
                await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608


async def test_the_api_can_change_only_the_account_columns_sign_in_needs(api: Api) -> None:
    _, staff_id = await a_staff_member(api, "support")
    _, client_id = await a_client(api)
    for statement, account in (
        ("UPDATE identity_staff SET email = 'other@example.com' WHERE id = :id", staff_id),
        ("UPDATE identity_clients SET email = 'other@example.com' WHERE id = :id", client_id),
        ("UPDATE identity_clients SET created_at = now() WHERE id = :id", client_id),
        ("UPDATE identity_clients SET status = 'closed' WHERE id = :id", client_id),
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with api.database.transaction() as session:
                await session.execute(text(statement), {"id": account})
    # A client's status changes only through the claim function, or through a lock or unlock
    # two people approved (migration 0152, and tests/integration/test_client_locks.py).
    # A role may change, but only through a change two admins approved (migration 0150, and
    # tests/integration/test_team.py).
    with pytest.raises(DBAPIError, match="approved change"):
        async with api.database.transaction() as session:
            await session.execute(
                text("UPDATE identity_staff SET role = 'admin' WHERE id = :id"), {"id": staff_id}
            )


async def test_nothing_personal_is_logged(api: Api, capsys: pytest.CaptureFixture[str]) -> None:
    phone, _ = await a_client(api)
    await api.http.post("/client/login/start", json={"phone": phone, "password": "wrong-pass-12"})
    await api.http.post("/client/login/start", json={"phone": phone, "password": PASSWORD})
    logged = capsys.readouterr()
    for secret in (phone, phone[4:], PASSWORD, "wrong-pass-12"):
        assert secret not in logged.out
        assert secret not in logged.err
