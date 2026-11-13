"""The automatic investing routes, called over HTTP as the client app calls them."""

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import key
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
# Friday 13 November 2026, 9:00 in Kampala.
CLOCK = FakeClock(datetime(2026, 11, 13, 6, tzinfo=UTC))
PLAN = {
    "product_code": "TREA-91",
    "amount_minor": 200_000,
    "frequency": "monthly",
    "day": 28,
    "network": "mtn",
}


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    database: Database

    async def client(self, *, verified: bool = True) -> dict[str, str]:
        """Sign in a new client. Returns the headers that carry their session."""
        phone = "+25677" + "".join(secrets.choice("0123456789") for _ in range(7))
        client_id = await self.identity.create_client(phone=phone, email=None, password=PASSWORD)
        actor = Actor("client", client_id)
        if verified:
            async with self.database.actor_transaction(actor) as session:
                await ready_draft(session, actor, CLOCK)
                check = await kyc.submit(session, SEALER, actor, ManualProvider(), CLOCK)
            reviewer = new_staff()
            async with self.database.actor_transaction(reviewer) as session:
                await kyc.approve(session, SEALER, reviewer, check.id, "Documents match", CLOCK)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="token")
        return {"Authorization": f"Bearer {issued.token}"}


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    app = create_app(test_database.settings("finvest_app"), app_database, CLOCK, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, app_database)


async def test_a_client_sets_up_reads_pauses_resumes_and_stops_a_plan(api: Api) -> None:
    headers = await api.client()
    created = await api.http.post(
        "/client/autoinvest/plans", json=PLAN, headers={**headers, "Idempotency-Key": key()}
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert {k: plan[k] for k in PLAN} == PLAN
    assert plan["product_name"]
    assert plan["currency"] == "UGX"
    assert plan["status"] == "active"
    assert plan["next_run_on"] == "2026-11-28"
    assert plan["payer_msisdn"].startswith("+25677")
    assert plan["at_maturity"] is None
    assert plan["runs"] == []
    # A client never sees a tax rate or a figure before tax.
    assert not {k for k in plan if "tax" in k or "gross" in k or "bps" in k}
    path = f"/client/autoinvest/plans/{plan['plan_id']}"

    listed = await api.http.get("/client/autoinvest/plans", headers=headers)
    assert [p["plan_id"] for p in listed.json()["plans"]] == [plan["plan_id"]]
    assert (await api.http.get(path, headers=headers)).json() == plan

    paused = await api.http.post(f"{path}/pause", headers=headers)
    assert (paused.json()["status"], paused.json()["next_run_on"]) == ("paused", None)
    # Sending it again changes nothing.
    assert (await api.http.post(f"{path}/pause", headers=headers)).json() == paused.json()
    resumed = await api.http.post(f"{path}/resume", headers=headers)
    assert (resumed.json()["status"], resumed.json()["next_run_on"]) == ("active", "2026-11-28")

    changed = await api.http.put(
        path, json={**PLAN, "frequency": "weekly", "day": 1}, headers=headers
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["frequency"], changed.json()["next_run_on"]) == ("weekly", "2026-11-16")

    stopped = await api.http.post(f"{path}/stop", headers=headers)
    assert stopped.json()["status"] == "stopped"
    assert (await api.http.post(f"{path}/resume", headers=headers)).status_code == 409
    assert (await api.http.get("/client/autoinvest/plans", headers=headers)).json() == {"plans": []}
    with_stopped = await api.http.get(
        "/client/autoinvest/plans", params={"include_stopped": "true"}, headers=headers
    )
    assert [p["status"] for p in with_stopped.json()["plans"]] == ["stopped"]


async def test_the_same_key_returns_the_same_plan_and_other_details_are_refused(
    api: Api,
) -> None:
    headers = {**(await api.client()), "Idempotency-Key": key()}
    first = await api.http.post("/client/autoinvest/plans", json=PLAN, headers=headers)
    again = await api.http.post("/client/autoinvest/plans", json=PLAN, headers=headers)
    assert first.json()["plan_id"] == again.json()["plan_id"]
    other = await api.http.post(
        "/client/autoinvest/plans", json={**PLAN, "amount_minor": 300_000}, headers=headers
    )
    assert other.status_code == 422
    assert other.json()["error"]["code"] == "idempotency_key_reused"


async def test_setting_up_a_plan_needs_a_key_a_session_and_an_identity_check(api: Api) -> None:
    headers = await api.client()
    assert (
        await api.http.post("/client/autoinvest/plans", json=PLAN, headers=headers)
    ).status_code == 428
    assert (
        await api.http.post(
            "/client/autoinvest/plans", json=PLAN, headers={"Idempotency-Key": key()}
        )
    ).status_code == 401
    unverified = {**(await api.client(verified=False)), "Idempotency-Key": key()}
    refused = await api.http.post("/client/autoinvest/plans", json=PLAN, headers=unverified)
    assert refused.status_code == 403


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"amount_minor": "200000"}, None),
        ({"amount_minor": 200_000.0}, None),
        ({"day": 8, "frequency": "weekly"}, "day"),
        ({"amount_minor": 5_000_001}, "amount"),
        ({"phone": "+256701000000"}, "phone"),
    ],
)
async def test_a_plan_no_run_could_be_paid_for_is_refused(
    api: Api, change: dict[str, object], field: str | None
) -> None:
    headers = {**(await api.client()), "Idempotency-Key": key()}
    response = await api.http.post(
        "/client/autoinvest/plans", json={**PLAN, **change}, headers=headers
    )
    assert response.status_code == 422, response.text
    if field is not None:
        problems = response.json()["error"]["fields"]
        assert [problem["field_path"][0] for problem in problems] == [field]


async def test_another_clients_plan_is_not_found(api: Api) -> None:
    owner = {**(await api.client()), "Idempotency-Key": key()}
    plan = (await api.http.post("/client/autoinvest/plans", json=PLAN, headers=owner)).json()
    other = await api.client()
    path = f"/client/autoinvest/plans/{plan['plan_id']}"
    assert (await api.http.get(path, headers=other)).status_code == 404
    assert (await api.http.post(f"{path}/stop", headers=other)).status_code == 404
    assert (await api.http.get("/client/autoinvest/plans", headers=other)).json() == {"plans": []}
