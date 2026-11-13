"""Accounts carried over from Finvest 1.0: created unclaimed, then claimed with a code."""

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
from app.modules.identity.codes import CLAIM_COMPLETIONS, MAX_TRIES, CodeService
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import (
    ClientContact,
    IdentityService,
    client_contact,
    create_unclaimed_client,
)
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
STAFF = Actor("staff", "-Nabc0000000000000S1")
_numbers = itertools.count(730_000_000)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    codes: CodeService
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
    clock = FakeClock()
    app = create_app(test_database.settings("finvest_app"), app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(
            http,
            app.state.identity,
            app.state.codes,
            clock,
            app_database,
            worker_database,
            migrator_database,
        )


def a_phone() -> str:
    return f"+256{next(_numbers)}"


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


async def imported(api: Api, phone: str | None = None) -> tuple[str, str]:
    phone = phone or a_phone()
    async with api.database.actor_transaction(STAFF) as session:
        client_id = await create_unclaimed_client(
            session, api.clock, STAFF, phone=phone, email="Ada@Example.com"
        )
    return phone, client_id


async def events(api: Api, client_id: str) -> list[tuple[str, str]]:
    async with api.owner.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT action, actor_kind FROM audit_events WHERE resource_id = :id "
                "AND resource_type = 'client' ORDER BY occurred_at, id"
            ),
            {"id": client_id},
        )
        return [(row.action, row.actor_kind) for row in rows]


async def test_an_imported_account_cannot_sign_in_until_it_is_claimed(api: Api) -> None:
    phone, client_id = await imported(api)
    # No password, so the password step finds nothing, and no reset is sent.
    refused = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": "x" * 12}
    )
    assert refused.status_code == 401
    await api.http.post("/client/password/reset/start", json={"phone": phone})
    assert await texted(api, phone) == []
    # The worker can still reach the client to remind them to claim.
    async with api.worker.transaction() as session:
        assert await client_contact(session, client_id) == ClientContact(
            phone, "ada@example.com", claimed=False
        )

    challenge = await api.codes.claim_start(client_id, address=None)
    [sent] = await texted(api, phone)
    claimed = await api.codes.claim_complete(challenge, sent, PASSWORD, "token")
    assert claimed.client_id == client_id
    opened = await api.identity.client_session(claimed.session.token)
    assert opened is not None
    assert opened.client_id == client_id
    # A code proves only the number, so withdrawals are limited for a day.
    assert opened.restricted_until is not None
    async with api.worker.transaction() as session:
        contact = await client_contact(session, client_id)
    assert contact is not None
    assert contact.claimed

    signed_in = await api.http.post(
        "/client/login/start", json={"phone": phone, "password": PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text
    assert await events(api, client_id) == [
        ("identity.client.imported", "staff"),
        ("identity.client.claimed", "client"),
    ]


async def test_a_claim_works_once(api: Api) -> None:
    phone, client_id = await imported(api)
    challenge = await api.codes.claim_start(client_id, address=None)
    [sent] = await texted(api, phone)
    await api.codes.claim_complete(challenge, sent, PASSWORD, "token")
    with pytest.raises(ApplicationError) as again:
        await api.codes.claim_complete(challenge, sent, PASSWORD, "token")
    assert again.value.code == "expired"
    with pytest.raises(ApplicationError) as restart:
        await api.codes.claim_start(client_id, address=None)
    assert restart.value.code == "not_found"


async def test_a_claim_passes_the_client_status_guard(api: Api) -> None:
    # Migration 0152 guards every change of a client's status. A claim is the one move it
    # leaves to identity_claim_client.
    phone, client_id = await imported(api)
    challenge = await api.codes.claim_start(client_id, address=None)
    [sent] = await texted(api, phone)
    await api.codes.claim_complete(challenge, sent, PASSWORD, "token")
    async with api.owner.transaction() as session:
        guards = await session.scalar(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'tr_identity_clients_status_guard'"
            )
        )
        status = await session.scalar(
            text("SELECT status FROM identity_clients WHERE id = :id"), {"id": client_id}
        )
    assert (guards, status) == (1, "active")


async def test_two_claims_at_once_make_one_password(api: Api) -> None:
    phone, client_id = await imported(api)
    first = await api.codes.claim_start(client_id, address=None)
    [first_code] = await texted(api, phone)
    # A second code replaces the first, so only one can win however they are timed.
    second = await api.codes.claim_start(client_id, address=None)
    second_code = (await texted(api, phone))[-1]
    results = await asyncio.gather(
        api.codes.claim_complete(first, first_code, PASSWORD, "token"),
        api.codes.claim_complete(second, second_code, PASSWORD, "token"),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert [event for event, _ in await events(api, client_id)].count(
        "identity.client.claimed"
    ) == 1


async def test_a_claim_code_allows_five_tries(api: Api) -> None:
    phone, client_id = await imported(api)
    challenge = await api.codes.claim_start(client_id, address=None)
    [sent] = await texted(api, phone)
    wrong = "000000" if sent != "000000" else "111111"
    for _ in range(MAX_TRIES):
        with pytest.raises(ApplicationError) as refused:
            await api.codes.claim_complete(challenge, wrong, PASSWORD, "token")
        assert refused.value.code == "invalid_code"
    with pytest.raises(ApplicationError) as spent:
        await api.codes.claim_complete(challenge, sent, PASSWORD, "token")
    assert spent.value.code == "expired"


async def test_a_weak_password_leaves_the_code_unspent(api: Api) -> None:
    phone, client_id = await imported(api)
    challenge = await api.codes.claim_start(client_id, address=None)
    [sent] = await texted(api, phone)
    with pytest.raises(ApplicationError) as weak:
        await api.codes.claim_complete(challenge, sent, phone, "token")
    assert weak.value.code in ("validation_failed", "leaked_password")
    assert await api.codes.claim_complete(challenge, sent, PASSWORD, "token")


async def test_a_number_that_has_an_account_is_not_imported_twice(api: Api) -> None:
    phone, _ = await imported(api)
    async with api.database.actor_transaction(STAFF) as session:
        with pytest.raises(ApplicationError) as taken:
            await create_unclaimed_client(session, api.clock, STAFF, phone=phone, email=None)
        assert taken.value.code == "phone_taken"
        # The transaction is still usable, so an import can note the problem and go on.
        assert await create_unclaimed_client(session, api.clock, STAFF, phone=a_phone(), email=None)


async def test_only_staff_import(api: Api) -> None:
    client = Actor("client", "-Nabc0000000000000C1")
    async with api.database.actor_transaction(client) as session:
        with pytest.raises(ValueError, match="staff"):
            await create_unclaimed_client(session, api.clock, client, phone=a_phone(), email=None)


async def test_the_api_claims_only_through_the_claim_function(api: Api) -> None:
    phone, client_id = await imported(api)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with api.database.transaction() as session:
            await session.execute(
                text("UPDATE identity_clients SET status = 'active' WHERE id = :id"),
                {"id": client_id},
            )
    claim = "SELECT identity_claim_client(:id, :code, 'h', now(), now())"
    challenge = await api.codes.claim_start(client_id, address=None)
    # Without a claim code spent in the same transaction, the function sets nothing.
    async with api.database.transaction() as session:
        assert not await session.scalar(text(claim), {"id": client_id, "code": challenge})
    async with api.database.transaction() as session:
        await session.execute(
            text("UPDATE identity_codes SET consumed_at = now() WHERE id = :id"),
            {"id": challenge},
        )
    async with api.database.transaction() as session:
        assert not await session.scalar(text(claim), {"id": client_id, "code": challenge})
    # Spent in the same transaction, it claims once and never takes a password away.
    second = await api.codes.claim_start(client_id, address=None)
    async with api.database.transaction() as session:
        await session.execute(
            text("UPDATE identity_codes SET consumed_at = now() WHERE id = :id"),
            {"id": second},
        )
        assert await session.scalar(text(claim), {"id": client_id, "code": second})
        assert not await session.scalar(text(claim), {"id": client_id, "code": second})
    assert phone


async def test_finishing_one_claim_is_limited(api: Api) -> None:
    phone, client_id = await imported(api)
    challenge = await api.codes.claim_start(client_id, address=None)
    for _ in range(CLAIM_COMPLETIONS):
        with pytest.raises(ApplicationError):
            await api.codes.claim_complete(challenge, "000000", phone, "token")
    with pytest.raises(ApplicationError) as limited:
        await api.codes.claim_complete(challenge, "000000", PASSWORD, "token")
    assert limited.value.code == "rate_limited"


async def test_a_claimed_account_never_becomes_unclaimed(
    api: Api, migrator_database: Database
) -> None:
    phone, client_id = await imported(api)
    challenge = await api.codes.claim_start(client_id, address=None)
    [sent] = await texted(api, phone)
    await api.codes.claim_complete(challenge, sent, PASSWORD, "token")
    with pytest.raises(DBAPIError, match="cannot become unclaimed"):
        async with migrator_database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_clients SET status = 'unclaimed', password_hash = NULL, "
                    "password_changed_at = NULL WHERE id = :id"
                ),
                {"id": client_id},
            )
