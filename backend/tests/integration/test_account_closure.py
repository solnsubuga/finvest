"""Closing a client's account: the client asks, one member of staff prepares, another closes."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.closures import service as closures
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import PRESENT, pay, request
from tests.support.kyc import verify
from tests.support.ledger import ugx

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


async def ask(api: Api, reason: str | None = "Moving abroad") -> httpx.Response:
    return await api.http.post("/client/account-closure", json={"reason": reason})


async def step(api: Api, request_id: str, name: str, note: str = "Checked") -> httpx.Response:
    return await api.http.post(f"/staff/closures/{request_id}/{name}", json={"note": note})


async def test_a_client_with_nothing_left_sees_no_blockers(api: Api) -> None:
    await api.client()
    found = (await api.http.get("/client/account-closure")).json()
    assert found["request"] is None
    assert found["standing"]["blockers"] == []
    assert found["standing"]["owed"] == []


async def test_closing_takes_two_people_and_ends_every_session(api: Api) -> None:
    client = await api.client()
    asked = await ask(api)
    assert asked.status_code == 201, asked.text
    request_id = asked.json()["request"]["id"]
    assert asked.json()["request"]["status"] == "requested"
    assert (await ask(api)).status_code == 409  # one open request at a time

    preparer = await api.staff("operations")
    listed = (await api.http.get("/staff/closures")).json()["items"]
    assert [item["id"] for item in listed] == [request_id]
    assert listed[0]["can_prepare"] is True and listed[0]["can_approve"] is False
    prepared = await step(api, request_id, "preparation")
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["status"] == "prepared"
    assert prepared.json()["can_approve"] is False
    assert (await step(api, request_id, "approval")).status_code == 403

    await api.staff("compliance")
    inbox = (await api.http.get("/staff/approvals")).json()
    assert any(item["kind"] == "account_closure" for item in inbox["items"]), inbox
    approved = await step(api, request_id, "approval", "Second check done")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "closed" and body["prepared_by"] == preparer.id
    assert await status_of(api, client) == "closed"

    await api.as_staff(preparer)
    assert (await step(api, request_id, "decline")).status_code == 409
    async with api.database.actor_transaction(preparer) as session:
        actions = (
            await session.execute(
                text(
                    "SELECT action FROM audit_events WHERE resource_type = 'closure_request' "
                    "AND resource_id = :id ORDER BY occurred_at, id"
                ),
                {"id": request_id},
            )
        ).scalars()
        assert list(actions) == ["closure.requested", "closure.prepared", "closure.approved"]


async def test_a_closed_client_can_no_longer_use_their_session(api: Api) -> None:
    client = await api.client()
    token = api.http.headers["Authorization"]
    request_id = (await ask(api)).json()["request"]["id"]
    await api.staff("operations")
    await step(api, request_id, "preparation")
    await api.staff("admin")
    await step(api, request_id, "approval")
    api.http.cookies.clear()
    api.http.headers.pop("Origin", None)
    api.http.headers["Authorization"] = token
    assert (await api.http.get("/client/session")).status_code == 401
    assert client.id


async def test_a_running_investment_or_money_owed_stops_the_request(
    api: Api, worker_database: Database
) -> None:
    client = await api.client()
    await verify(api.database, client, api.clock)
    investment = await request(api.database, client, PRESENT, amount=ugx(1_000_000))
    await api.as_client(client)
    blocked = await ask(api)
    assert blocked.status_code == 409
    codes = [f["code"] for f in blocked.json()["error"]["fields"]]
    assert codes == ["investments_running"]

    await pay(worker_database, investment, investment.amount, api.clock)
    standing = (await api.http.get("/client/account-closure")).json()["standing"]
    assert standing["blockers"] == ["investments_running", "money_owed"]
    assert standing["owed"] == [{"amount_minor": 1_000_000, "currency": "UGX"}]
    assert standing["running_investments"] == 1


async def test_staff_check_again_before_closing(api: Api) -> None:
    client = await api.client()
    await verify(api.database, client, api.clock)
    await api.as_client(client)
    request_id = (await ask(api)).json()["request"]["id"]
    await api.staff("operations")
    await step(api, request_id, "preparation")
    # The client starts an investment after the request was prepared.
    await request(api.database, client, PRESENT, amount=ugx(1_000_000))
    await api.staff("compliance")
    refused = await step(api, request_id, "approval")
    assert refused.status_code == 409
    assert [f["code"] for f in refused.json()["error"]["fields"]] == ["investments_running"]
    assert await status_of(api, client) == "active"


async def test_a_declined_request_shows_the_client_why_and_they_may_ask_again(api: Api) -> None:
    client = await api.client()
    request_id = (await ask(api)).json()["request"]["id"]
    await api.staff("support")
    assert (await step(api, request_id, "decline")).status_code == 403
    await api.staff("compliance")
    declined = await step(api, request_id, "decline", "Please call us about your complaint first")
    assert declined.status_code == 200, declined.text
    await api.as_client(client)
    seen = (await api.http.get("/client/account-closure")).json()["request"]
    assert seen["status"] == "declined"
    assert seen["decision_note"] == "Please call us about your complaint first"
    assert (await ask(api)).status_code == 201


async def test_the_client_may_cancel_while_staff_have_not_decided(api: Api) -> None:
    client = await api.client()
    request_id = (await ask(api, None)).json()["request"]["id"]
    await api.staff("operations")
    await step(api, request_id, "preparation")
    await api.as_client(client)
    cancelled = await api.http.post(f"/client/account-closure/{request_id}/cancellation")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["request"]["status"] == "cancelled"
    await api.staff("compliance")
    assert (await step(api, request_id, "approval")).status_code == 409
    other = await api.client()
    assert other.id
    stranger = await api.http.post(f"/client/account-closure/{request_id}/cancellation")
    assert stranger.status_code == 404


async def test_asking_needs_a_recent_sign_in(api: Api) -> None:
    client = await api.client()
    api.clock.advance(minutes=11)
    assert (await ask(api)).json()["error"]["code"] == "sign_in_again"
    await api.as_client(client)
    assert (await ask(api)).status_code == 201


async def test_two_approvals_at_once_close_the_account_once(api: Api) -> None:
    client = await api.client()
    request_id = (await ask(api)).json()["request"]["id"]
    preparer = await api.staff("operations")
    await step(api, request_id, "preparation")
    first, second = await api.staff("compliance"), await api.staff("admin")
    assert preparer.id

    async def approve(staff: Actor) -> str:
        try:
            async with api.database.actor_transaction(staff) as session:
                await closures.approve(session, staff, request_id, "Checked", api.clock)
                await asyncio.sleep(0.2)
            return "closed"
        except ApplicationError as error:
            return error.code

    outcomes = sorted(await asyncio.gather(approve(first), approve(second)))
    assert outcomes == ["closed", "conflict"]
    assert await status_of(api, client) == "closed"


async def test_postgres_refuses_a_closure_without_a_second_person(api: Api) -> None:
    client = await api.client()
    request_id = (await ask(api)).json()["request"]["id"]
    staff = await api.staff("operations")
    await step(api, request_id, "preparation")
    with pytest.raises(DBAPIError, match="second member of staff"):
        async with api.database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE closure_requests SET status = 'closed', decided_by = :staff, "
                    "decided_at = now(), decision_note = 'x', decided_in = pg_current_xact_id() "
                    "WHERE id = :id"
                ),
                {"staff": staff.id, "id": request_id},
            )
    assert client.id


async def test_postgres_refuses_closing_a_client_without_an_approved_request(api: Api) -> None:
    client = await api.client()
    for statement in (
        "UPDATE identity_clients SET status = 'closed' WHERE id = :id",
        "SELECT identity_close_client('-Nabc0000000000000Z9')",
    ):
        try:
            async with api.database.actor_transaction(client) as session:
                result = await session.execute(text(statement), {"id": client.id})
                if statement.startswith("SELECT"):
                    assert result.scalar() is False
        except DBAPIError as error:
            assert "permission denied" in str(error)
    assert await status_of(api, client) == "active"


async def test_a_client_cannot_ask_for_someone_else(api: Api) -> None:
    owner = await api.client()
    other = await api.client()
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(other) as session:
            await session.execute(
                text(
                    "INSERT INTO closure_requests (id, client_id, requested_at) "
                    "VALUES ('-Nabc0000000000000Q1', :id, now())"
                ),
                {"id": owner.id},
            )
