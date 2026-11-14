"""A client freezes their own account, and two members of staff unfreeze it."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.identity import freezes
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def status_of(api: Api, client: Actor) -> str:
    async with api.database.transaction() as session:
        return str(
            await session.scalar(
                text("SELECT status FROM identity_clients WHERE id = :id"), {"id": client.id}
            )
        )


async def test_freezing_locks_the_account_and_ends_every_session(api: Api) -> None:
    client = await api.client()
    frozen = await api.http.post("/client/account-freeze", json={"reason": "Lost my phone"})
    assert frozen.status_code == 201, frozen.text
    assert "frozen_at" in frozen.json()
    assert await status_of(api, client) == "locked"
    assert (await api.http.get("/client/session")).status_code == 401
    async with api.database.transaction() as session:
        live = await session.scalar(
            text(
                "SELECT count(*) FROM identity_sessions WHERE actor_kind = 'client' "
                "AND actor_id = :id AND revoked_at IS NULL"
            ),
            {"id": client.id},
        )
        reasons = (
            await session.execute(
                text(
                    "SELECT DISTINCT revoked_reason FROM identity_sessions "
                    "WHERE actor_kind = 'client' AND actor_id = :id"
                ),
                {"id": client.id},
            )
        ).scalars()
        assert live == 0 and list(reasons) == ["frozen"]
    staff = await api.staff("compliance")
    async with api.database.actor_transaction(staff) as session:
        actions = (
            await session.execute(
                text(
                    "SELECT action, actor_kind FROM audit_events "
                    "WHERE resource_type = 'client_freeze' AND actor_id = :id"
                ),
                {"id": client.id},
            )
        ).all()
        assert [tuple(row) for row in actions] == [("identity.client.frozen", "client")]


async def test_staff_see_the_freeze_and_two_of_them_unfreeze(api: Api) -> None:
    client = await api.client()
    await api.http.post("/client/account-freeze", json={"reason": None})
    proposer = await api.staff("operations")
    page = (await api.http.get(f"/staff/clients/{client.id}")).json()
    assert page["account"]["status"] == "locked"
    assert [item["reason"] for item in page["freezes"]] == [None]
    proposed = await api.http.post(
        f"/staff/clients/{client.id}/locks",
        json={"kind": "unlock", "reason": "Client called from a new phone, checked their ID"},
    )
    assert proposed.status_code == 201, proposed.text
    await api.staff("admin")
    approved = await api.http.post(
        f"/staff/clients/locks/{proposed.json()['id']}/decision",
        json={"decision": "approved", "reason": "Called the client back"},
    )
    assert approved.status_code == 200, approved.text
    assert await status_of(api, client) == "active"
    assert proposer.id


async def test_a_frozen_or_closed_account_cannot_be_frozen_again(
    api: Api, app_database: Database
) -> None:
    client = await api.client()
    await api.http.post("/client/account-freeze", json={})
    with pytest.raises(ApplicationError) as refused:
        async with app_database.actor_transaction(client) as session:
            await freezes.freeze(session, client, None, FakeClock())
    assert refused.value.code == "conflict"
    async with app_database.actor_transaction(client) as session:
        found = await freezes.freezes(session, str(client.id))
    assert len(found) == 1


async def test_a_long_reason_is_refused(api: Api) -> None:
    client = await api.client()
    refused = await api.http.post("/client/account-freeze", json={"reason": "x" * 501})
    assert refused.status_code == 422
    assert await status_of(api, client) == "active"


async def test_only_the_client_freezes_and_only_through_the_function(
    api: Api, app_database: Database
) -> None:
    client = await api.client()
    staff = await api.staff("admin")
    with pytest.raises(DBAPIError, match="only a client freezes"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("SELECT identity_freeze_client('-Nabc0000000000000F1', NULL, now())")
            )
    for statement in (
        "INSERT INTO identity_client_freezes (id, client_id, frozen_at, frozen_in) "
        "VALUES ('-Nabc0000000000000F2', :id, now(), pg_current_xact_id())",
        "UPDATE identity_clients SET status = 'locked' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(text(statement), {"id": client.id})
    assert await status_of(api, client) == "active"


async def test_a_client_never_sees_another_clients_freezes(
    api: Api, app_database: Database
) -> None:
    owner = await api.client()
    await api.http.post("/client/account-freeze", json={})
    other = await api.client()
    async with app_database.actor_transaction(other) as session:
        assert await freezes.freezes(session, str(owner.id)) == []
