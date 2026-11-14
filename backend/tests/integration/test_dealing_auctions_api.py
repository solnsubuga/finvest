"""The auction calendar over HTTP, against real PostgreSQL as finvest_app: who may read and keep
it, what it refuses, that a retry enters nothing twice, and the next auction on the desk.

Tests share one database, so each picks its own days far enough apart not to meet another's.
"""

import secrets
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.identity.domain.permissions import ROLE_PERMISSIONS
from app.shared.actors import Actor
from tests.integration.test_dealing_api import Api, api, code, desk, key, signed_in_staff
from tests.support.investments import today

pytestmark = pytest.mark.integration
__all__ = ["api"]

PATH = "/staff/dealing/auctions"


def a_free_day() -> date:
    """A day some years ahead that no other test picks."""
    return today() + timedelta(days=400 + secrets.randbelow(20_000))


def entered(body: dict[str, object], product: str, day: date) -> list[dict[str, object]]:
    auctions = body["auctions"]
    assert isinstance(auctions, list)
    return [
        a for a in auctions if a["product_code"] == product and a["auction_date"] == day.isoformat()
    ]


async def test_the_calendar_is_read_with_the_desk_and_kept_with_the_order_permission(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    view_only = ROLE_PERMISSIONS["compliance"] | {"dealing_desk.view"}
    monkeypatch.setitem(ROLE_PERMISSIONS, "compliance", view_only)
    _, dealer = await signed_in_staff(api, "finance")
    _, reader = await signed_in_staff(api, "compliance")
    _, operations = await signed_in_staff(api, "operations")

    shown = await api.http.get(PATH, headers=dealer)
    assert shown.status_code == 200, shown.text
    assert shown.json()["may_change"] is True
    read = await api.http.get(PATH, headers=reader)
    assert read.status_code == 200, read.text
    assert read.json()["may_change"] is False

    new = {"product_code": "TREA-91", "auction_date": a_free_day().isoformat()}
    refused = await api.http.post(PATH, headers={**reader, **key()}, json=new)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    cancel = await api.http.post(
        f"{PATH}/-Nnothing/cancel", headers={**reader, **key()}, json={"reason": "No"}
    )
    assert (cancel.status_code, code(cancel)) == (403, "forbidden")
    assert (await api.http.get(PATH, headers=operations)).status_code == 403
    assert (await api.http.get(PATH)).status_code == 401


async def test_a_date_is_entered_once_and_cancelled_with_a_reason(api: Api) -> None:
    dealer_id, dealer = await signed_in_staff(api, "finance")
    day = a_free_day()
    new = {"product_code": "TREA-182", "auction_date": day.isoformat()}
    once = key()

    added = await api.http.post(PATH, headers={**dealer, **once}, json=new)
    assert added.status_code == 200, added.text
    (auction,) = entered(added.json(), "TREA-182", day)
    assert (auction["added_by"], auction["cancelled_by"]) == (dealer_id, None)

    # A retry with the same key answers again and enters nothing more.
    again = await api.http.post(PATH, headers={**dealer, **once}, json=new)
    assert again.status_code == 200, again.text
    assert len(entered(again.json(), "TREA-182", day)) == 1

    # A new key for the same product and day is refused.
    twice = await api.http.post(PATH, headers={**dealer, **key()}, json=new)
    assert (twice.status_code, code(twice)) == (422, "validation_failed")
    assert twice.json()["error"]["fields"][0]["code"] == "auction_already_entered"

    blank = await api.http.post(
        f"{PATH}/{auction['id']}/cancel", headers={**dealer, **key()}, json={"reason": "   "}
    )
    assert (blank.status_code, code(blank)) == (422, "validation_failed")
    cancelled = await api.http.post(
        f"{PATH}/{auction['id']}/cancel",
        headers={**dealer, **key()},
        json={"reason": "The Bank moved it to Thursday"},
    )
    assert cancelled.status_code == 200, cancelled.text
    (gone,) = entered(cancelled.json(), "TREA-182", day)
    assert (gone["cancelled_by"], gone["cancel_reason"]) == (
        dealer_id,
        "The Bank moved it to Thursday",
    )
    twice_cancelled = await api.http.post(
        f"{PATH}/{auction['id']}/cancel", headers={**dealer, **key()}, json={"reason": "Again"}
    )
    assert (twice_cancelled.status_code, code(twice_cancelled)) == (409, "conflict")
    unknown = await api.http.post(
        f"{PATH}/-Nnothing0000000000/cancel", headers={**dealer, **key()}, json={"reason": "No"}
    )
    assert (unknown.status_code, code(unknown)) == (404, "not_found")

    # The cancelled day may be entered again, and both rows stay.
    back = await api.http.post(PATH, headers={**dealer, **key()}, json=new)
    assert back.status_code == 200, back.text
    assert len(entered(back.json(), "TREA-182", day)) == 2

    async with api.database.actor_transaction(Actor("staff", dealer_id)) as session:
        trail = (
            await session.execute(
                text(
                    "SELECT action, reason FROM audit_events WHERE resource_type = "
                    "'dealing_auction' AND resource_id = :id ORDER BY occurred_at, id"
                ),
                {"id": auction["id"]},
            )
        ).all()
    assert [tuple(row) for row in trail] == [
        ("dealing.auction.added", None),
        ("dealing.auction.cancelled", "The Bank moved it to Thursday"),
    ]


async def test_a_past_day_and_an_unknown_product_are_refused(api: Api) -> None:
    _, dealer = await signed_in_staff(api, "finance")
    past = {"product_code": "TREA-91", "auction_date": (today() - timedelta(days=1)).isoformat()}
    refused = await api.http.post(PATH, headers={**dealer, **key()}, json=past)
    assert (refused.status_code, code(refused)) == (422, "validation_failed")
    assert refused.json()["error"]["fields"][0]["code"] == "auction_date_passed"
    unknown = {"product_code": "NOPE-1", "auction_date": a_free_day().isoformat()}
    refused = await api.http.post(PATH, headers={**dealer, **key()}, json=unknown)
    assert (refused.status_code, code(refused)) == (422, "validation_failed")
    assert refused.json()["error"]["fields"][0]["code"] == "unknown_product"


async def test_the_desk_shows_the_earliest_standing_date_for_each_bill(api: Api) -> None:
    _, dealer = await signed_in_staff(api, "finance")
    added = await api.http.post(
        PATH,
        headers={**dealer, **key()},
        json={"product_code": "TREA-91", "auction_date": today().isoformat()},
    )
    if added.status_code == 422:
        # Another test run already entered today; it stands or was cancelled.
        assert added.json()["error"]["fields"][0]["code"] == "auction_already_entered"
    else:
        assert added.status_code == 200, added.text

    calendar = (await api.http.get(PATH, headers=dealer)).json()["auctions"]
    expected = {
        product: min(
            (
                a["auction_date"]
                for a in calendar
                if a["product_code"] == product
                and a["cancelled_by"] is None
                and a["auction_date"] >= today().isoformat()
            ),
            default=None,
        )
        for product in ("TREA-91", "TREA-182")
    }
    assert expected["TREA-91"] == today().isoformat()
    positions = (await desk(api, dealer))["positions"]
    assert isinstance(positions, list)
    shown = {p["product_code"]: p["next_auction"] for p in positions}
    assert {product: shown[product] for product in expected} == expected


async def test_the_database_never_removes_or_rewrites_a_date(api: Api) -> None:
    dealer_id, dealer = await signed_in_staff(api, "finance")
    other_id, _ = await signed_in_staff(api, "finance")
    day = a_free_day()
    added = await api.http.post(
        PATH,
        headers={**dealer, **key()},
        json={"product_code": "TREA-91", "auction_date": str(day)},
    )
    (auction,) = entered(added.json(), "TREA-91", day)
    statements = [
        "DELETE FROM dealing_auctions WHERE id = :id",
        "UPDATE dealing_auctions SET auction_date = auction_date + 1 WHERE id = :id",
        # Cancelling in someone else's name.
        "UPDATE dealing_auctions SET cancelled_by = :other, cancelled_at = now(), "
        "cancel_reason = 'x' WHERE id = :id",
    ]
    for statement in statements:
        with pytest.raises(DBAPIError):
            async with api.database.actor_transaction(Actor("staff", dealer_id)) as session:
                await session.execute(text(statement), {"id": auction["id"], "other": other_id})
    # The person signed in may still cancel it.
    async with api.database.actor_transaction(Actor("staff", dealer_id)) as session:
        await session.execute(text(statements[2]), {"id": auction["id"], "other": dealer_id})
