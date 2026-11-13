"""Client signup, the code fallback at sign-in and password reset, through the API."""

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.api import COOKIES
from app.modules.identity.codes import MAX_TRIES, SENDS_PER_NUMBER
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
NEW_PASSWORD = "walnut-lantern-orbit"
ORIGIN = "https://app.finvestug.com"
BROWSER = {"Origin": ORIGIN}
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = itertools.count(720_000_000)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    worker: Database


@pytest.fixture
async def api(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"client_allowed_origins": [ORIGIN]}
    )
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    number = next(_numbers)
    address = {
        "X-Forwarded-For": f"10.{number // 65536 % 256}.{number // 256 % 256}.{number % 256}"
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api", headers=address
    ) as http:
        yield Api(http, app.state.identity, clock, worker_database)


def a_phone() -> str:
    return f"+256{next(_numbers)}"


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def texted(api: Api, recipient: str) -> list[str]:
    """The codes queued for a number or address, oldest first, as the worker would send them."""
    async with api.worker.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT variables->>'code' AS code FROM notification_messages "
                "WHERE recipient = :recipient ORDER BY id"
            ),
            {"recipient": recipient},
        )
        return [row.code for row in rows]


def wrong(right: str) -> str:
    return "000000" if right != "000000" else "111111"


async def sign_up(api: Api, phone: str, headers: dict[str, str] | None = None) -> httpx.Response:
    started = await api.http.post("/client/signup/start", json={"phone": phone}, headers=headers)
    assert started.status_code == 200, started.text
    confirmed = await api.http.post(
        "/client/signup/confirm",
        json={
            "challenge_id": started.json()["challenge_id"],
            "code": (await texted(api, phone))[-1],
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return await api.http.post(
        "/client/signup/complete",
        json={"ticket": confirmed.json()["ticket"], "password": PASSWORD},
        headers=headers,
    )


# Signup


async def test_a_client_signs_up_with_a_texted_code_and_a_password(api: Api) -> None:
    phone = a_phone()
    started = await api.http.post(
        "/client/signup/start", json={"phone": "0" + phone[4:], "email": "Ada@Example.com"}
    )
    assert started.json()["phone"] == phone
    [sent] = await texted(api, phone)
    assert len(sent) == 6
    assert sent.isdigit()
    confirmed = await api.http.post(
        "/client/signup/confirm",
        json={"challenge_id": started.json()["challenge_id"], "code": sent},
    )
    assert confirmed.json()["phone"] == phone
    completed = await api.http.post(
        "/client/signup/complete",
        json={"ticket": confirmed.json()["ticket"], "password": PASSWORD},
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    # The phone app is given a token. A new account is not restricted.
    assert body["session"]["phone"] == phone
    assert body["session"]["restricted_until"] is None
    bearer = {"Authorization": f"Bearer {body['token']}"}
    assert (await api.http.get("/client/session", headers=bearer)).status_code == 200
    # The ticket made one account and is spent.
    again = await api.http.post(
        "/client/signup/complete",
        json={"ticket": confirmed.json()["ticket"], "password": PASSWORD},
    )
    assert (again.status_code, code(again)) == (410, "expired")


async def test_signing_up_in_a_browser_sets_a_cookie(api: Api) -> None:
    completed = await sign_up(api, a_phone(), BROWSER)
    assert completed.json()["token"] is None
    assert COOKIES["client"] in completed.headers["set-cookie"]
    assert (await api.http.get("/client/session")).status_code == 200


async def test_only_the_owner_of_a_number_learns_it_has_an_account(api: Api) -> None:
    phone = a_phone()
    assert (await sign_up(api, phone)).status_code == 200
    # Starting again looks exactly like a new number, and a code is still texted.
    started = await api.http.post("/client/signup/start", json={"phone": phone})
    assert started.status_code == 200
    assert set(started.json()) == {"challenge_id", "phone"}
    codes = await texted(api, phone)
    assert len(codes) == 2
    challenge = started.json()["challenge_id"]
    guess = await api.http.post(
        "/client/signup/confirm", json={"challenge_id": challenge, "code": wrong(codes[-1])}
    )
    assert (guess.status_code, code(guess)) == (401, "invalid_code")
    proved = await api.http.post(
        "/client/signup/confirm", json={"challenge_id": challenge, "code": codes[-1]}
    )
    assert (proved.status_code, code(proved)) == (409, "phone_taken")


async def test_a_code_allows_five_tries_and_then_stops(api: Api) -> None:
    phone = a_phone()
    started = await api.http.post("/client/signup/start", json={"phone": phone})
    challenge = started.json()["challenge_id"]
    [sent] = await texted(api, phone)
    guesses = await asyncio.gather(
        *(
            api.http.post(
                "/client/signup/confirm", json={"challenge_id": challenge, "code": wrong(sent)}
            )
            for _ in range(MAX_TRIES + 3)
        )
    )
    statuses = sorted(response.status_code for response in guesses)
    assert statuses == [401] * MAX_TRIES + [410] * 3
    # Out of tries: even the right code no longer works.
    late = await api.http.post(
        "/client/signup/confirm", json={"challenge_id": challenge, "code": sent}
    )
    assert (late.status_code, code(late)) == (410, "expired")


async def test_a_code_expires_and_works_once(api: Api) -> None:
    phone = a_phone()
    first = await api.http.post("/client/signup/start", json={"phone": phone})
    api.clock.advance(minutes=10, seconds=1)
    late = await api.http.post(
        "/client/signup/confirm",
        json={"challenge_id": first.json()["challenge_id"], "code": (await texted(api, phone))[-1]},
    )
    assert (late.status_code, code(late)) == (410, "expired")
    second = await api.http.post("/client/signup/start", json={"phone": phone})
    body = {
        "challenge_id": second.json()["challenge_id"],
        "code": (await texted(api, phone))[-1],
    }
    assert (await api.http.post("/client/signup/confirm", json=body)).status_code == 200
    again = await api.http.post("/client/signup/confirm", json=body)
    assert (again.status_code, code(again)) == (410, "expired")


async def test_only_a_digest_of_the_code_is_kept_by_sign_in(
    api: Api, app_database: Database
) -> None:
    phone = a_phone()
    started = await api.http.post("/client/signup/start", json={"phone": phone})
    [sent] = await texted(api, phone)
    async with app_database.transaction() as session:
        stored = bytes(
            await session.scalar(
                text("SELECT code_hash FROM identity_codes WHERE id = :id"),
                {"id": started.json()["challenge_id"]},
            )
        )
    assert len(stored) == 32
    assert sent.encode() not in stored


async def test_a_weak_password_keeps_the_ticket(api: Api) -> None:
    phone = a_phone()
    started = await api.http.post("/client/signup/start", json={"phone": phone})
    confirmed = await api.http.post(
        "/client/signup/confirm",
        json={
            "challenge_id": started.json()["challenge_id"],
            "code": (await texted(api, phone))[-1],
        },
    )
    ticket = confirmed.json()["ticket"]
    for password, problem, error in (
        ("short", "too_short", "validation_failed"),
        ("password123", "leaked_password", "leaked_password"),
        (f"{phone[1:]}ab", "leaked_password", "leaked_password"),
    ):
        weak = await api.http.post(
            "/client/signup/complete", json={"ticket": ticket, "password": password}
        )
        assert (weak.status_code, code(weak)) == (422, error)
        assert weak.json()["error"]["fields"] == [
            {"field_path": ["body", "password"], "code": problem}
        ]
    good = await api.http.post(
        "/client/signup/complete", json={"ticket": ticket, "password": PASSWORD}
    )
    assert good.status_code == 200


async def test_two_people_finishing_with_one_number_make_one_account(api: Api) -> None:
    phone = a_phone()
    tickets = []
    for _ in range(2):
        started = await api.http.post("/client/signup/start", json={"phone": phone})
        confirmed = await api.http.post(
            "/client/signup/confirm",
            json={
                "challenge_id": started.json()["challenge_id"],
                "code": (await texted(api, phone))[-1],
            },
        )
        tickets.append(confirmed.json()["ticket"])
    responses = await asyncio.gather(
        *(
            api.http.post("/client/signup/complete", json={"ticket": t, "password": PASSWORD})
            for t in tickets
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_codes_to_one_number_are_limited(api: Api) -> None:
    phone = a_phone()
    for _ in range(SENDS_PER_NUMBER):
        assert (
            await api.http.post("/client/signup/start", json={"phone": phone})
        ).status_code == 200
    limited = await api.http.post("/client/signup/start", json={"phone": phone})
    assert (limited.status_code, code(limited)) == (429, "rate_limited")
    assert len(await texted(api, phone)) == SENDS_PER_NUMBER


async def test_a_bad_number_is_refused_before_anything_is_sent(api: Api) -> None:
    response = await api.http.post("/client/signup/start", json={"phone": "12345"})
    assert (response.status_code, code(response)) == (422, "validation_failed")
    assert response.json()["error"]["fields"] == [
        {"field_path": ["body", "phone"], "code": "invalid"}
    ]


# The sign-in fallback


async def a_client(api: Api, email: str | None = None) -> tuple[str, str]:
    phone = a_phone()
    return phone, await api.identity.create_client(phone=phone, email=email, password=PASSWORD)


async def start_login(api: Api, phone: str, headers: dict[str, str] | None = None) -> str:
    started = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}, headers=headers
    )
    assert started.status_code == 200, started.text
    return str(started.json()["login_id"])


async def test_a_code_finishes_sign_in_and_restricts_the_account(api: Api) -> None:
    phone, client_id = await a_client(api)
    login_id = await start_login(api, phone, BROWSER)
    sent = await api.http.post(
        "/client/login/code/send", json={"login_id": login_id}, headers=BROWSER
    )
    assert sent.status_code == 204
    [texted_code] = await texted(api, phone)
    verified = await api.http.post(
        "/client/login/code/verify",
        json={"login_id": login_id, "code": texted_code},
        headers=BROWSER,
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["token"] is None
    assert verified.json()["session"] == {
        "client_id": client_id,
        "phone": phone,
        "restricted_until": "2026-01-02T00:00:00Z",
    }
    assert COOKIES["client"] in verified.headers["set-cookie"]


async def test_a_code_alone_never_signs_in(api: Api) -> None:
    phone, _ = await a_client(api)
    # No password step, so there is no sign-in to send a code for.
    response = await api.http.post("/client/login/code/send", json={"login_id": "-" * 20})
    assert response.status_code in (410, 422)
    assert await texted(api, phone) == []


async def test_a_wrong_code_does_not_sign_in_and_a_new_code_replaces_the_old(api: Api) -> None:
    phone, _ = await a_client(api)
    login_id = await start_login(api, phone)
    await api.http.post("/client/login/code/send", json={"login_id": login_id})
    await api.http.post("/client/login/code/send", json={"login_id": login_id})
    first, second = await texted(api, phone)
    refused = await api.http.post(
        "/client/login/code/verify", json={"login_id": login_id, "code": wrong(second)}
    )
    assert (refused.status_code, code(refused)) == (401, "invalid_code")
    if first != second:
        old = await api.http.post(
            "/client/login/code/verify", json={"login_id": login_id, "code": first}
        )
        assert (old.status_code, code(old)) == (401, "invalid_code")
    good = await api.http.post(
        "/client/login/code/verify", json={"login_id": login_id, "code": second}
    )
    assert good.status_code == 200
    assert good.json()["token"]


async def test_a_code_for_one_sign_in_does_not_finish_another(api: Api) -> None:
    phone, _ = await a_client(api)
    other_phone, _ = await a_client(api)
    login_id = await start_login(api, phone)
    other_login = await start_login(api, other_phone)
    await api.http.post("/client/login/code/send", json={"login_id": login_id})
    [sent] = await texted(api, phone)
    response = await api.http.post(
        "/client/login/code/verify", json={"login_id": other_login, "code": sent}
    )
    assert (response.status_code, code(response)) == (410, "expired")


async def test_the_code_goes_by_email_only_when_the_client_has_an_address(api: Api) -> None:
    address = f"client{next(_numbers)}@example.com"
    phone, _ = await a_client(api, address)
    login_id = await start_login(api, phone)
    await api.http.post("/client/login/code/send", json={"login_id": login_id, "channel": "email"})
    assert await texted(api, phone) == []
    [emailed] = await texted(api, address)
    assert (
        await api.http.post(
            "/client/login/code/verify", json={"login_id": login_id, "code": emailed}
        )
    ).status_code == 200

    no_email_phone, _ = await a_client(api)
    login_id = await start_login(api, no_email_phone)
    await api.http.post("/client/login/code/send", json={"login_id": login_id, "channel": "email"})
    assert len(await texted(api, no_email_phone)) == 1


async def test_sends_for_one_sign_in_are_limited(api: Api) -> None:
    phone, _ = await a_client(api)
    login_id = await start_login(api, phone)
    for _ in range(3):
        sent = await api.http.post("/client/login/code/send", json={"login_id": login_id})
        assert sent.status_code == 204
    limited = await api.http.post("/client/login/code/send", json={"login_id": login_id})
    assert (limited.status_code, code(limited)) == (429, "rate_limited")


# Password reset


async def test_a_reset_sets_the_password_ends_sessions_and_restricts(api: Api) -> None:
    phone, client_id = await a_client(api)
    open_before = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="token"
    )
    started = await api.http.post("/client/password/reset/start", json={"phone": phone})
    [sent] = await texted(api, phone)
    done = await api.http.post(
        "/client/password/reset/complete",
        json={
            "challenge_id": started.json()["challenge_id"],
            "code": sent,
            "password": NEW_PASSWORD,
        },
    )
    assert done.status_code == 204, done.text
    assert "set-cookie" not in done.headers
    assert await api.identity.client_session(open_before.token) is None

    old = await api.http.post("/client/login/start", json={"phone": phone, "password": PASSWORD})
    assert old.status_code == 401
    new = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": NEW_PASSWORD}
    )
    assert new.status_code == 200
    # Even a later sign-in with a stronger second step stays restricted for the day.
    issued = await api.identity.open_session(
        new.json()["login_id"], "client", second_step="passkey", transport="token"
    )
    session = await api.identity.client_session(issued.token)
    assert session is not None
    assert session.restricted_until == api.clock.now().replace(day=2)


async def test_a_reset_for_an_unknown_number_looks_the_same_and_sends_nothing(api: Api) -> None:
    known, _ = await a_client(api)
    unknown = a_phone()
    answers = [
        await api.http.post("/client/password/reset/start", json={"phone": phone})
        for phone in (known, unknown)
    ]
    assert [a.status_code for a in answers] == [200, 200]
    assert [set(a.json()) for a in answers] == [{"challenge_id"}, {"challenge_id"}]
    assert await texted(api, unknown) == []
    guess = await api.http.post(
        "/client/password/reset/complete",
        json={
            "challenge_id": answers[1].json()["challenge_id"],
            "code": "123456",
            "password": NEW_PASSWORD,
        },
    )
    assert (guess.status_code, code(guess)) == (401, "invalid_code")


async def test_a_wrong_reset_code_changes_nothing(api: Api) -> None:
    phone, _ = await a_client(api)
    started = await api.http.post("/client/password/reset/start", json={"phone": phone})
    [sent] = await texted(api, phone)
    body = {"challenge_id": started.json()["challenge_id"], "password": NEW_PASSWORD}
    refused = await api.http.post(
        "/client/password/reset/complete", json={**body, "code": wrong(sent)}
    )
    assert (refused.status_code, code(refused)) == (401, "invalid_code")
    weak = await api.http.post(
        "/client/password/reset/complete", json={**body, "code": sent, "password": "short"}
    )
    assert (weak.status_code, code(weak)) == (422, "validation_failed")
    still = await api.http.post("/client/login/start", json={"phone": phone, "password": PASSWORD})
    assert still.status_code == 200


async def test_a_signup_code_cannot_reset_a_password(api: Api) -> None:
    phone, _ = await a_client(api)
    started = await api.http.post("/client/signup/start", json={"phone": phone})
    [sent] = await texted(api, phone)
    response = await api.http.post(
        "/client/password/reset/complete",
        json={
            "challenge_id": started.json()["challenge_id"],
            "code": sent,
            "password": NEW_PASSWORD,
        },
    )
    assert (response.status_code, code(response)) == (410, "expired")


async def test_no_code_or_number_is_logged(api: Api, capsys: pytest.CaptureFixture[str]) -> None:
    phone = a_phone()
    await sign_up(api, phone)
    [sent] = await texted(api, phone)
    logged = capsys.readouterr()
    for secret in (phone, phone[4:], sent, PASSWORD):
        assert secret not in logged.out
        assert secret not in logged.err
