"""Staff decide a payout held because it is large, through the HTTP routes."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.payouts.domain.payouts import PayoutStatus
from app.shared.actors import SYSTEM
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.payouts import World

pytestmark = pytest.mark.integration

LARGE = 12_000_000
FUNDS = 30_000_000


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = datetime.now(UTC)
        yield running


@pytest.fixture
async def world(api: Api, app_database: Database, worker_database: Database) -> World:
    # A bank account, so an approved payout waits for staff to pay it rather than being sent.
    return await World(app_database, worker_database, api.clock).build(FUNDS, mobile=False)


def key() -> dict[str, str]:
    return {"Idempotency-Key": push_id()}


def code(response: object) -> str:
    body = response.json()  # type: ignore[attr-defined]
    fields = body["error"].get("fields") or []
    return str(fields[0]["code"] if fields else body["error"]["code"])


async def test_one_member_of_staff_approves_a_held_payout(api: Api, world: World) -> None:
    held = await world.request(LARGE)
    assert held.status is PayoutStatus.HELD
    reviewer = await api.staff("operations")

    listed = await api.http.get("/staff/payouts/held")
    assert listed.status_code == 200, listed.text
    [item] = [each for each in listed.json() if each["id"] == held.id]
    assert (item["kind"], item["amount"]) == ("bank", {"minor": LARGE, "currency": "UGX"})
    assert item["client_id"] == world.client_id

    approve = f"/staff/payouts/{held.id}/approve"
    headers = key()
    approved = await api.http.post(approve, json={"reason": "Spoke to the client"}, headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json() == {"id": held.id, "decision": "approved", "status": "queued"}
    # A retried request is answered as the first was, and nothing is decided twice.
    again = await api.http.post(approve, json={"reason": "Spoke to the client"}, headers=headers)
    assert (again.status_code, again.json()) == (200, approved.json())
    reused = await api.http.post(approve, json={"reason": "Something else"}, headers=headers)
    assert (reused.status_code, code(reused)) == (422, "idempotency_key_reused")
    late = await api.http.post(approve, json={"reason": "Again"}, headers=key())
    assert (late.status_code, code(late)) == (409, "payout_not_held")
    assert held.id not in [
        each["id"] for each in (await api.http.get("/staff/payouts/held")).json()
    ]

    async with api.database.actor_transaction(reviewer) as session:
        events = await audit.events_for_resource(session, "payout", held.id)
    assert ("payouts.payout_review_approved", reviewer.id) in [
        (event.action, event.actor_id) for event in events
    ]


async def test_declining_a_held_payout_gives_the_money_back(api: Api, world: World) -> None:
    held = await world.request(LARGE)
    await api.staff("compliance")
    declined = await api.http.post(
        f"/staff/payouts/{held.id}/decline", json={"reason": "Could not reach"}, headers=key()
    )
    assert declined.status_code == 200, declined.text
    assert declined.json() == {"id": held.id, "decision": "declined", "status": "cancelled"}
    assert await world.balance(world.holding) == FUNDS
    assert await world.balance(held.in_flight_account_id) == 0
    # The client is told once, naming where it was going, never the account itself.
    async with world.worker.actor_transaction(SYSTEM) as session:
        told = (
            await session.execute(
                text(
                    "SELECT event, body FROM notifications"
                    " WHERE client_id = :client AND link_id = :id"
                ),
                {"client": world.client_id, "id": held.id},
            )
        ).all()
    assert [row.event for row in told] == ["payout_declined"]
    assert "your bank account" in told[0].body
    late = await api.http.post(
        f"/staff/payouts/{held.id}/approve", json={"reason": "Changed my mind"}, headers=key()
    )
    assert (late.status_code, code(late)) == (409, "payout_not_held")


async def test_deciding_a_held_payout_needs_the_reviewers_permission_and_a_fresh_step(
    api: Api, world: World
) -> None:
    held = await world.request(LARGE)
    approve = f"/staff/payouts/{held.id}/approve"

    await api.staff("finance")
    refused = await api.http.post(approve, json={"reason": "Fine"}, headers=key())
    assert refused.status_code == 403, refused.text

    await api.staff("operations")
    unkeyed = await api.http.post(approve, json={"reason": "Fine"})
    assert code(unkeyed) == "idempotency_key_required", unkeyed.text
    blank = await api.http.post(approve, json={"reason": ""}, headers=key())
    assert blank.status_code == 422, blank.text
    missing = await api.http.post(
        f"/staff/payouts/{push_id()}/approve", json={"reason": "Fine"}, headers=key()
    )
    assert missing.status_code == 404, missing.text

    # The second step was used more than ten minutes ago, so it is asked for again.
    api.clock.current += timedelta(minutes=11)
    stale = await api.http.post(approve, json={"reason": "Fine"}, headers=key())
    assert (stale.status_code, code(stale)) == (403, "confirm_it_is_you"), stale.text

    await api.staff("support")
    assert (await api.http.get("/staff/payouts/held")).status_code == 403
