"""The allocation report over HTTP, against real PostgreSQL as finvest_app: who may read it, and
that it shows what the worker allocated.

The 182 day bill already holds what earlier tests bought and started, so the checks are on the
lot and the investment this test adds, and on totals that hold whatever else is there.
"""

import pytest

from app.infrastructure.database import Database
from tests.integration.test_dealing_allocations import buy, check, run
from tests.integration.test_dealing_api import Api, api, signed_in_staff
from tests.integration.test_dealing_settlement import CODE
from tests.support.investments import PRESENT, started

pytestmark = pytest.mark.integration
__all__ = ["api"]


async def test_the_report_is_for_staff_who_see_the_desk(api: Api) -> None:
    _, finance = await signed_in_staff(api, "finance")
    _, operations = await signed_in_staff(api, "operations")
    shown = await api.http.get("/staff/dealing/allocations?currency=USD", headers=finance)
    assert shown.status_code == 200, shown.text
    assert shown.json()["currency"] == "USD"
    refused = await api.http.get("/staff/dealing/allocations", headers=operations)
    assert refused.status_code == 403
    behind = await api.http.get("/staff/dealing/allocations/investments/x", headers=operations)
    assert behind.status_code == 403
    assert (await api.http.get("/staff/dealing/allocations")).status_code == 401


async def test_a_new_lot_and_the_investment_it_covers_show_in_the_report(
    api: Api, worker_database: Database
) -> None:
    await run(worker_database)
    waiting = await check(worker_database)
    newest = await started(api.database, worker_database, PRESENT, product_code=CODE)
    lot_id = await buy(api.database, waiting + newest.amount.minor)
    await run(worker_database)
    assert await check(worker_database) == 0
    _, finance = await signed_in_staff(api, "finance")

    response = await api.http.get("/staff/dealing/allocations", headers=finance)
    assert response.status_code == 200, response.text
    product = next(p for p in response.json()["products"] if p["product_code"] == CODE)
    lot = next(line for line in product["lots"] if line["lot_id"] == lot_id)
    assert lot["allocated"]["minor"] + lot["unallocated"]["minor"] == lot["cost"]["minor"]
    assert lot["investments"] >= 1
    # Nothing waits once the run has shared out a lot large enough for everyone.
    assert (product["investments_waiting"], product["waiting"]["minor"]) == (0, 0)
    assert product["covered"] == product["owed"]
    assert product["allocated"]["minor"] >= product["covered"]["minor"]

    behind = await api.http.get(
        f"/staff/dealing/allocations/investments/{newest.id}", headers=finance
    )
    assert behind.status_code == 200, behind.text
    body = behind.json()
    assert body["total"] == {"minor": newest.amount.minor, "currency": "UGX"}
    assert lot_id in {bill["lot_id"] for bill in body["bills"]}

    unknown = await api.http.get(
        "/staff/dealing/allocations/investments/-Nnothing", headers=finance
    )
    assert unknown.json() == {"investment_id": "-Nnothing", "bills": [], "total": None}
