"""The auction calendar over HTTP, against real PostgreSQL as finvest_app: who may read and keep
it, that every change takes two people, what it refuses, that a retry applies nothing twice, the
next auction on the desk and the approvals inbox.

Tests share one database, so each picks its own days far enough apart not to meet another's.
"""

import asyncio
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
Headers = dict[str, str]


def a_free_day() -> date:
    """A day some years ahead that no other test picks."""
    return today() + timedelta(days=400 + secrets.randbelow(20_000))


def entered(body: dict[str, object], product: str, day: date) -> list[dict[str, object]]:
    auctions = body["auctions"]
    assert isinstance(auctions, list)
    return [
        a for a in auctions if a["product_code"] == product and a["auction_date"] == day.isoformat()
    ]


async def propose(api: Api, headers: Headers, product: str, day: date) -> dict[str, object]:
    response = await api.http.post(
        PATH, headers={**headers, **key()}, json={"product_code": product, "auction_date": str(day)}
    )
    assert response.status_code == 200, response.text
    (found,) = [a for a in entered(response.json(), product, day) if a["state"] == "waiting"]
    return found


async def step(
    api: Api, headers: Headers, change_id: object, name: str, body: object = None
) -> dict[str, object]:
    response = await api.http.post(
        f"{PATH}/changes/{change_id}/{name}", headers={**headers, **key()}, json=body
    )
    assert response.status_code == 200, response.text
    result: dict[str, object] = response.json()
    return result


async def standing(api: Api, maker: Headers, checker: Headers, product: str, day: date) -> str:
    """A date one person proposed and another approved."""
    waiting = await propose(api, maker, product, day)
    await step(api, checker, waiting["id"], "approve")
    return str(waiting["id"])


async def test_the_calendar_is_read_with_the_desk_and_kept_with_the_order_permission(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    view_only = ROLE_PERMISSIONS["compliance"] | {"dealing_desk.view"}
    monkeypatch.setitem(ROLE_PERMISSIONS, "compliance", view_only)
    _, dealer = await signed_in_staff(api, "finance")
    _, reader = await signed_in_staff(api, "compliance")
    _, operations = await signed_in_staff(api, "operations")
    waiting = await propose(api, dealer, "TREA-91", a_free_day())

    shown = await api.http.get(PATH, headers=dealer)
    assert shown.status_code == 200, shown.text
    assert shown.json()["may_change"] is True
    read = await api.http.get(PATH, headers=reader)
    assert read.status_code == 200, read.text
    assert read.json()["may_change"] is False
    # The reader sees the waiting date and is offered nothing on it.
    (same,) = [a for a in read.json()["auctions"] if a["id"] == waiting["id"]]
    assert same["allowed"] == []

    new = {"product_code": "TREA-91", "auction_date": a_free_day().isoformat()}
    commands = [
        (PATH, new),
        (f"{PATH}/{waiting['id']}/cancel", {"reason": "No"}),
        (f"{PATH}/changes/{waiting['id']}/approve", None),
        (f"{PATH}/changes/{waiting['id']}/decline", {"note": "No"}),
        (f"{PATH}/changes/{waiting['id']}/withdraw", None),
    ]
    for path, body in commands:
        refused = await api.http.post(path, headers={**reader, **key()}, json=body)
        assert (refused.status_code, code(refused)) == (403, "forbidden"), path
    assert (await api.http.get(PATH, headers=operations)).status_code == 403
    assert (await api.http.get(PATH)).status_code == 401


async def test_a_date_counts_once_a_second_person_approves_it(api: Api) -> None:
    maker_id, maker = await signed_in_staff(api, "finance")
    checker_id, checker = await signed_in_staff(api, "admin")
    day = a_free_day()
    once = key()
    body = {"product_code": "TREA-182", "auction_date": day.isoformat()}

    proposed = await api.http.post(PATH, headers={**maker, **once}, json=body)
    assert proposed.status_code == 200, proposed.text
    (waiting,) = entered(proposed.json(), "TREA-182", day)
    assert (waiting["state"], waiting["proposed_by"], waiting["allowed"]) == (
        "waiting",
        maker_id,
        ["withdraw"],
    )
    # A retry with the same key answers again and proposes nothing more.
    again = await api.http.post(PATH, headers={**maker, **once}, json=body)
    assert len(entered(again.json(), "TREA-182", day)) == 1
    # A new key for the same product and day is refused while one waits.
    twice = await api.http.post(PATH, headers={**maker, **key()}, json=body)
    assert (twice.status_code, code(twice)) == (422, "validation_failed")
    assert twice.json()["error"]["fields"][0]["code"] == "auction_already_entered"

    # The proposer may not approve their own.
    own = await api.http.post(
        f"{PATH}/changes/{waiting['id']}/approve", headers={**maker, **key()}, json=None
    )
    assert (own.status_code, code(own)) == (403, "forbidden")
    shown = entered((await api.http.get(PATH, headers=checker)).json(), "TREA-182", day)
    assert shown[0]["allowed"] == ["approve", "decline"]

    approved = await step(api, checker, waiting["id"], "approve")
    (date_,) = entered(approved, "TREA-182", day)
    assert (date_["state"], date_["decided_by"]) == ("standing", checker_id)
    # Approving twice is refused: it is no longer waiting.
    late = await api.http.post(
        f"{PATH}/changes/{waiting['id']}/approve", headers={**checker, **key()}, json=None
    )
    assert (late.status_code, code(late)) == (409, "conflict")
    standing_twice = await api.http.post(PATH, headers={**checker, **key()}, json=body)
    assert standing_twice.json()["error"]["fields"][0]["code"] == "auction_already_entered"


async def test_a_cancellation_takes_two_people_and_the_day_may_be_proposed_again(
    api: Api,
) -> None:
    maker_id, maker = await signed_in_staff(api, "finance")
    _, checker = await signed_in_staff(api, "admin")
    day = a_free_day()
    date_id = await standing(api, maker, checker, "TREA-91", day)

    blank = await api.http.post(
        f"{PATH}/{date_id}/cancel", headers={**maker, **key()}, json={"reason": "   "}
    )
    assert (blank.status_code, code(blank)) == (422, "validation_failed")
    asked = await api.http.post(
        f"{PATH}/{date_id}/cancel",
        headers={**maker, **key()},
        json={"reason": "The Bank moved it to Thursday"},
    )
    assert asked.status_code == 200, asked.text
    (cancelling,) = entered(asked.json(), "TREA-91", day)
    cancellation = cancelling["cancellation"]
    assert isinstance(cancellation, dict)
    assert cancelling["state"] == "cancelling"
    assert (cancellation["status"], cancellation["proposed_by"], cancellation["reason"]) == (
        "proposed",
        maker_id,
        "The Bank moved it to Thursday",
    )
    again = await api.http.post(
        f"{PATH}/{date_id}/cancel", headers={**checker, **key()}, json={"reason": "Again"}
    )
    assert (again.status_code, code(again)) == (409, "conflict")

    # Declining needs a note, and the date stands again.
    no_note = await api.http.post(
        f"{PATH}/changes/{cancellation['id']}/decline", headers={**checker, **key()}, json={}
    )
    assert no_note.status_code == 422
    declined = await step(api, checker, cancellation["id"], "decline", {"note": "It did not move"})
    assert entered(declined, "TREA-91", day)[0]["state"] == "standing"

    asked = await api.http.post(
        f"{PATH}/{date_id}/cancel", headers={**maker, **key()}, json={"reason": "It moved now"}
    )
    second = entered(asked.json(), "TREA-91", day)[0]["cancellation"]
    assert isinstance(second, dict)
    cancelled = await step(api, checker, second["id"], "approve")
    assert entered(cancelled, "TREA-91", day)[0]["state"] == "cancelled"

    # The day may be proposed again, and the cancelled date stays.
    await propose(api, maker, "TREA-91", day)
    calendar = (await api.http.get(PATH, headers=maker)).json()
    assert sorted(str(a["state"]) for a in entered(calendar, "TREA-91", day)) == [
        "cancelled",
        "waiting",
    ]

    async with api.database.actor_transaction(Actor("staff", maker_id)) as session:
        trail = (
            await session.execute(
                text(
                    "SELECT action FROM audit_events WHERE resource_type = 'dealing_auction' "
                    "AND details ->> 'day' = :day AND details ->> 'product' = 'TREA-91' "
                    "ORDER BY occurred_at, id"
                ),
                {"day": day.isoformat()},
            )
        ).scalars()
        assert list(trail) == [
            "dealing.auction.add_proposed",
            "dealing.auction.add_approved",
            "dealing.auction.cancel_proposed",
            "dealing.auction.cancel_declined",
            "dealing.auction.cancel_proposed",
            "dealing.auction.cancel_approved",
            "dealing.auction.add_proposed",
        ]


async def test_the_proposer_may_withdraw_and_no_one_else(api: Api) -> None:
    _, maker = await signed_in_staff(api, "finance")
    _, other = await signed_in_staff(api, "admin")
    day = a_free_day()
    waiting = await propose(api, maker, "TREA-91", day)
    refused = await api.http.post(
        f"{PATH}/changes/{waiting['id']}/withdraw", headers={**other, **key()}, json=None
    )
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    withdrawn = await step(api, maker, waiting["id"], "withdraw")
    assert entered(withdrawn, "TREA-91", day)[0]["state"] == "withdrawn"
    unknown = await api.http.post(
        f"{PATH}/changes/-Nnothing0000000000/approve", headers={**other, **key()}, json=None
    )
    assert (unknown.status_code, code(unknown)) == (404, "not_found")


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


async def test_the_desk_shows_the_earliest_standing_date_and_the_inbox_the_waiting_ones(
    api: Api,
) -> None:
    _, maker = await signed_in_staff(api, "finance")
    _, checker = await signed_in_staff(api, "admin")
    proposed = await api.http.post(
        PATH,
        headers={**maker, **key()},
        json={"product_code": "TREA-91", "auction_date": today().isoformat()},
    )
    if proposed.status_code == 200:
        (waiting,) = [
            a for a in entered(proposed.json(), "TREA-91", today()) if a["state"] == "waiting"
        ]
        inbox = await api.http.get("/staff/approvals", headers=checker)
        assert inbox.status_code == 200, inbox.text
        items = {i["id"]: i for i in inbox.json()["items"] if i["kind"] == "auction_date"}
        assert items[str(waiting["id"])]["can_decide"] is True
        assert items[str(waiting["id"])]["subject"] == f"TREA-91 on {today().isoformat()}"
        await step(api, checker, waiting["id"], "approve")
    else:
        # Another run already put today forward; it waits or stands.
        assert proposed.json()["error"]["fields"][0]["code"] == "auction_already_entered"

    calendar = (await api.http.get(PATH, headers=maker)).json()["auctions"]
    expected = {
        product: min(
            (
                a["auction_date"]
                for a in calendar
                if a["product_code"] == product
                and a["state"] in ("standing", "cancelling")
                and a["auction_date"] >= today().isoformat()
            ),
            default=None,
        )
        for product in ("TREA-91", "TREA-182")
    }
    positions = (await desk(api, maker))["positions"]
    assert isinstance(positions, list)
    shown = {p["product_code"]: p["next_auction"] for p in positions}
    assert {product: shown[product] for product in expected} == expected


async def test_the_database_holds_the_two_people_and_never_removes_a_change(api: Api) -> None:
    maker_id, maker = await signed_in_staff(api, "finance")
    checker_id, checker = await signed_in_staff(api, "admin")
    waiting = await propose(api, maker, "TREA-91", a_free_day())
    date_id = await standing(api, maker, checker, "TREA-182", a_free_day())
    approve = (
        "UPDATE dealing_auctions SET status = 'approved', decided_by = :who, "
        "decided_at = now() WHERE id = :id"
    )
    statements = [
        # The proposer approving their own.
        (maker_id, approve, maker_id, waiting["id"]),
        # Approving in someone else's name.
        (checker_id, approve, maker_id, waiting["id"]),
        # Changing a decided date.
        (
            checker_id,
            "UPDATE dealing_auctions SET status = 'declined', decided_by = :who, "
            "decided_at = now(), decision_note = 'x' WHERE id = :id",
            checker_id,
            date_id,
        ),
        (
            checker_id,
            "UPDATE dealing_auctions SET auction_date = auction_date + 1 WHERE id = :id",
            checker_id,
            waiting["id"],
        ),
        (checker_id, "DELETE FROM dealing_auctions WHERE id = :id", checker_id, date_id),
        # Cancelling a date that only waits.
        (
            checker_id,
            "INSERT INTO dealing_auctions (id, action, product_code, auction_date, cancels, "
            "reason, proposed_by, proposed_at) SELECT '-Nauctiontest0000001', 'cancel', "
            "product_code, auction_date, id, 'x', :who, now() FROM dealing_auctions "
            "WHERE id = :id",
            checker_id,
            waiting["id"],
        ),
    ]
    for actor_id, statement, who, target in statements:
        with pytest.raises(DBAPIError):
            async with api.database.actor_transaction(Actor("staff", actor_id)) as session:
                await session.execute(text(statement), {"id": target, "who": who})


async def test_two_people_proposing_the_same_day_at_once_enter_it_once(api: Api) -> None:
    _, first = await signed_in_staff(api, "finance")
    _, second = await signed_in_staff(api, "admin")
    day = a_free_day()
    body = {"product_code": "TREA-91", "auction_date": day.isoformat()}
    answers = await asyncio.gather(
        *(api.http.post(PATH, headers={**who, **key()}, json=body) for who in (first, second))
    )
    assert sorted(a.status_code for a in answers) == [200, 422]
    calendar = (await api.http.get(PATH, headers=first)).json()
    assert [a["state"] for a in entered(calendar, "TREA-91", day)] == ["waiting"]
