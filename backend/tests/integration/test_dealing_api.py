"""The dealing desk over HTTP, against real PostgreSQL as finvest_app: who may call it, that
every command runs once per key, and the whole order from preparing to settling."""

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.dealing.orders import ORDER_RESOURCE
from app.modules.identity.api import COOKIES
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from app.shared.push_ids import push_id
from tests.integration.test_dealing_settlement import fund_bank
from tests.support.database import TestDatabase
from tests.support.http import HASHER, PASSWORD, STAFF_ORIGIN
from tests.support.investments import PRESENT, today
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration
M = 1_000_000


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    database: Database


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"staff_allowed_origins": [STAFF_ORIGIN], "trusted_proxies": ["127.0.0.1"]}
    )
    # The real time: a result is refused when it settles after today.
    app = create_app(settings, app_database, PRESENT, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, app_database)


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def signed_in_staff(api: Api, role: str = "admin") -> tuple[str, dict[str, str]]:
    """A member of staff with a console session, and the headers the console would send."""
    staff_id = await api.identity.invite_staff(
        email=f"dealer{secrets.randbelow(10**9)}@finvestug.com",
        name="Ada Nakato",
        role=role,  # type: ignore[arg-type]
        created_by=None,
    )
    # Test set-up only, as in tests.support.http: active, with a password.
    async with api.database.transaction() as session:
        await session.execute(
            text(
                "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                "password_changed_at = created_at WHERE id = :id"
            ),
            {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
        )
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    return staff_id, {"Cookie": f"{COOKIES['staff']}={issued.token}", "Origin": STAFF_ORIGIN}


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{push_id()}"}


async def desk(api: Api, headers: dict[str, str]) -> dict[str, object]:
    response = await api.http.get("/staff/dealing/desk", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def order_in(body: dict[str, object], order_id: str) -> dict[str, object]:
    orders = body["orders"]
    assert isinstance(orders, list)
    return next(order for order in orders if order["id"] == order_id)


async def test_the_desk_is_for_staff_who_hold_the_dealing_permission(api: Api) -> None:
    _, admin = await signed_in_staff(api)
    _, finance = await signed_in_staff(api, "finance")
    _, operations = await signed_in_staff(api, "operations")
    body = await desk(api, admin)
    assert (await desk(api, finance)).keys() == body.keys()
    assert body["matches_custodian"] is None
    buffer = body["buffer"]
    assert isinstance(buffer, dict)
    assert buffer["floor"] == {"minor": 100_000_000, "currency": "UGX"}
    positions = body["positions"]
    assert isinstance(positions, list)
    assert {p["product_code"] for p in positions} >= {"TREA-91", "TREA-182"}
    refused = await api.http.get("/staff/dealing/desk", headers=operations)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    assert (await api.http.get("/staff/dealing/desk")).status_code == 401


async def test_an_order_from_preparing_to_settling_over_http(api: Api) -> None:
    daniel_id, daniel = await signed_in_staff(api)
    _, grace = await signed_in_staff(api)
    _, moses = await signed_in_staff(api)
    order = {"kind": "auction_bid", "product_code": "TREA-182", "face_minor": M, "currency": "UGX"}

    once = key()
    first = await api.http.post("/staff/dealing/orders", headers={**daniel, **once}, json=order)
    again = await api.http.post("/staff/dealing/orders", headers={**daniel, **once}, json=order)
    assert first.status_code == again.status_code == 200, first.text
    mine = [o for o in first.json()["orders"] if o["prepared_by"] == daniel_id]
    assert len(mine) == 1
    assert [o for o in again.json()["orders"] if o["prepared_by"] == daniel_id] == mine
    order_id = mine[0]["id"]
    assert mine[0]["allowed"] == ["withdraw"]
    assert order_in(await desk(api, grace), order_id)["allowed"] == ["approve", "send_back"]

    reused = await api.http.post(
        "/staff/dealing/orders", headers={**daniel, **once}, json={**order, "face_minor": 2 * M}
    )
    assert code(reused) == "idempotency_key_reused"
    own = await api.http.post(
        f"/staff/dealing/orders/{order_id}/approve", headers={**daniel, **key()}
    )
    assert (own.status_code, code(own)) == (403, "forbidden")
    approved = await api.http.post(
        f"/staff/dealing/orders/{order_id}/approve", headers={**grace, **key()}
    )
    assert order_in(approved.json(), order_id)["status"] == "approved"

    settled_on = today()
    fill = {
        "face_minor": M,
        "amount_minor": 970_000,
        "settled_on": settled_on.isoformat(),
        "matures_on": (settled_on + timedelta(days=180)).isoformat(),
        "custodian_reference": f"CSD-{push_id()[-8:]}",
    }
    recorded = await api.http.post(
        f"/staff/dealing/orders/{order_id}/result", headers={**grace, **key()}, json={"fill": fill}
    )
    line = order_in(recorded.json(), order_id)
    assert (line["status"], line["result"]["amount"]["minor"]) == ("result_recorded", 970_000)  # type: ignore[index]
    assert "confirm_result" not in line["allowed"]  # type: ignore[operator]

    async with api.database.actor_transaction(new_staff()) as session:
        events = await events_for_resource(session, ORDER_RESOURCE, order_id)
    result_event = next(e for e in events if e.action == "dealing.order.result_recorded")
    assert result_event.details["amount_minor"] == 970_000
    assert result_event.details["custodian_reference"] == fill["custodian_reference"]
    await fund_bank(api.database, 970_000)
    confirm = {**moses, **key()}
    settled = await api.http.post(
        f"/staff/dealing/orders/{order_id}/confirm-result", headers=confirm
    )
    replayed = await api.http.post(
        f"/staff/dealing/orders/{order_id}/confirm-result", headers=confirm
    )
    assert settled.status_code == replayed.status_code == 200, settled.text
    # A settled order is finished, so it leaves the open orders.
    assert order_id not in {o["id"] for o in replayed.json()["orders"]}


async def test_commands_need_a_key_a_note_and_exact_types(api: Api) -> None:
    _, staff = await signed_in_staff(api)
    order = {"kind": "auction_bid", "product_code": "TREA-182", "face_minor": M, "currency": "UGX"}
    no_key = await api.http.post("/staff/dealing/orders", headers=staff, json=order)
    assert code(no_key) == "idempotency_key_required"
    as_text = await api.http.post(
        "/staff/dealing/orders", headers={**staff, **key()}, json={**order, "face_minor": "1000000"}
    )
    assert as_text.status_code == 422
    not_a_lot = await api.http.post(
        "/staff/dealing/orders", headers={**staff, **key()}, json={**order, "face_minor": M + 1}
    )
    assert code(not_a_lot) == "validation_failed"
    prepared = await api.http.post("/staff/dealing/orders", headers={**staff, **key()}, json=order)
    order_id = prepared.json()["orders"][-1]["id"]
    _, other = await signed_in_staff(api)
    blank = await api.http.post(
        f"/staff/dealing/orders/{order_id}/send-back", headers={**other, **key()}, json={"note": ""}
    )
    assert blank.status_code == 422
    missing = await api.http.post(
        f"/staff/dealing/orders/{push_id()}/approve", headers={**other, **key()}
    )
    assert (missing.status_code, code(missing)) == (404, "not_found")
    withdrawn = await api.http.post(
        f"/staff/dealing/orders/{order_id}/withdraw", headers={**staff, **key()}
    )
    assert order_id not in {o["id"] for o in withdrawn.json()["orders"]}
