"""Withholding tax over HTTP, against real PostgreSQL as finvest_app: who may see it, the month's
return and its CSV file, and a payment to URA passing two people, once per key."""

import pytest

from app.infrastructure.database import Database
from tests.integration.test_dealing_api import Api, api, code, key, signed_in_staff
from tests.integration.test_tax import (
    LAST_MONTH,
    approved_client,
    fund_trust,
    running,
    withhold,
)
from tests.support.investments import today

pytestmark = pytest.mark.integration
__all__ = ["api"]

MONTH = f"{LAST_MONTH.year:04d}-{LAST_MONTH.month:02d}"


async def screen(api: Api, headers: dict[str, str]) -> dict[str, object]:
    response = await api.http.get("/staff/tax?currency=UGX", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def month_in(body: dict[str, object], month: str) -> dict[str, object]:
    months = body["months"]
    assert isinstance(months, list)
    return next(m for m in months if m["month"] == month)


async def test_the_tax_screen_is_for_finance(api: Api, worker_database: Database) -> None:
    _, finance = await signed_in_staff(api, "finance")
    _, operations = await signed_in_staff(api, "operations")
    body = await screen(api, finance)
    assert body["currency"] == "UGX"
    months = body["months"]
    assert isinstance(months, list) and len(months) == 12
    assert months[0]["month"] == f"{today().year:04d}-{today().month:02d}"
    assert month_in(body, MONTH)["due_on"] == f"{today().year:04d}-{today().month:02d}-15"
    for path in (
        "/staff/tax",
        f"/staff/tax/months/{MONTH}",
        f"/staff/tax/months/{MONTH}/payees.csv",
    ):
        refused = await api.http.get(path, headers=operations)
        assert (refused.status_code, code(refused)) == (403, "forbidden")
        assert (await api.http.get(path)).status_code == 401
    bad = await api.http.get("/staff/tax/months/2026-13", headers=finance)
    assert (bad.status_code, code(bad)) == (422, "validation_failed")


async def test_the_months_return_and_its_file_list_each_client(
    api: Api, worker_database: Database
) -> None:
    client = await approved_client(api.database)
    investment = await running(api.database, worker_database, client)
    await withhold(worker_database, investment, LAST_MONTH, 31_164, 4_674)
    _, finance = await signed_in_staff(api, "finance")
    response = await api.http.get(f"/staff/tax/months/{MONTH}?currency=UGX", headers=finance)
    assert response.status_code == 200, response.text
    body = response.json()
    (line,) = [p for p in body["payees"] if p["client_id"] == client.id]
    assert line["gross"] == {"minor": 31_164, "currency": "UGX"}
    assert line["tax"] == {"minor": 4_674, "currency": "UGX"}
    assert line["name"] == "NAKATO Amara"
    assert body["summary"]["month"] == MONTH
    exported = await api.http.get(f"/staff/tax/months/{MONTH}/payees.csv", headers=finance)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert f"finvest-withholding-ugx-{MONTH}.csv" in exported.headers["content-disposition"]
    rows = exported.text.split("\r\n")
    assert rows[0].startswith("Month,Payee name,Payee TIN")
    assert f"{MONTH},NAKATO Amara,,{line['account_number']},UGX,31164,4674" in rows


async def test_a_payment_to_ura_is_recorded_by_one_person_and_approved_by_another(
    api: Api, worker_database: Database
) -> None:
    client = await approved_client(api.database)
    investment = await running(api.database, worker_database, client)
    await withhold(worker_database, investment, LAST_MONTH, 6_000, 900)
    await fund_trust(worker_database, 900)
    daniel_id, daniel = await signed_in_staff(api, "finance")
    _, grace = await signed_in_staff(api, "admin")
    _, operations = await signed_in_staff(api, "operations")
    before = await screen(api, daniel)
    payment = {
        "currency": "UGX",
        "month": MONTH,
        "amount_minor": 900,
        "ura_reference": f"PRN-{key()['Idempotency-Key']}",
        "bank_reference": f"FT-{key()['Idempotency-Key']}",
        "paid_on": today().isoformat(),
        "note": "Withholding for the month",
    }
    refused = await api.http.post(
        "/staff/tax/remittances", headers={**operations, **key()}, json=payment
    )
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    once = key()
    recorded = await api.http.post(
        "/staff/tax/remittances", headers={**daniel, **once}, json=payment
    )
    assert recorded.status_code == 200, recorded.text
    again = await api.http.post("/staff/tax/remittances", headers={**daniel, **once}, json=payment)
    assert again.status_code == 200
    old = {r["id"] for r in before["remittances"]}  # type: ignore[attr-defined]
    (made,) = [r for r in again.json()["remittances"] if r["id"] not in old]
    assert (made["status"], made["allowed"]) == ("proposed", [])
    assert made["proposed_by"] == daniel_id
    seen_by_grace = await screen(api, grace)
    (mine,) = [r for r in seen_by_grace["remittances"] if r["id"] == made["id"]]  # type: ignore[attr-defined]
    assert mine["allowed"] == ["approve", "decline"]
    assert (mine["proposed_by_name"], mine["decided_by_name"]) == ("Ada Nakato", None)

    approved = await api.http.post(
        f"/staff/tax/remittances/{made['id']}/approve", headers={**grace, **key()}
    )
    assert approved.status_code == 200, approved.text
    (done,) = [r for r in approved.json()["remittances"] if r["id"] == made["id"]]
    assert done["status"] == "approved"
    held_before = before["held_for_ura"]["minor"]  # type: ignore[index]
    assert approved.json()["held_for_ura"]["minor"] == held_before - 900
    assert month_in(approved.json(), MONTH)["remitted"]["minor"] >= 900  # type: ignore[index]
