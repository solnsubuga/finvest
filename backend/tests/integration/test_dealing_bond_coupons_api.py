"""Coupons paid in on the desk's bonds, over HTTP, against real PostgreSQL as finvest_app: who
may call it, a coupon recorded once per key, in the approvals inbox, and approved by a second
person."""

import pytest

from tests.integration.test_bond_holdings import live_issue, now
from tests.integration.test_dealing_api import Api, api, code, key, signed_in_staff
from tests.integration.test_dealing_bond_coupons import PER_MILLION, M, held_before, last_coupon

pytestmark = pytest.mark.integration
__all__ = ["api"]


async def screen(api: Api, headers: dict[str, str]) -> dict[str, object]:
    response = await api.http.get("/staff/dealing/bond-coupons?currency=UGX", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_a_coupon_is_recorded_once_and_approved_by_a_second_person(api: Api) -> None:
    clock = now()
    _, daniel = await signed_in_staff(api, "finance")
    _, grace = await signed_in_staff(api, "admin")
    _, support = await signed_in_staff(api, "support")
    async with live_issue(api.database, clock) as issue:
        coupon = await last_coupon(api.database, issue, clock)
        await held_before(api.database, issue, coupon, 1 * M, clock)
        before = await screen(api, daniel)
        due = [d for d in before["due"] if d["issue_code"] == issue]  # type: ignore[attr-defined]
        assert [(d["coupon_date"], d["expected"]["minor"]) for d in due] == [
            (coupon.isoformat(), PER_MILLION)
        ]
        refused = await api.http.get("/staff/dealing/bond-coupons", headers=support)
        assert (refused.status_code, code(refused)) == (403, "forbidden")

        coupon_in = {
            "issue_code": issue,
            "coupon_date": coupon.isoformat(),
            "currency": "UGX",
            "amount_minor": PER_MILLION,
            "value_date": coupon.isoformat(),
            "bank_reference": f"FT{key()['Idempotency-Key'][-12:]}",
        }
        once = key()
        recorded = await api.http.post(
            "/staff/dealing/bond-coupons", headers={**daniel, **once}, json=coupon_in
        )
        assert recorded.status_code == 200, recorded.text
        again = await api.http.post(
            "/staff/dealing/bond-coupons", headers={**daniel, **once}, json=coupon_in
        )
        assert again.status_code == 200
        mine = [c for c in again.json()["coupons"] if c["issue_code"] == issue]
        assert len(mine) == 1
        coupon_id = mine[0]["id"]
        assert mine[0]["allowed"] == []

        inbox = await api.http.get("/staff/approvals", headers=grace)
        items = {i["id"]: i for i in inbox.json()["items"]}
        assert (items[coupon_id]["kind"], items[coupon_id]["subject"]) == (
            "bond_coupon",
            f"{issue} coupon of {coupon.isoformat()}",
        )

        approved = await api.http.post(
            f"/staff/dealing/bond-coupons/{coupon_id}/approve", headers={**grace, **key()}
        )
        assert approved.status_code == 200, approved.text
        after = next(c for c in approved.json()["coupons"] if c["id"] == coupon_id)
        assert (after["status"], after["allowed"]) == ("approved", [])
        assert all(d["issue_code"] != issue for d in approved.json()["due"])
