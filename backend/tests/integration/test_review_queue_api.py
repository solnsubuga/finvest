"""The review queue lists what waits for a person, by the reader's permissions."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from app.infrastructure.database import Database
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
    return await World(app_database, worker_database, api.clock).build(FUNDS, mobile=False)


async def read(api: Api) -> dict[str, Any]:
    response = await api.http.get("/staff/review-queue")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def find(body: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    return next((each for each in body["items"] if each["id"] == item_id), None)


async def test_a_held_payout_waits_in_the_queue_until_released(api: Api, world: World) -> None:
    held = await world.request(LARGE)
    api.clock.current = datetime.now(UTC)
    maker = await api.staff("operations", name="Daniel Opio")

    body = await read(api)
    item = find(body, held.id)
    assert item is not None
    assert (item["kind"], item["group"], item["reason"]) == (
        "held_payout",
        "withdrawals",
        "above_review_limit",
    )
    assert item["amount"] == {"minor": LARGE, "currency": "UGX"}
    assert (item["client_id"], item["proposed_by"], item["yours"]) == (world.client_id, None, False)
    # The figures above the list agree with the list.
    assert body["waiting"] == len(body["items"])
    assert body["oldest_waiting_since"] == min(each["waiting_since"] for each in body["items"])
    assert body["cleared_automatically_today"] >= 0

    proposed = await api.http.post(
        f"/staff/payouts/{held.id}/release",
        json={"reason": "Spoke to the client"},
        headers={"Idempotency-Key": push_id()},
    )
    assert proposed.status_code == 201, proposed.text
    mine = find(await read(api), held.id)
    assert mine is not None
    assert (mine["proposed_by"], mine["yours"]) == (maker.id, True)

    await api.staff("operations")
    theirs = find(await read(api), held.id)
    assert theirs is not None
    assert (theirs["proposed_by"], theirs["yours"]) == (maker.id, False)


async def test_each_role_reads_only_the_kinds_it_may_see(api: Api) -> None:
    await api.staff("compliance")
    body = await read(api)
    assert body["kinds"] == [
        "identity_check",
        "payout_account",
        "held_payout",
        "investment_transfer",
        "organisation",
    ]
    assert {each["kind"] for each in body["items"]} <= set(body["kinds"])

    await api.staff("support")
    refused = await api.http.get("/staff/review-queue")
    assert refused.status_code == 403
