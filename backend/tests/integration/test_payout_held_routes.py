"""Staff decide a payout held because it is large, through the HTTP routes."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.payouts import reviews
from app.modules.payouts.domain.payouts import PayoutStatus
from app.modules.payouts.payouts import PayoutView
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


async def held_payout(api: Api, world: World) -> PayoutView:
    held = await world.request(LARGE)
    # The world moves the clock past the new account's waiting time. The database checks that
    # a release is proposed and decided now, so the API's clock comes back to the real time.
    api.clock.current = datetime.now(UTC)
    return held


def key() -> dict[str, str]:
    return {"Idempotency-Key": push_id()}


def code(response: object) -> str:
    body = response.json()  # type: ignore[attr-defined]
    fields = body["error"].get("fields") or []
    return str(fields[0]["code"] if fields else body["error"]["code"])


async def test_two_members_of_staff_release_a_held_payout(api: Api, world: World) -> None:
    held = await held_payout(api, world)
    assert held.status is PayoutStatus.HELD
    maker = await api.staff("operations", name="Daniel Opio")

    listed = await api.http.get("/staff/payouts/held")
    assert listed.status_code == 200, listed.text
    [item] = [each for each in listed.json() if each["id"] == held.id]
    assert (item["kind"], item["amount"]) == ("bank", {"minor": LARGE, "currency": "UGX"})
    assert (item["client_id"], item["release"]) == (world.client_id, None)

    propose = f"/staff/payouts/{held.id}/release"
    headers = key()
    proposed = await api.http.post(propose, json={"reason": "Spoke to the client"}, headers=headers)
    assert proposed.status_code == 201, proposed.text
    release = proposed.json()
    assert (release["status"], release["proposed_by"], release["reason"]) == (
        "proposed",
        maker.id,
        "Spoke to the client",
    )
    again = await api.http.post(propose, json={"reason": "Spoke to the client"}, headers=headers)
    assert (again.status_code, again.json()) == (201, release)
    second = await api.http.post(propose, json={"reason": "Me too"}, headers=key())
    assert (second.status_code, code(second)) == (409, "release_already_proposed")
    [item] = [
        each for each in (await api.http.get("/staff/payouts/held")).json() if each["id"] == held.id
    ]
    assert item["release"] == release

    approve = f"/staff/payout-releases/{release['id']}/approve"
    own = await api.http.post(approve, json={"reason": "Fine"}, headers=key())
    assert (own.status_code, code(own)) == (403, "proposed_by_you")

    checker = await api.staff("operations")
    headers = key()
    approved = await api.http.post(approve, json={"reason": "Agree"}, headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json() == {"id": held.id, "decision": "approved", "status": "queued"}
    # A retried request is answered as the first was, and nothing is decided twice.
    again = await api.http.post(approve, json={"reason": "Agree"}, headers=headers)
    assert (again.status_code, again.json()) == (200, approved.json())
    reused = await api.http.post(approve, json={"reason": "Something else"}, headers=headers)
    assert (reused.status_code, code(reused)) == (422, "idempotency_key_reused")
    late = await api.http.post(approve, json={"reason": "Again"}, headers=key())
    assert (late.status_code, code(late)) == (409, "release_already_decided")
    after = await api.http.post(propose, json={"reason": "Again"}, headers=key())
    assert (after.status_code, code(after)) == (409, "payout_not_held")
    assert held.id not in [
        each["id"] for each in (await api.http.get("/staff/payouts/held")).json()
    ]

    async with api.database.actor_transaction(checker) as session:
        events = await audit.events_for_resource(session, "payout", held.id)
    actions = [(event.action, event.actor_id) for event in events]
    assert ("payouts.release_proposed", maker.id) in actions
    assert ("payouts.release_approved", checker.id) in actions
    assert ("payouts.payout_review_approved", checker.id) in actions


async def test_the_proposer_withdraws_and_a_decline_closes_the_proposal(
    api: Api, world: World
) -> None:
    held = await held_payout(api, world)
    maker = await api.staff("operations")
    first = (
        await api.http.post(
            f"/staff/payouts/{held.id}/release", json={"reason": "Fine"}, headers=key()
        )
    ).json()
    other = await api.staff("operations")
    theirs = await api.http.post(
        f"/staff/payout-releases/{first['id']}/withdraw", json={"reason": "Not mine"}
    )
    assert (theirs.status_code, code(theirs)) == (403, "not_proposed_by_you")
    await api.as_staff(maker)
    withdrawn = await api.http.post(
        f"/staff/payout-releases/{first['id']}/withdraw", json={"reason": "Wrong payout"}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert (withdrawn.json()["status"], withdrawn.json()["decided_by"]) == ("withdrawn", maker.id)

    second = (
        await api.http.post(
            f"/staff/payouts/{held.id}/release", json={"reason": "Now yes"}, headers=key()
        )
    ).json()
    await api.as_staff(other)
    declined = await api.http.post(
        f"/staff/payouts/{held.id}/decline", json={"reason": "Could not reach"}, headers=key()
    )
    assert declined.json()["status"] == "cancelled", declined.text
    async with api.database.actor_transaction(other) as session:
        closed = await reviews.get_release(session, second["id"])
    assert (closed.status, closed.decided_by) == (reviews.ReleaseStatus.DECLINED, other.id)


async def test_declining_a_held_payout_gives_the_money_back(api: Api, world: World) -> None:
    held = await held_payout(api, world)
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
        f"/staff/payouts/{held.id}/release", json={"reason": "Changed my mind"}, headers=key()
    )
    assert (late.status_code, code(late)) == (409, "payout_not_held")


async def test_deciding_a_held_payout_needs_the_reviewers_permission_and_a_fresh_step(
    api: Api, world: World
) -> None:
    held = await held_payout(api, world)
    await api.staff("finance")
    refused = await api.http.post(
        f"/staff/payouts/{held.id}/release", json={"reason": "Fine"}, headers=key()
    )
    assert refused.status_code == 403, refused.text

    await api.staff("operations")
    release = (
        await api.http.post(
            f"/staff/payouts/{held.id}/release", json={"reason": "Fine"}, headers=key()
        )
    ).json()
    unproposed = await api.http.post(f"/staff/payouts/{held.id}/release", json={"reason": "Fine"})
    assert code(unproposed) == "idempotency_key_required", unproposed.text
    await api.staff("operations")
    approve = f"/staff/payout-releases/{release['id']}/approve"
    unkeyed = await api.http.post(approve, json={"reason": "Fine"})
    assert code(unkeyed) == "idempotency_key_required", unkeyed.text
    blank = await api.http.post(approve, json={"reason": ""}, headers=key())
    assert blank.status_code == 422, blank.text
    missing = await api.http.post(
        f"/staff/payout-releases/{push_id()}/approve", json={"reason": "Fine"}, headers=key()
    )
    assert missing.status_code == 404, missing.text

    # The second step was used more than ten minutes ago, so it is asked for again.
    api.clock.current += timedelta(minutes=11)
    stale = await api.http.post(approve, json={"reason": "Fine"}, headers=key())
    assert (stale.status_code, code(stale)) == (403, "confirm_it_is_you"), stale.text

    await api.staff("support")
    assert (await api.http.get("/staff/payouts/held")).status_code == 403
