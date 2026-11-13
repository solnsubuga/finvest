"""The approvals inbox gathers what waits for a second person, by the reader's permissions."""

import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.infrastructure.database import Database
from app.modules.reconciliation import transfers
from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.business_day import business_date
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

WORDING = "/staff/notification-wordings/payment_confirmed/proposals"
CHANGE = {
    "wording": {"title": "Money in", "body": "{amount} arrived.", "sms": "Finvest: {amount}."},
    "reason": "Shorter",
}
RATE = {
    "product_code": "TREA-182",
    "annual_rate_bps": 1400,
    "tax_bps": 1500,
    "effective_from": "2031-07-01",
    "reason": "Auction result",
}


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        yield running


async def inbox(api: Api) -> dict[str, dict[str, Any]]:
    response = await api.http.get("/staff/approvals")
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["items"]}


async def test_a_rate_change_waits_for_a_second_person_who_may_see_rates(api: Api) -> None:
    proposer = await api.staff("finance")
    proposed = await api.http.post("/staff/rate-proposals", json=RATE)
    assert proposed.status_code == 201
    proposal_id = proposed.json()["id"]
    try:
        mine = (await inbox(api))[proposal_id]
        assert mine["kind"] == "rate_change"
        assert mine["subject"] == "TREA-182"
        assert mine["proposed_by"] == proposer.id
        assert (mine["yours"], mine["can_decide"]) == (True, False)

        await api.staff("finance")
        theirs = (await inbox(api))[proposal_id]
        assert (theirs["yours"], theirs["can_decide"]) == (False, True)

        # Support cannot open rates, so the change is not in their inbox at all.
        await api.staff("support")
        assert proposal_id not in await inbox(api)
    finally:
        await api.as_staff(proposer)
        withdrawn = await api.http.post(f"/staff/rate-proposals/{proposal_id}/withdraw")
        assert withdrawn.status_code == 200
    await api.staff("finance")
    assert proposal_id not in await inbox(api)


async def test_a_wording_change_shows_to_who_sees_settings_and_only_admins_decide(
    api: Api,
) -> None:
    proposer = await api.staff("admin")
    created = await api.http.post(WORDING, json=CHANGE)
    assert created.status_code == 201
    proposal_id = created.json()["id"]
    try:
        await api.staff("admin")
        item = (await inbox(api))[proposal_id]
        assert (item["kind"], item["subject"]) == ("wording_change", "payment_confirmed")
        assert item["can_decide"]
        # Operations do not see settings.
        await api.staff("operations")
        assert proposal_id not in await inbox(api)
    finally:
        await api.as_staff(proposer)
        decision = f"/staff/notification-wordings/proposals/{proposal_id}/decision"
        withdrawn = await api.http.post(decision, json={"decision": "withdrawn", "reason": "Test"})
        assert withdrawn.status_code == 200


async def test_a_pot_transfer_waits_in_the_reconciliation_inbox(api: Api) -> None:
    proposer = await api.staff("operations")
    day = business_date(api.clock.now())
    async with api.database.actor_transaction(proposer) as session:
        proposed = await transfers.propose(
            session,
            proposer,
            from_pot=(StatementSource.IOTEC, "mtn"),
            to_pot=(StatementSource.BANK, "trust"),
            sent=ugx(1_000_000),
            received=ugx(990_000),
            value_date=day,
            provider_reference=f"IOT-{secrets.token_hex(6)}",
            reason="Daily sweep of collected mobile money",
            clock=api.clock,
        )
    checker = await api.staff("finance")
    try:
        item = (await inbox(api))[proposed.id]
        assert (item["kind"], item["subject"]) == ("pot_transfer", day.isoformat())
        assert (item["yours"], item["can_decide"]) == (False, True)
        await api.staff("compliance")
        assert proposed.id not in await inbox(api)
    finally:
        async with api.database.actor_transaction(checker) as session:
            await transfers.decline(session, checker, proposed.id, "Test only", api.clock)


async def test_every_role_has_an_inbox_and_signed_out_callers_do_not(api: Api) -> None:
    for role in ("admin", "operations", "finance", "compliance", "support"):
        await api.staff(role)
        response = await api.http.get("/staff/approvals")
        assert response.status_code == 200, role
        items = response.json()["items"]
        assert [i["proposed_at"] for i in items] == sorted(i["proposed_at"] for i in items)
    api.http.cookies.clear()
    assert (await api.http.get("/staff/approvals")).status_code == 401
    await api.client()
    assert (await api.http.get("/staff/approvals")).status_code in (401, 403)
