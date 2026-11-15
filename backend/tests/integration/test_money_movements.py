"""Staff see every payment in and payout, newest first, with filters (QA finding F-010)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.infrastructure.database import Database
from app.modules.payments.domain.outcomes import Rejected, RejectionCode
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import PRESENT
from tests.support.kyc import verify
from tests.support.payments import FakeCollections, deposit_worker, drain, phone, start_deposit

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        yield running


async def every_page(api: Api, **params: str) -> list[dict[str, Any]]:
    """Every row, page by page. Other tests' movements are in the list too."""
    seen: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(500):
        response = await api.http.get(
            "/staff/money-movements", params={**params, **({"after": after} if after else {})}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["movements"]) <= 50
        seen += page["movements"]
        if page["next_after"] is None:
            return seen
        after = page["next_after"]
    raise AssertionError("the list never ended")


async def test_every_movement_is_listed_newest_first_and_each_filter_narrows_it(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    client = await api.client()
    await verify(app_database, client, PRESENT)
    provider = FakeCollections()
    refused = phone()
    provider.on_send(refused, Rejected(RejectionCode.ACCOUNT_NOT_FOUND))
    made = [
        await start_deposit(app_database, api.clock, actor=client, amount=10_000 * (n + 1))
        for n in range(2)
    ]
    failed = await start_deposit(app_database, api.clock, actor=client, msisdn=refused)
    await drain(deposit_worker(worker_database, provider, api.clock))
    await api.staff("finance")

    everything = await every_page(api)
    ids = [row["id"] for row in everything]
    assert len(ids) == len(set(ids)), "a movement was on two pages"
    times = [(row["created_at"], row["id"]) for row in everything]
    assert times == sorted(times, reverse=True)
    mine = [row for row in everything if row["client_id"] == client.id]
    assert [row["id"] for row in mine] == [failed.id, made[1].id, made[0].id]
    second = next(row for row in mine if row["id"] == made[1].id)
    assert (second["kind"], second["channel"], second["amount"]) == (
        "deposit",
        "mtn",
        {"minor": 20_000, "currency": "UGX"},
    )
    assert second["client_name"] == "Amara Nakato"
    refused_row = next(row for row in mine if row["id"] == failed.id)
    assert (refused_row["status"], refused_row["failure_code"]) == ("failed", "account_not_found")

    deposits = await every_page(api, show="deposits")
    assert all(row["kind"] == "deposit" for row in deposits)
    assert {failed.id, made[0].id} <= {row["id"] for row in deposits}
    payouts = await every_page(api, show="payouts")
    assert all(row["kind"] == "payout" for row in payouts)
    failures = await every_page(api, show="failed")
    assert all(row["status"] == "failed" for row in failures)
    assert failed.id in [row["id"] for row in failures]
    assert made[0].id not in [row["id"] for row in failures]
    unmatched = await every_page(api, show="unmatched")
    assert all(row["status"] in ("in_review", "uncertain") for row in unmatched)


async def test_a_bad_filter_or_page_place_is_refused(api: Api) -> None:
    await api.staff("finance")
    for params in ({"show": "everything"}, {"after": "yesterday"}):
        response = await api.http.get("/staff/money-movements", params=params)
        assert response.status_code == 422, params


async def test_only_staff_who_see_money_movement_may_list_it(api: Api) -> None:
    await api.staff("support")
    response = await api.http.get("/staff/money-movements")
    assert response.status_code == 403
