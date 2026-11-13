"""Finding a client, their page, and locking or unlocking them with two people."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.identity import client_locks
from app.modules.identity.service import client_contact
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.kyc import verify

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def phone_of(api: Api, client: Actor) -> str:
    async with api.database.transaction() as session:
        return str(
            await session.scalar(
                text("SELECT phone FROM identity_clients WHERE id = :id"), {"id": client.id}
            )
        )


async def status_of(api: Api, client: Actor) -> str:
    async with api.database.transaction() as session:
        return str(
            await session.scalar(
                text("SELECT status FROM identity_clients WHERE id = :id"), {"id": client.id}
            )
        )


async def propose(api: Api, client: Actor, kind: str = "lock") -> httpx.Response:
    return await api.http.post(
        f"/staff/clients/{client.id}/locks", json={"kind": kind, "reason": "Phone reported stolen"}
    )


async def decide(api: Api, lock_id: str, decision: str = "approved") -> httpx.Response:
    return await api.http.post(
        f"/staff/clients/locks/{lock_id}/decision",
        json={"decision": decision, "reason": "Called the client back"},
    )


async def test_staff_find_a_client_by_id_or_mobile_number(api: Api) -> None:
    client = await api.client()
    phone = await phone_of(api, client)
    await api.staff("support")
    for query in (client.id, phone, "0" + phone[4:], phone[1:]):
        found = (await api.http.get("/staff/clients/search", params={"q": query})).json()
        assert [item["id"] for item in found["clients"]] == [client.id], query
    nobody = (await api.http.get("/staff/clients/search", params={"q": "+256700000001"})).json()
    assert nobody["clients"] == []


async def test_the_client_page_shows_what_each_role_may_see(api: Api) -> None:
    client = await api.client()
    await api.staff("support")
    page = (await api.http.get(f"/staff/clients/{client.id}")).json()
    assert page["account"]["status"] == "active"
    assert page["identity_check"] is None and page["verified_name"] is None
    assert page["owed"] == [] and page["locks"] == []
    assert page["complaints"] == [] and page["can_lock"] is False
    await api.staff("finance")
    page = (await api.http.get(f"/staff/clients/{client.id}")).json()
    assert page["complaints"] is None and page["conversation"] is None
    assert (await api.http.get("/staff/clients/-Nabc0000000000000Z9")).status_code == 404


async def test_client_cards_name_only_verified_clients_and_skip_unknown_ids(api: Api) -> None:
    verified = await api.client()
    await verify(api.database, verified, api.clock)
    unverified = await api.client()
    await api.staff("finance")
    params = {"ids": [str(verified.id), str(unverified.id), "-Nabc0000000000000Z9"]}
    response = await api.http.get("/staff/clients/cards", params=params)
    assert response.status_code == 200, response.text
    cards = {card["id"]: card for card in response.json()["cards"]}
    assert set(cards) == {verified.id, unverified.id}
    assert cards[verified.id]["verified_name"] and cards[verified.id]["account_number"]
    assert cards[unverified.id]["verified_name"] is None
    assert cards[unverified.id]["account_number"] is None
    assert set(cards[unverified.id]) == {
        "id",
        "verified_name",
        "account_number",
        "status",
        "created_at",
    }
    assert (await api.http.get("/staff/clients/cards")).status_code == 422
    too_many = {"ids": [verified.id] * 101}
    assert (await api.http.get("/staff/clients/cards", params=too_many)).status_code == 422


async def test_a_lock_needs_a_second_person_and_ends_the_clients_sessions(api: Api) -> None:
    client = await api.client()
    token = api.http.headers["Authorization"]
    assert (await api.http.get("/client/session")).status_code == 200
    approver = await api.staff("compliance")
    await api.staff("operations")
    proposed = await propose(api, client)
    assert proposed.status_code == 201, proposed.text
    lock = proposed.json()
    assert await status_of(api, client) == "active"
    assert (await decide(api, lock["id"])).status_code == 403

    await api.as_staff(approver)
    approved = await decide(api, lock["id"])
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert await status_of(api, client) == "locked"
    async with api.database.transaction() as session:
        assert await client_contact(session, str(client.id)) is None

    api.http.cookies.clear()
    api.http.headers.pop("Origin", None)
    api.http.headers["Authorization"] = token
    assert (await api.http.get("/client/session")).status_code == 401


async def test_unlocking_also_takes_two_people(api: Api) -> None:
    client = await api.client()
    first, second = await api.staff("operations"), await api.staff("compliance")
    await api.as_staff(first)
    lock = (await propose(api, client)).json()
    await api.as_staff(second)
    await decide(api, lock["id"])
    assert (await propose(api, client, "lock")).json()["error"]["fields"][0]["code"] == (
        "already_locked"
    )
    unlock = (await propose(api, client, "unlock")).json()
    await api.as_staff(first)
    assert (await decide(api, unlock["id"])).status_code == 200
    assert await status_of(api, client) == "active"
    page = (await api.http.get(f"/staff/clients/{client.id}")).json()
    assert [item["kind"] for item in page["locks"]] == ["unlock", "lock"]


async def test_withdrawn_and_rejected_proposals_change_nothing(api: Api) -> None:
    client = await api.client()
    other = await api.staff("compliance")
    await api.staff("operations")
    lock = (await propose(api, client)).json()
    assert (await decide(api, lock["id"], "withdrawn")).json()["status"] == "withdrawn"
    lock = (await propose(api, client)).json()
    assert (await propose(api, client)).status_code == 409
    await api.as_staff(other)
    assert (await decide(api, lock["id"], "withdrawn")).status_code == 403
    assert (await decide(api, lock["id"], "rejected")).json()["status"] == "rejected"
    assert await status_of(api, client) == "active"


async def test_support_and_finance_cannot_lock(api: Api) -> None:
    client = await api.client()
    for role in ("support", "finance"):
        await api.staff(role)
        assert (await propose(api, client)).status_code == 403


async def test_two_approvals_of_one_lock_at_once_apply_it_once(api: Api) -> None:
    client = await api.client()
    proposer = await api.staff("operations")
    async with api.database.actor_transaction(proposer) as session:
        lock = await client_locks.propose(
            session, proposer, str(client.id), "lock", "Fraud alert", api.clock
        )
    deciders = [await api.staff("compliance"), await api.staff("admin")]

    async def approve(decider: Actor) -> str:
        try:
            async with api.database.actor_transaction(decider) as session:
                await client_locks.decide(
                    session, decider, lock.id, "approved", "Confirmed", api.clock
                )
        except ApplicationError as error:
            return error.code
        return "approved"

    outcomes = await asyncio.gather(*(approve(decider) for decider in deciders))
    assert sorted(outcomes) == ["approved", "not_found"]
    assert await status_of(api, client) == "locked"


async def test_the_api_cannot_write_a_clients_status_in_any_direction(api: Api) -> None:
    client = await api.client()
    staff = await api.staff("admin")
    for status in ("locked", "closed", "active", "unclaimed"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with api.database.actor_transaction(staff) as session:
                await session.execute(
                    text("UPDATE identity_clients SET status = :status WHERE id = :id"),
                    {"status": status, "id": client.id},
                )
    # Applying a change that was never approved in this transaction does nothing.
    async with api.database.actor_transaction(staff) as session:
        assert not await session.scalar(
            text("SELECT identity_apply_client_lock(:id)"), {"id": "-Nabc0000000000000Z9"}
        )
    assert await status_of(api, client) == "active"


async def test_the_guard_refuses_any_other_status_move_even_for_the_owner(
    api: Api, migrator_database: Database
) -> None:
    client = await api.client()
    for status in ("locked", "closed"):
        with pytest.raises(DBAPIError, match="approved change"):
            async with migrator_database.transaction() as session:
                await session.execute(
                    text("UPDATE identity_clients SET status = :status WHERE id = :id"),
                    {"status": status, "id": client.id},
                )
    assert await status_of(api, client) == "active"


async def test_an_approved_lock_cannot_be_applied_in_a_later_transaction(api: Api) -> None:
    client = await api.client()
    maker, checker = await api.staff("operations"), await api.staff("compliance")
    async with api.database.actor_transaction(maker) as session:
        lock = await client_locks.propose(
            session, maker, str(client.id), "lock", "Fraud alert", api.clock
        )
    async with api.database.actor_transaction(checker) as session:
        await client_locks.decide(session, checker, lock.id, "approved", "Checked", api.clock)
    unlock_maker = await api.staff("operations")
    async with api.database.actor_transaction(unlock_maker) as session:
        unlock = await client_locks.propose(
            session, unlock_maker, str(client.id), "unlock", "Resolved", api.clock
        )
    # Replaying the old lock's approval in a new transaction changes nothing.
    async with api.database.actor_transaction(checker) as session:
        assert not await session.scalar(
            text("SELECT identity_apply_client_lock(:id)"), {"id": lock.id}
        )
    assert await status_of(api, client) == "locked"
    async with api.database.actor_transaction(checker) as session:
        await client_locks.decide(session, checker, unlock.id, "approved", "Checked", api.clock)
    assert await status_of(api, client) == "active"


async def test_the_database_refuses_one_person_deciding_their_own_lock(api: Api) -> None:
    client = await api.client()
    staff = await api.staff("operations")
    async with api.database.actor_transaction(staff) as session:
        lock = await client_locks.propose(
            session, staff, str(client.id), "lock", "Fraud alert", api.clock
        )
    with pytest.raises(DBAPIError, match="two_people"):
        async with api.database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE identity_client_locks SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x', decided_in = pg_current_xact_id() "
                    "WHERE id = :id"
                ),
                {"me": staff.id, "id": lock.id},
            )


async def test_each_step_is_audited(api: Api, migrator_database: Database) -> None:
    client = await api.client()
    other = await api.staff("compliance")
    await api.staff("operations")
    lock = (await propose(api, client)).json()
    await api.as_staff(other)
    await decide(api, lock["id"])
    async with migrator_database.transaction() as session:
        actions = (
            await session.scalars(
                text(
                    "SELECT action FROM audit_events WHERE resource_type = 'client_lock' "
                    "AND resource_id = :id ORDER BY occurred_at, id"
                ),
                {"id": lock["id"]},
            )
        ).all()
    assert actions == ["identity.client.lock_proposed", "identity.client.lock_approved"]
