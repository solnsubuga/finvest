"""The liquidity buffer over HTTP, against real PostgreSQL as finvest_app: who may call it, that
every command runs once per key, and a rules change and Finvest's money each passing two people.

Dollars, like the other buffer tests, with the dollar buffer moved to the level each test needs.
"""

from datetime import timedelta

import httpx
import pytest

from tests.integration.test_dealing_api import Api, api, code, key, signed_in_staff
from tests.integration.test_dealing_withdrawal_funding import set_buffer
from tests.support.investments import today

pytestmark = pytest.mark.integration
__all__ = ["api"]


async def screen(api: Api, headers: dict[str, str]) -> dict[str, object]:
    response = await api.http.get("/staff/dealing/buffer?currency=USD", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def item(body: dict[str, object], field: str, item_id: str) -> dict[str, object]:
    items = body[field]
    assert isinstance(items, list)
    return next(i for i in items if i["id"] == item_id)


async def inbox(api: Api, headers: dict[str, str]) -> dict[str, dict[str, object]]:
    response = await api.http.get("/staff/approvals", headers=headers)
    assert response.status_code == 200, response.text
    return {i["id"]: i for i in response.json()["items"]}


def new_id(before: dict[str, object], after: httpx.Response, field: str) -> str:
    assert after.status_code == 200, after.text
    old = {i["id"] for i in before[field]}  # type: ignore[attr-defined]
    (found,) = {i["id"] for i in after.json()[field]} - old
    return str(found)


async def test_the_buffer_screen_is_for_staff_who_see_the_desk(api: Api) -> None:
    await set_buffer(api.database, 0)
    _, finance = await signed_in_staff(api, "finance")
    _, operations = await signed_in_staff(api, "operations")
    body = await screen(api, finance)
    assert body["currency"] == "USD"
    assert body["buffer"] == {"minor": 0, "currency": "USD"}
    assert body["in_force"]["floor"] == {"minor": 1_000_000, "currency": "USD"}  # type: ignore[index]
    refused = await api.http.get("/staff/dealing/buffer?currency=USD", headers=operations)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    assert (await api.http.get("/staff/dealing/buffer")).status_code == 401


async def test_finvests_money_is_paid_in_by_one_person_and_approved_by_another(api: Api) -> None:
    await set_buffer(api.database, 0)
    _, daniel = await signed_in_staff(api, "finance")
    _, grace = await signed_in_staff(api, "admin")
    before = await screen(api, daniel)
    paid_before = before["finvest_money"]["minor"]  # type: ignore[index]
    movement = {
        "direction": "in",
        "currency": "USD",
        "amount_minor": 750_000,
        "reason": "Top up the dollar buffer",
        "bank_reference": f"FT{key()['Idempotency-Key']}"[:40],
        "value_date": today().isoformat(),
    }
    once = key()
    proposed = await api.http.post(
        "/staff/dealing/buffer/capital", headers={**daniel, **once}, json=movement
    )
    movement_id = new_id(before, proposed, "movements")
    # A retry with the same key records nothing more.
    again = await api.http.post(
        "/staff/dealing/buffer/capital", headers={**daniel, **once}, json=movement
    )
    assert again.status_code == 200
    assert len(again.json()["movements"]) == len(proposed.json()["movements"])
    assert item(proposed.json(), "movements", movement_id)["allowed"] == []
    assert item(await screen(api, grace), "movements", movement_id)["allowed"] == [
        "approve",
        "decline",
    ]

    own = await api.http.post(
        f"/staff/dealing/buffer/capital/{movement_id}/approve", headers={**daniel, **key()}
    )
    assert (own.status_code, code(own)) == (403, "forbidden")
    approve = {**grace, **key()}
    approved = await api.http.post(
        f"/staff/dealing/buffer/capital/{movement_id}/approve", headers=approve
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert item(body, "movements", movement_id)["status"] == "approved"
    assert body["buffer"] == {"minor": 750_000, "currency": "USD"}
    assert body["finvest_money"]["minor"] == paid_before + 750_000
    replay = await api.http.post(
        f"/staff/dealing/buffer/capital/{movement_id}/approve", headers=approve
    )
    assert replay.status_code == 200
    assert replay.json()["finvest_money"] == body["finvest_money"]


async def test_taking_out_more_than_the_buffer_holds_is_refused(api: Api) -> None:
    await set_buffer(api.database, 100)
    _, daniel = await signed_in_staff(api, "finance")
    refused = await api.http.post(
        "/staff/dealing/buffer/capital",
        headers={**daniel, **key()},
        json={"direction": "out", "currency": "USD", "amount_minor": 101, "reason": "Back"},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["fields"][0]["code"] in {
        "more_than_the_buffer",
        "more_than_paid_in",
    }


async def test_a_movement_is_declined_with_a_reason(api: Api) -> None:
    await set_buffer(api.database, 0)
    _, daniel = await signed_in_staff(api, "finance")
    _, grace = await signed_in_staff(api, "finance")
    before = await screen(api, daniel)
    proposed = await api.http.post(
        "/staff/dealing/buffer/capital",
        headers={**daniel, **key()},
        json={
            "direction": "in",
            "currency": "USD",
            "amount_minor": 500,
            "reason": "Top up",
            "bank_reference": f"FT-{key()['Idempotency-Key'][-12:]}",
            "value_date": today().isoformat(),
        },
    )
    movement_id = new_id(before, proposed, "movements")
    waiting = (await inbox(api, grace))[movement_id]
    assert (waiting["kind"], waiting["subject"]) == ("buffer_capital", "USD paid in")
    assert (waiting["yours"], waiting["can_decide"]) == (False, True)
    assert (await inbox(api, daniel))[movement_id]["yours"] is True
    declined = await api.http.post(
        f"/staff/dealing/buffer/capital/{movement_id}/decline",
        headers={**grace, **key()},
        json={"reason": "Not on the statement"},
    )
    assert declined.status_code == 200, declined.text
    assert item(declined.json(), "movements", movement_id)["status"] == "declined"
    assert declined.json()["buffer"] == {"minor": 0, "currency": "USD"}
    assert movement_id not in await inbox(api, grace)


async def test_a_rules_change_is_proposed_withdrawn_or_approved_by_another(api: Api) -> None:
    _, daniel = await signed_in_staff(api, "finance")
    _, grace = await signed_in_staff(api, "admin")
    before = await screen(api, daniel)
    # Each run adds versions that stay, so each run's change starts later and differs.
    count = len(before["versions"])  # type: ignore[arg-type]
    starts = today() + timedelta(days=400 + count)
    rules = {
        "currency": "USD",
        "effective_from": starts.isoformat(),
        "target_minor": 3_000_000 + count,
        "floor_minor": 1_500_000,
        "from_buffer_up_to_minor": 60_000,
        "pay_within_working_days": 2,
        "reason": "More dollar withdrawals",
    }
    proposed = await api.http.post(
        "/staff/dealing/buffer/rules", headers={**daniel, **key()}, json=rules
    )
    proposal_id = new_id(before, proposed, "proposals")
    assert item(proposed.json(), "proposals", proposal_id)["allowed"] == ["withdraw"]
    waiting = (await inbox(api, grace))[proposal_id]
    assert (waiting["kind"], waiting["subject"]) == ("buffer_rules", f"USD from {starts}")
    assert (waiting["yours"], waiting["can_decide"]) == (False, True)
    mine = (await inbox(api, daniel))[proposal_id]
    assert (mine["yours"], mine["can_decide"]) == (True, False)
    assert item(await screen(api, grace), "proposals", proposal_id)["allowed"] == [
        "approve",
        "send_back",
    ]
    approved = await api.http.post(
        f"/staff/dealing/buffer/rules/{proposal_id}/approve", headers={**grace, **key()}
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert item(body, "proposals", proposal_id)["status"] == "approved"
    assert proposal_id not in await inbox(api, grace)
    assert starts.isoformat() in {v["effective_from"] for v in body["versions"]}
    # In force only from that later day.
    assert body["in_force"]["floor"] == {"minor": 1_000_000, "currency": "USD"}

    second = {
        **rules,
        "effective_from": (starts + timedelta(days=1)).isoformat(),
        "target_minor": 4_000_000 + count,
    }
    before = approved.json()
    proposed = await api.http.post(
        "/staff/dealing/buffer/rules", headers={**daniel, **key()}, json=second
    )
    proposal_id = new_id(before, proposed, "proposals")
    withdrawn = await api.http.post(
        f"/staff/dealing/buffer/rules/{proposal_id}/withdraw", headers={**daniel, **key()}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert item(withdrawn.json(), "proposals", proposal_id)["status"] == "withdrawn"
