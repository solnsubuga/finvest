"""Sending a client a statement of chosen dates, with two members of staff."""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.shared.actors import Actor
from app.shared.business_day import business_date
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def propose(api: Api, client: Actor, days_back: int = 30, ahead: int = 0) -> httpx.Response:
    today = business_date(api.clock.now())
    return await api.http.post(
        f"/staff/clients/{client.id}/actions",
        json={
            "kind": "statement",
            "reason": "Client asked on the phone for a statement for their bank",
            "statement": {
                "date_from": (today - timedelta(days=days_back)).isoformat(),
                "date_to": (today + timedelta(days=ahead)).isoformat(),
            },
        },
    )


async def documents_of(owner: Database, client: Actor) -> list[tuple[str, str, str]]:
    async with owner.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT kind, status, idempotency_key FROM documents WHERE client_id = :id "
                "ORDER BY requested_at"
            ),
            {"id": client.id},
        )
        return [(row.kind, row.status, row.idempotency_key) for row in rows]


async def test_a_statement_two_people_agree_is_asked_for_and_queued(
    api: Api, migrator_database: Database
) -> None:
    client = await api.client()
    checker = await api.staff("operations")
    await api.staff("support")
    proposed = await propose(api, client)
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["statement"]["date_to"] == business_date(api.clock.now()).isoformat()
    assert action["limits"] is None
    assert await documents_of(migrator_database, client) == []

    await api.as_staff(checker)
    inbox = (await api.http.get("/staff/approvals")).json()["items"]
    assert [item["kind"] for item in inbox if item["id"] == action["id"]] == ["client_statement"]
    approved = await api.http.post(
        f"/staff/clients/actions/{action['id']}/decision",
        json={"decision": "approved", "reason": "Checked the dates"},
    )
    assert approved.status_code == 200, approved.text
    key = f"staff:{action['id']}"
    assert await documents_of(migrator_database, client) == [
        ("requested_statement", "pending", key)
    ]
    async with migrator_database.transaction() as session:
        jobs = await session.scalar(
            text("SELECT count(*) FROM jobs WHERE kind = 'documents.build'")
        )
    assert jobs and jobs > 0


async def test_a_period_a_client_could_not_ask_for_is_refused(api: Api) -> None:
    client = await api.client()
    await api.staff("support")
    future = await propose(api, client, ahead=1)
    assert future.status_code == 422
    assert future.json()["error"]["fields"][0]["code"] == "period_in_future"
    too_long = await propose(api, client, days_back=400)
    assert too_long.status_code == 422
    missing = await api.http.post(
        f"/staff/clients/{client.id}/actions",
        json={"kind": "statement", "reason": "Asked"},
    )
    assert missing.status_code == 422


async def test_finance_cannot_send_a_statement(api: Api) -> None:
    client = await api.client()
    await api.staff("finance")
    assert (await propose(api, client)).status_code == 403


async def test_staff_never_ask_for_a_statement_without_an_approval(api: Api) -> None:
    client = await api.client()
    staff = await api.staff("admin")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with api.database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO documents (id, client_id, kind, idempotency_key, period_start, "
                    "period_end, requested_at) VALUES ('-Nabc0000000000000Z5', :client, "
                    "'requested_statement', 'staff:-Nabc0000000000000Z4', DATE '2026-10-01', "
                    "DATE '2026-10-31', now())"
                ),
                {"client": client.id},
            )
