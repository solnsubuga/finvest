"""Changing a client's mobile number takes two members of staff, through the API."""

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
from app.modules.identity.phone_changes import PROPOSAL_LIFETIME
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
CONSOLE = "https://admin.finvestug.com"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = itertools.count(740_000_000)


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
        update={"staff_allowed_origins": [CONSOLE]}
    )
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
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


async def propose(api: Api, staff: dict[str, str], client_id: str, phone: str) -> httpx.Response:
    return await api.http.post(
        f"/staff/clients/{client_id}/phone-changes",
        json={"phone": phone, "reason": "Client lost their SIM; checked in branch with ID"},
        headers=staff,
    )


async def queued(api: Api, recipient: str) -> list[str]:
    async with api.worker.transaction() as session:
        rows = await session.execute(
            text("SELECT template FROM notification_messages WHERE recipient = :r ORDER BY id"),
            {"r": recipient},
        )
        return [row.template for row in rows]


async def current_phone(api: Api, client_id: str) -> str:
    async with api.owner.transaction() as session:
        phone: str = await session.scalar(
            text("SELECT phone FROM identity_clients WHERE id = :id"), {"id": client_id}
        )
        return phone


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def test_a_second_person_approves_and_the_number_changes(api: Api) -> None:
    maker, checker = await a_staff_member(api), await a_staff_member(api)
    old, email, client_id = await a_client(api)
    signed_in = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="token"
    )
    new = a_phone()
    proposed = await propose(api, maker, client_id, "0" + new[4:])
    assert proposed.status_code == 201, proposed.text
    change_id = proposed.json()["id"]
    # Nothing changes on a proposal alone.
    assert await current_phone(api, client_id) == old
    listed = await api.http.get("/staff/phone-changes", headers=checker)
    assert [item["id"] for item in listed.json()["items"]] == [change_id]
    assert listed.json()["items"][0]["new_phone"] == new

    approved = await api.http.post(f"/staff/phone-changes/{change_id}/approval", headers=checker)
    assert approved.status_code == 204, approved.text
    assert await current_phone(api, client_id) == new
    # The client is signed out everywhere and limited for a day, and told at the old number
    # and the email on file.
    assert await api.identity.client_session(signed_in.token) is None
    assert await queued(api, old) == ["phone_changed"]
    assert await queued(api, email) == ["phone_changed"]
    async with api.owner.transaction() as session:
        restricted = await session.scalar(
            text("SELECT restricted_until FROM identity_clients WHERE id = :id"),
            {"id": client_id},
        )
        actions = (
            await session.execute(
                text(
                    "SELECT action, actor_id FROM audit_events WHERE resource_id = :id "
                    "AND action LIKE 'identity.client.phone%' ORDER BY occurred_at, id"
                ),
                {"id": client_id},
            )
        ).all()
    assert restricted is not None
    assert [(row.action, row.actor_id) for row in actions] == [
        ("identity.client.phone_change_proposed", maker["X-Staff-Id"]),
        ("identity.client.phone_changed", checker["X-Staff-Id"]),
    ]
    again = await api.http.post(f"/staff/phone-changes/{change_id}/approval", headers=checker)
    assert again.status_code == 404


async def test_nobody_approves_their_own_proposal(api: Api) -> None:
    maker = await a_staff_member(api)
    old, _, client_id = await a_client(api)
    change_id = (await propose(api, maker, client_id, a_phone())).json()["id"]
    own = await api.http.post(f"/staff/phone-changes/{change_id}/approval", headers=maker)
    assert (own.status_code, code(own)) == (403, "forbidden")
    assert await current_phone(api, client_id) == old


async def test_the_database_refuses_one_person_twice_and_a_change_without_approval(
    api: Api,
) -> None:
    maker = await a_staff_member(api)
    _, _, client_id = await a_client(api)
    change_id = (await propose(api, maker, client_id, a_phone())).json()["id"]
    actor = Actor("staff", maker["X-Staff-Id"])
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(actor) as session:
            await session.execute(
                text(
                    "UPDATE identity_phone_changes SET status = 'approved', decided_by = :me, "
                    "decided_at = now() WHERE id = :id"
                ),
                {"me": actor.id, "id": change_id},
            )
    # The function changes nothing without an approval made in the same transaction.
    with pytest.raises(DBAPIError, match="approval in the same transaction"):
        async with api.database.actor_transaction(actor) as session:
            await session.execute(
                text("SELECT identity_apply_phone_change(:id, now())"), {"id": change_id}
            )
    # And the API cannot change a number directly.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with api.database.transaction() as session:
            await session.execute(
                text("UPDATE identity_clients SET phone = '+256700000001' WHERE id = :id"),
                {"id": client_id},
            )


async def test_an_approval_cannot_be_replayed_in_a_later_transaction(api: Api) -> None:
    maker, checker = await a_staff_member(api), await a_staff_member(api)
    _, _, client_id = await a_client(api)
    change_id = (await propose(api, maker, client_id, a_phone())).json()["id"]
    assert (
        await api.http.post(f"/staff/phone-changes/{change_id}/approval", headers=checker)
    ).status_code == 204
    actor = Actor("staff", checker["X-Staff-Id"])
    with pytest.raises(DBAPIError, match="approval in the same transaction"):
        async with api.database.actor_transaction(actor) as session:
            await session.execute(
                text("SELECT identity_apply_phone_change(:id, now())"), {"id": change_id}
            )


async def test_a_rejection_needs_a_note_and_changes_nothing(api: Api) -> None:
    maker, checker = await a_staff_member(api), await a_staff_member(api)
    old, _, client_id = await a_client(api)
    change_id = (await propose(api, maker, client_id, a_phone())).json()["id"]
    empty = await api.http.post(
        f"/staff/phone-changes/{change_id}/rejection", json={"note": "  "}, headers=checker
    )
    assert empty.status_code == 422
    rejected = await api.http.post(
        f"/staff/phone-changes/{change_id}/rejection",
        json={"note": "Could not confirm who called"},
        headers=checker,
    )
    assert rejected.status_code == 204
    assert await current_phone(api, client_id) == old
    assert await queued(api, old) == []
    # A new proposal can follow a decided one.
    assert (await propose(api, maker, client_id, a_phone())).status_code == 201


async def test_one_open_proposal_per_client_and_numbers_in_use_are_refused(api: Api) -> None:
    maker = await a_staff_member(api)
    taken, _, _ = await a_client(api)
    _, _, client_id = await a_client(api)
    clash = await propose(api, maker, client_id, taken)
    assert (clash.status_code, code(clash)) == (409, "phone_taken")
    assert (await propose(api, maker, client_id, a_phone())).status_code == 201
    second = await propose(api, maker, client_id, a_phone())
    assert (second.status_code, code(second)) == (409, "conflict")
    missing = await propose(api, maker, "-Nabc0000000000000Z9", a_phone())
    assert missing.status_code == 404


async def test_a_number_given_away_before_approval_is_refused(api: Api) -> None:
    maker, checker = await a_staff_member(api), await a_staff_member(api)
    _, _, first = await a_client(api)
    _, _, second = await a_client(api)
    wanted = a_phone()
    one = (await propose(api, maker, first, wanted)).json()["id"]
    two = (await propose(api, maker, second, wanted)).json()["id"]
    assert (
        await api.http.post(f"/staff/phone-changes/{one}/approval", headers=checker)
    ).status_code == 204
    refused = await api.http.post(f"/staff/phone-changes/{two}/approval", headers=checker)
    assert (refused.status_code, code(refused)) == (409, "phone_taken")
    # The refused approval left the proposal open.
    listed = await api.http.get("/staff/phone-changes", headers=checker)
    assert two in [item["id"] for item in listed.json()["items"]]


async def test_two_approvals_at_once_change_the_number_once(api: Api) -> None:
    maker = await a_staff_member(api)
    checkers = [await a_staff_member(api), await a_staff_member(api)]
    old, _, client_id = await a_client(api)
    change_id = (await propose(api, maker, client_id, a_phone())).json()["id"]
    results = await asyncio.gather(
        *(
            api.http.post(f"/staff/phone-changes/{change_id}/approval", headers=checker)
            for checker in checkers
        )
    )
    assert sorted(result.status_code for result in results) == [204, 404]
    assert await queued(api, old) == ["phone_changed"]


async def test_an_old_proposal_lapses(api: Api) -> None:
    maker = await a_staff_member(api)
    _, _, client_id = await a_client(api)
    change_id = (await propose(api, maker, client_id, a_phone())).json()["id"]
    api.clock.advance(seconds=PROPOSAL_LIFETIME.total_seconds())
    checker = await a_staff_member(api)
    late = await api.http.post(f"/staff/phone-changes/{change_id}/approval", headers=checker)
    assert (late.status_code, code(late)) == (410, "expired")
    listed = await api.http.get("/staff/phone-changes", headers=checker)
    assert listed.json()["items"] == []


async def test_only_staff_with_the_permission_may_propose_or_decide(api: Api) -> None:
    support = await a_staff_member(api, "support")
    maker = await a_staff_member(api)
    _, _, client_id = await a_client(api)
    assert (await propose(api, support, client_id, a_phone())).status_code == 403
    change_id = (await propose(api, maker, client_id, a_phone())).json()["id"]
    denied = await api.http.post(f"/staff/phone-changes/{change_id}/approval", headers=support)
    assert denied.status_code == 403
    assert (await api.http.get("/staff/phone-changes", headers=support)).status_code == 403
