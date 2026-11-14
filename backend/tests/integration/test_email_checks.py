"""A client proves their email address with a one-time link, through the API."""

import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.email_checks import LINK_LIFETIME, LINKS_PER_CLIENT, VERIFY_PATH
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import (
    ACCOUNT_FROM_ADDRESS_ATTEMPTS,
    IdentityService,
    client_contact,
)
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
ORIGIN = "https://app.finvestug.com"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = itertools.count(750_000_000)


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
        update={"client_allowed_origins": [ORIGIN]}
    )
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, clock, app_database, worker_database, migrator_database)


async def a_client(api: Api, email: str | None = "set") -> tuple[str, str | None, dict[str, str]]:
    number = next(_numbers)
    address = f"client{number}@example.com" if email else None
    client_id = await api.identity.create_client(
        phone=f"+256{number}", email=address, password=PASSWORD
    )
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="token"
    )
    return client_id, address, {"Authorization": f"Bearer {issued.token}"}


async def links(api: Api, recipient: str) -> list[str]:
    async with api.worker.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT variables->>'link' AS link FROM notification_messages "
                "WHERE recipient = :r AND template = 'email_verification' ORDER BY id"
            ),
            {"r": recipient},
        )
        return [row.link for row in rows]


def token_of(link: str) -> str:
    assert link.startswith(f"{ORIGIN}{VERIFY_PATH}#")
    return link.split("#", 1)[1]


async def confirm(api: Api, token: str) -> httpx.Response:
    return await api.http.post("/client/email/verification/confirm", json={"token": token})


async def test_a_client_proves_their_address_with_the_link(api: Api) -> None:
    client_id, email, bearer = await a_client(api)
    assert email is not None
    before = await api.http.get("/client/session", headers=bearer)
    assert (before.json()["email"], before.json()["email_verified"]) == (email, False)
    sent = await api.http.post("/client/email/verification", headers=bearer)
    assert (sent.status_code, sent.json()) == (200, {"sent": True})
    [link] = await links(api, email)

    # The link works on any device, without a session.
    assert (await confirm(api, token_of(link))).status_code == 204
    after = await api.http.get("/client/session", headers=bearer)
    assert after.json()["email_verified"] is True
    async with api.worker.transaction() as session:
        contact = await client_contact(session, client_id)
    assert contact is not None
    assert contact.email_verified
    # It works once, and a proved address needs no new link.
    again = await confirm(api, token_of(link))
    assert again.status_code == 410
    assert (await api.http.post("/client/email/verification", headers=bearer)).json() == {
        "sent": False
    }
    async with api.owner.transaction() as session:
        actions = await session.scalars(
            text("SELECT action FROM audit_events WHERE resource_id = :id"), {"id": client_id}
        )
        assert "identity.email.verified" in list(actions)


async def test_a_newer_link_replaces_an_older_one_and_links_expire(api: Api) -> None:
    _, email, bearer = await a_client(api)
    assert email is not None
    await api.http.post("/client/email/verification", headers=bearer)
    await api.http.post("/client/email/verification", headers=bearer)
    old, new = await links(api, email)
    assert (await confirm(api, token_of(old))).status_code == 410
    api.clock.advance(seconds=(LINK_LIFETIME + timedelta(seconds=1)).total_seconds())
    assert (await confirm(api, token_of(new))).status_code == 410


async def test_only_a_digest_of_the_token_is_stored(api: Api) -> None:
    client_id, email, bearer = await a_client(api)
    assert email is not None
    await api.http.post("/client/email/verification", headers=bearer)
    [link] = await links(api, email)
    async with api.owner.transaction() as session:
        stored = await session.scalar(
            text("SELECT token_hash FROM identity_email_checks WHERE client_id = :id"),
            {"id": client_id},
        )
    assert token_of(link).encode() not in bytes(stored)
    assert len(bytes(stored)) == 32


async def test_a_link_proves_only_the_address_it_was_sent_to(api: Api) -> None:
    client_id, email, bearer = await a_client(api)
    assert email is not None
    await api.http.post("/client/email/verification", headers=bearer)
    [link] = await links(api, email)
    async with api.owner.transaction() as session:
        await session.execute(
            text("UPDATE identity_clients SET email = 'other@example.com' WHERE id = :id"),
            {"id": client_id},
        )
    assert (await confirm(api, token_of(link))).status_code == 410
    async with api.worker.transaction() as session:
        contact = await client_contact(session, client_id)
    assert contact is not None
    assert not contact.email_verified


async def test_no_address_no_link_and_requests_are_limited(api: Api) -> None:
    _, _, bearer = await a_client(api, email=None)
    missing = await api.http.post("/client/email/verification", headers=bearer)
    assert missing.status_code == 422
    _, _, bearer = await a_client(api)
    for _ in range(LINKS_PER_CLIENT):
        assert (
            await api.http.post("/client/email/verification", headers=bearer)
        ).status_code == 200
    limited = await api.http.post("/client/email/verification", headers=bearer)
    assert limited.status_code == 429
    unsigned = await api.http.post("/client/email/verification")
    assert unsigned.status_code == 401


async def test_the_api_cannot_mark_an_address_proved_itself(api: Api) -> None:
    client_id, _, _ = await a_client(api)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with api.database.transaction() as session:
            await session.execute(
                text("UPDATE identity_clients SET email_verified_at = now() WHERE id = :id"),
                {"id": client_id},
            )


async def test_a_new_address_is_never_taken_as_proved(api: Api) -> None:
    client_id, email, bearer = await a_client(api)
    assert email is not None
    await api.http.post("/client/email/verification", headers=bearer)
    [link] = await links(api, email)
    assert (await confirm(api, token_of(link))).status_code == 204
    # Whoever changes the address, and however, the proof goes with the old one.
    async with api.owner.transaction() as session:
        await session.execute(
            text(
                "UPDATE identity_clients SET email = 'new@example.com', "
                "email_verified_at = now() WHERE id = :id"
            ),
            {"id": client_id},
        )
    async with api.worker.transaction() as session:
        contact = await client_contact(session, client_id)
    assert contact is not None
    assert (contact.email, contact.email_verified) == ("new@example.com", False)
    # Changing something else keeps a proof.
    async with api.owner.transaction() as session:
        await session.execute(
            text("UPDATE identity_clients SET email_verified_at = now() WHERE id = :id"),
            {"id": client_id},
        )
        await session.execute(
            text("UPDATE identity_clients SET restricted_until = NULL WHERE id = :id"),
            {"id": client_id},
        )
    async with api.worker.transaction() as session:
        contact = await client_contact(session, client_id)
    assert contact is not None
    assert contact.email_verified


async def test_the_api_sees_only_the_links_of_the_client_it_acts_for(api: Api) -> None:
    client_id, _, bearer = await a_client(api)
    other_id, _, _ = await a_client(api)
    await api.http.post("/client/email/verification", headers=bearer)
    count = text("SELECT count(*) FROM identity_email_checks WHERE client_id = :id")
    async with api.database.actor_transaction(Actor("client", client_id)) as session:
        assert await session.scalar(count, {"id": client_id}) == 1
    for actor in (Actor("client", other_id), Actor("staff", "-Nabc0000000000000S1")):
        async with api.database.actor_transaction(actor) as session:
            assert await session.scalar(count, {"id": client_id}) == 0
    async with api.database.transaction() as session:
        assert await session.scalar(count, {"id": client_id}) == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with api.database.actor_transaction(Actor("client", other_id)) as session:
            await session.execute(
                text(
                    "INSERT INTO identity_email_checks (id, client_id, email, token_hash, "
                    "created_at, expires_at) VALUES ('-Nabc0000000000000A1', :id, "
                    "'a@example.com', decode(repeat('00', 32), 'hex'), now(), "
                    "now() + interval '1 day')"
                ),
                {"id": client_id},
            )


async def prove(api: Api, email: str, bearer: dict[str, str]) -> httpx.Response:
    await api.http.post("/client/email/verification", headers=bearer)
    link = (await links(api, email))[-1]
    return await confirm(api, token_of(link))


async def test_only_one_account_can_prove_an_address(api: Api) -> None:
    _, email, first = await a_client(api)
    assert email is not None
    number = next(_numbers)
    second_id = await api.identity.create_client(
        phone=f"+256{number}", email=email, password=PASSWORD
    )
    issued = await api.identity.issue_session(
        Actor("client", second_id), second_step="passkey", transport="token"
    )
    second = {"Authorization": f"Bearer {issued.token}"}
    assert (await prove(api, email, first)).status_code == 204

    taken = await prove(api, email, second)
    assert (taken.status_code, taken.json()["error"]["code"]) == (409, "email_taken")
    session = await api.http.get("/client/session", headers=second)
    assert session.json()["email_verified"] is False


async def login_by_email(api: Api, email: str, password: str = PASSWORD) -> httpx.Response:
    return await api.http.post("/client/login/start", json={"email": email, "password": password})


async def test_a_proved_address_signs_the_client_in(api: Api) -> None:
    _, email, bearer = await a_client(api)
    assert email is not None
    # Before the proof the address opens nothing, with the same answer as a wrong password.
    before = await login_by_email(api, email)
    assert (before.status_code, before.json()["error"]["code"]) == (401, "invalid_credentials")
    assert (await prove(api, email, bearer)).status_code == 204

    started = await login_by_email(api, f"  {email.upper()} ")
    assert started.status_code == 200
    assert started.json()["methods"][-1] == "code"
    wrong = await login_by_email(api, email, "not-the-password-at-all")
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "invalid_credentials")
    unknown = await login_by_email(api, "nobody-here@example.com")
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (401, "invalid_credentials")


async def test_login_takes_a_phone_or_an_email_not_both(api: Api) -> None:
    both = await api.http.post(
        "/client/login/start",
        json={"phone": "+256772000000", "email": "a@example.com", "password": PASSWORD},
    )
    neither = await api.http.post("/client/login/start", json={"password": PASSWORD})
    assert both.status_code == neither.status_code == 422


async def test_email_and_phone_share_one_budget_of_guesses(api: Api) -> None:
    client_id, email, bearer = await a_client(api)
    assert email is not None
    assert (await prove(api, email, bearer)).status_code == 204
    async with api.worker.transaction() as session:
        contact = await client_contact(session, client_id)
    assert contact is not None
    for attempt in range(ACCOUNT_FROM_ADDRESS_ATTEMPTS):
        if attempt % 2:
            await login_by_email(api, email, "wrong-password-guess")
        else:
            await api.http.post(
                "/client/login/start",
                json={"phone": contact.phone, "password": "wrong-password-guess"},
            )
    limited = await login_by_email(api, email)
    assert limited.status_code == 429


async def test_the_database_holds_one_proof_per_address_whoever_writes_it(api: Api) -> None:
    # What 0220 meets if two accounts proved one address before it ran: the index refuses it,
    # and the migration stops until one proof is cleared by hand.
    first, email, _ = await a_client(api)
    assert email is not None
    second = await api.identity.create_client(
        phone=f"+256{next(_numbers)}", email=email, password=PASSWORD
    )
    async with api.owner.transaction() as session:
        await session.execute(
            text("UPDATE identity_clients SET email_verified_at = now() WHERE id = :id"),
            {"id": first},
        )
    with pytest.raises(IntegrityError):
        async with api.owner.transaction() as session:
            await session.execute(
                text("UPDATE identity_clients SET email_verified_at = now() WHERE id = :id"),
                {"id": second},
            )
