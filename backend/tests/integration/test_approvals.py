"""The approvals inbox gathers what waits for a second person, by the reader's permissions."""

import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.notifications.domain.email_events import address_hash
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


async def test_locks_lifts_and_team_changes_wait_for_the_right_second_person(
    api: Api, migrator_database: Database
) -> None:
    client = await api.client()
    hashed = await suppressed_address(migrator_database)
    maker = await api.staff("operations")
    lock = await api.http.post(
        f"/staff/clients/{client.id}/locks", json={"kind": "lock", "reason": "Lost phone"}
    )
    assert lock.status_code == 201, lock.text
    lift = await api.http.post(
        f"/staff/email-suppressions/{hashed}/lifts", json={"reason": "Inbox fixed"}
    )
    assert lift.status_code == 201, lift.text
    mine = await inbox(api)
    assert mine[lock.json()["id"]]["kind"] == "client_lock"
    assert mine[lift.json()["id"]]["subject"] == hashed[:12]
    assert not any(mine[i]["can_decide"] for i in (lock.json()["id"], lift.json()["id"]))

    # Support sees both and may lift, but locking is not theirs.
    await api.staff("support")
    theirs = await inbox(api)
    assert theirs[lift.json()["id"]]["can_decide"] is True
    assert theirs[lock.json()["id"]]["can_decide"] is False
    assert maker.id == theirs[lock.json()["id"]]["proposed_by"]

    first = await api.staff("admin")
    target = await api.staff("support")
    await api.as_staff(first)
    change = await api.http.post(
        "/staff/team/changes",
        json={"kind": "deactivate", "staff_id": target.id, "reason": "Left"},
    )
    assert change.status_code == 201, change.text
    await api.staff("admin")
    other = (await inbox(api))[change.json()["id"]]
    assert (other["kind"], other["subject"], other["can_decide"]) == (
        "team_change",
        "deactivate",
        True,
    )


async def suppressed_address(migrator: Database) -> str:
    """A permanent bounce, as the Resend webhook and the worker leave it."""
    hashed = address_hash(f"client.{secrets.token_hex(6)}@example.com")
    svix_id = f"msg_{secrets.token_hex(8)}"
    async with migrator.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO notification_email_events (svix_id, delivery, email_id, recipients, "
                "permanent, received_at, processed_at, outcome) VALUES (:svix, 'bounced', :e, "
                "ARRAY[:hash]::text[], true, now(), now(), 'applied')"
            ),
            {"svix": svix_id, "e": secrets.token_hex(8), "hash": hashed},
        )
        await session.execute(
            text(
                "INSERT INTO notification_email_suppressions (address_hash, reason, svix_id, "
                "created_at) VALUES (:hash, 'bounced', :svix, now())"
            ),
            {"hash": hashed, "svix": svix_id},
        )
    return hashed
