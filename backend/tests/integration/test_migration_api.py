"""The staff routes for the migration checks, through HTTP against real PostgreSQL."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.api import COOKIES
from app.modules.identity.service import IdentityService
from app.shared.actors import Actor
from app.shared.push_ids import push_id
from tests.integration.test_identity import HASHER, STAFF_ORIGIN, Api, a_staff_member
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.unit.test_migration_sheet import clients, investments
from tests.unit.test_migration_trial import client, investment

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": STAFF_ORIGIN}
# The same moment the service tests use, so the newest trial is always the one just run.
NOW = datetime(2026, 11, 13, 9, tzinfo=UTC)
# An "as of" day of its own: the service tests enter Finvest 1.0 totals for 2026-11-13, and
# these tests must not depend on running before them.
AS_OF = "2026-09-01"


@dataclass
class Console:
    """One browser. `sign_in` swaps the cookie for another member of staff."""

    http: httpx.AsyncClient
    identity: IdentityService
    database: Database

    async def sign_in(self, role: str) -> str:
        # The sign-in tests' own set-up: an active member of staff with a password.
        _, staff_id = await a_staff_member(
            Api(self.http, self.identity, FakeClock(NOW), self.database), role
        )
        issued = await self.identity.issue_session(
            Actor("staff", staff_id), second_step="passkey", transport="cookie"
        )
        self.http.cookies.set(COOKIES["staff"], issued.token)
        return staff_id

    async def post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        return await self.http.post(f"/staff/migration{path}", json=body, headers=ORIGIN)

    async def put(self, path: str, body: dict[str, Any]) -> httpx.Response:
        return await self.http.put(f"/staff/migration{path}", json=body, headers=ORIGIN)

    async def overview(self) -> dict[str, Any]:
        response = await self.http.get("/staff/migration")
        assert response.status_code == 200
        return dict(response.json())


@pytest.fixture
async def console(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Console]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"staff_allowed_origins": [STAFF_ORIGIN], "trusted_proxies": ["127.0.0.1"]}
    )
    app = create_app(settings, app_database, FakeClock(NOW), HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Console(http, app.state.identity, app_database)


def code(response: httpx.Response) -> tuple[int, str]:
    return response.status_code, str(response.json()["error"]["code"])


def sheets(stated_interest: int) -> dict[str, Any]:
    """One client with one investment. Over 34 days the formula gives 11,643 before tax."""
    ref = f"INV-{push_id()[-8:].replace('_', 'x').replace('-', 'x')}"
    return {
        "ref": ref.upper(),
        "body": {
            "as_of": AS_OF,
            "clients_csv": clients(client("1")),
            "investments_csv": investments(
                investment(
                    ref,
                    "1",
                    start_date="2026-07-29",
                    maturity_date="2026-10-28",
                    interest_before_tax=str(stated_interest),
                )
            ),
        },
    }


def totals(interest: int, **changes: Any) -> dict[str, Any]:
    return {
        "as_of": AS_OF,
        "clients": 1,
        "verified_identities": 1,
        "running_investments": 1,
        "pending_orders": 0,
        "invested": [{"currency": "UGX", "minor": 1_000_000}],
        "interest_before_tax": [{"currency": "UGX", "minor": interest}],
    } | changes


async def test_the_checks_run_from_a_trial_to_a_sign_off_by_two_people(console: Console) -> None:
    operations = await console.sign_in("operations")
    case = sheets(11_700)
    ran = await console.post("/trials", case["body"])
    assert ran.status_code == 200, ran.text
    seen = ran.json()
    key = f"investment:{case['ref']}"
    assert seen["trial"]["ran_by"] == operations
    assert (seen["trial"]["error_count"], seen["trial"]["problems"]) == (0, [])
    assert seen["lines"] == []
    assert seen["differences"] == [
        {
            "key": key,
            "figures": [11_700, 11_643, 1_746, 1_746],
            "sign_off": None,
            "row": 2,
            "currency": "UGX",
        }
    ]
    assert seen["blockers"] == ["totals_not_entered", "difference_not_signed_off"]
    assert seen["allowed"] == ["run_trial", "propose_switch"]
    # Nothing in the answer comes from a cell of the spreadsheet.
    assert "Nakato" not in ran.text and "+256" not in ran.text

    finance = await console.sign_in("finance")
    entered = await console.put("/control-totals", totals(11_643))
    assert entered.status_code == 200
    assert entered.json()["allowed"] == ["enter_totals", "sign_off", "decide_switch"]
    # The totals are shown for checking, and are not used until someone else confirms them.
    entry = entered.json()["totals"]
    assert (entry["entered_by"], entry["confirmed_by"]) == (finance, None)
    assert entry["interest_before_tax"] == [{"currency": "UGX", "minor": 11_643}]
    assert entered.json()["lines"] == []
    assert entered.json()["blockers"] == ["totals_not_confirmed", "difference_not_signed_off"]
    confirmation = f"/control-totals/{entry['id']}/confirmation"
    assert code(await console.post(confirmation, {})) == (403, "forbidden")
    checker = await console.sign_in("finance")
    entered = await console.post(confirmation, {})
    assert entered.status_code == 200
    assert entered.json()["totals"]["confirmed_by"] == checker
    assert entered.json()["blockers"] == ["difference_not_signed_off"]
    assert code(await console.post(confirmation, {})) == (409, "conflict")
    assert {
        "key": "total:interest_before_tax:UGX",
        "figure": "interest_before_tax",
        "currency": "UGX",
        "old": 11_643,
        "new": 11_643,
        "agrees": True,
    } in entered.json()["lines"]

    proposed = await console.post("/sign-offs", {"key": key, "reason": "Counted the start date"})
    assert proposed.status_code == 201
    sign_off = proposed.json()
    assert (sign_off["state"], sign_off["proposed_by"]) == ("proposed", checker)
    assert sign_off["figures"] == [11_700, 11_643, 1_746, 1_746]

    # The person who proposed it cannot approve it.
    own = await console.post(f"/sign-offs/{sign_off['id']}/decision", {"approve": True})
    assert code(own) == (403, "forbidden")

    admin = await console.sign_in("admin")
    approved = await console.post(f"/sign-offs/{sign_off['id']}/decision", {"approve": True})
    assert approved.status_code == 200
    assert (approved.json()["state"], approved.json()["decided_by"]) == ("approved", admin)
    assert approved.json()["decision_reason"] is None
    again = await console.post(f"/sign-offs/{sign_off['id']}/decision", {"approve": True})
    assert code(again) == (409, "conflict")

    history = await console.http.get(
        "/staff/migration/trials", params={"date_from": AS_OF, "date_to": AS_OF}
    )
    assert history.status_code == 200
    assert history.json()["trials"][-1]["investments_differing"] == 1
    assert history.json()["trials"][-1]["totals_confirmed"] is True

    state = await console.overview()
    assert state["blockers"] == []
    assert state["differences"][0]["sign_off"]["state"] == "approved"
    assert state["allowed"] == [
        "run_trial",
        "propose_switch",
        "enter_totals",
        "sign_off",
        "decide_switch",
    ]


async def test_each_route_checks_its_own_permission(console: Console) -> None:
    case = sheets(11_643)
    decision = f"/sign-offs/{push_id()}/decision"
    unsigned = await console.http.get("/staff/migration")
    assert code(unsigned) == (401, "unauthorized")

    await console.sign_in("support")
    assert code(await console.http.get("/staff/migration")) == (403, "forbidden")

    await console.sign_in("operations")
    await console.overview()
    assert code(await console.put("/control-totals", totals(1))) == (403, "forbidden")
    assert code(await console.post("/sign-offs", {"key": "k", "reason": "r"})) == (403, "forbidden")
    assert code(await console.post(decision, {"approve": True})) == (403, "forbidden")
    confirmation = f"/control-totals/{push_id()}/confirmation"
    assert code(await console.post(confirmation, {})) == (403, "forbidden")

    await console.sign_in("finance")
    assert code(await console.post(confirmation, {})) == (404, "not_found")
    await console.overview()
    assert code(await console.post("/trials", case["body"])) == (403, "forbidden")
    assert code(await console.post(decision, {"approve": True})) == (404, "not_found")

    await console.sign_in("compliance")
    assert code(await console.http.get("/staff/migration")) == (403, "forbidden")


async def test_a_change_from_another_site_is_refused(console: Console) -> None:
    await console.sign_in("admin")
    case = sheets(11_643)
    for headers in ({}, {"Origin": "https://example.com"}):
        response = await console.http.post(
            "/staff/migration/trials", json=case["body"], headers=headers
        )
        assert code(response) == (403, "forbidden")


async def test_requests_that_are_not_valid_are_refused_without_echoing_them(
    console: Console,
) -> None:
    await console.sign_in("admin")
    case = sheets(11_643)

    future = await console.post("/trials", case["body"] | {"as_of": "2026-11-14"})
    assert code(future) == (422, "validation_failed")

    # Amounts are whole numbers: not text, not fractions, not negative, and one per currency.
    for change in (
        {"clients": "1"},
        {"clients": 1.5},
        {"clients": -1},
        {"clients": 2**53},
        {"invested": [{"currency": "UGX", "minor": 1}, {"currency": "UGX", "minor": 2}]},
        {"invested": [{"currency": "KES", "minor": 1}]},
        {"extra": 1},
    ):
        response = await console.put("/control-totals", totals(1, **change))
        assert code(response) == (422, "validation_failed"), change

    unknown = await console.post("/sign-offs", {"key": "investment:NOT-THERE", "reason": "Why"})
    assert code(unknown) == (422, "validation_failed")
    assert unknown.json()["error"]["fields"] == [{"field_path": ["key"], "code": "does_not_differ"}]
    blank = await console.post("/sign-offs", {"key": "total:clients", "reason": ""})
    assert code(blank) == (422, "validation_failed")
    rejected = await console.post(f"/sign-offs/{push_id()}/decision", {"approve": False})
    assert code(rejected) == (404, "not_found")
    assert "NOT-THERE" not in unknown.text
