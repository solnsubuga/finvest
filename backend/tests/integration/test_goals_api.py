"""The goal routes, called over HTTP as the client app calls them."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api as StaffApi
from tests.support.http import running_api
from tests.support.ledger import key

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_numbers = iter(range(780_000_000, 790_000_000))


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    clock = FakeClock(datetime(2026, 11, 13, 9, tzinfo=UTC))
    app = create_app(test_database.settings("finvest_app"), app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity)


async def signed_in(api: Api) -> dict[str, str]:
    phone = f"+256{next(_numbers)}"
    await api.identity.create_client(phone=phone, email=None, password=PASSWORD)
    challenge = await api.identity.start_login("client", phone, PASSWORD, address=None)
    issued = await api.identity.open_session(
        challenge.id, "client", second_step="passkey", transport="token"
    )
    return {"Authorization": f"Bearer {issued.token}"}


GOAL = {
    "name": "School fees, Term 1",
    "category": "school_fees",
    "target_minor": 2_000_000,
    "target_date": "2027-11-13",
}


def keys_of(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


async def test_a_client_plans_sets_up_reads_and_closes_a_goal(api: Api) -> None:
    headers = await signed_in(api)
    plan = await api.http.get(
        "/client/goals/plan",
        params={"target_minor": 2_000_000, "target_date": "2027-11-13"},
        headers=headers,
    )
    assert plan.status_code == 200
    assert plan.json()["product_code"] == "TREA-91"
    assert plan.json()["rate_percent"] == "10.63"

    created = await api.http.post(
        "/client/goals", json=GOAL, headers={**headers, "Idempotency-Key": key()}
    )
    assert created.status_code == 201
    goal = created.json()
    assert goal["plan"] == plan.json()
    assert goal["progress"]["standing"] == "on_track"
    # A client never sees a tax rate or a figure before tax.
    assert not {k for k in keys_of(goal) if "tax" in k or "gross" in k or "bps" in k}

    listed = await api.http.get("/client/goals", headers=headers)
    assert [g["goal_id"] for g in listed.json()["goals"]] == [goal["goal_id"]]

    changed = await api.http.put(
        f"/client/goals/{goal['goal_id']}",
        json={**GOAL, "name": "Fees for Term 2", "target_date": None},
        headers=headers,
    )
    assert changed.status_code == 200
    assert changed.json()["name"] == "Fees for Term 2"
    assert changed.json()["plan"] is None

    closed = await api.http.post(f"/client/goals/{goal['goal_id']}/close", headers=headers)
    assert closed.json()["status"] == "closed"


async def test_setting_up_a_goal_needs_a_key_and_a_session(api: Api) -> None:
    headers = await signed_in(api)
    response = await api.http.post("/client/goals", json=GOAL, headers=headers)
    assert response.status_code == 428
    response = await api.http.post("/client/goals", json=GOAL, headers={"Idempotency-Key": key()})
    assert response.status_code == 401


async def test_an_amount_as_text_or_a_fraction_is_refused(api: Api) -> None:
    headers = {**(await signed_in(api)), "Idempotency-Key": key()}
    for target in ("2000000", 2_000_000.0):
        response = await api.http.post(
            "/client/goals", json={**GOAL, "target_minor": target}, headers=headers
        )
        assert response.status_code == 422


async def test_another_clients_goal_is_not_found(api: Api) -> None:
    owner, other = await signed_in(api), await signed_in(api)
    created = await api.http.post(
        "/client/goals", json=GOAL, headers={**owner, "Idempotency-Key": key()}
    )
    assert created.status_code == 201, created.json()
    goal_id = created.json()["goal_id"]
    assert (await api.http.get(f"/client/goals/{goal_id}", headers=other)).status_code == 404
    response = await api.http.post(
        f"/client/goals/{goal_id}/savings",
        json={"amount_minor": 200_000},
        headers={**other, "Idempotency-Key": key()},
    )
    assert response.status_code == 404


async def test_a_client_session_does_not_open_the_staff_routes(api: Api) -> None:
    response = await api.http.get("/staff/goals", headers=await signed_in(api))
    assert response.status_code == 401


@pytest.fixture
async def staff_api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[StaffApi]:
    async with running_api(test_database, app_database) as running:
        yield running


@pytest.mark.parametrize("role", ["admin", "operations", "finance"])
async def test_staff_with_goals_view_open_the_staff_routes(staff_api: StaffApi, role: str) -> None:
    await staff_api.staff(role)
    response = await staff_api.http.get("/staff/goals")
    assert response.status_code == 200, response.json()


@pytest.mark.parametrize("role", ["compliance", "support"])
async def test_staff_without_goals_view_are_refused(staff_api: StaffApi, role: str) -> None:
    await staff_api.staff(role)
    assert (await staff_api.http.get("/staff/goals")).status_code == 403
    assert (await staff_api.http.get(f"/staff/goals/{key()}")).status_code == 403
