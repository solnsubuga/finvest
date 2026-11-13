"""The staff routes for payout accounts: the queue, opening one, and the two-person approval."""

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from app.infrastructure.database import Database
from app.infrastructure.storage import MemoryStorage
from app.modules.audit import service as audit
from app.modules.payouts import destinations
from app.shared.actors import Actor
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.kyc import VERIFIED_NAME, verify

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 2048
BASE = "/staff/payout-destinations"


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, storage=MemoryStorage()) as running:
        # A check and an approval are stamped against the database clock.
        running.clock.current = datetime.now(UTC)
        yield running


async def in_review(
    api: Api, *, verified: bool = True, proof: str = "bank_letter"
) -> tuple[Actor, str, str]:
    """A client's bank account with proof uploaded. Returns the client, its ID and number."""
    owner = await api.client()
    if verified:
        await verify(api.database, owner, api.clock)
    number = str(10**12 + secrets.randbelow(10**12))
    added = await api.http.post(
        "/client/payout-destinations",
        json={
            "kind": "bank",
            "holder_name": "Amara Nakato",
            "bank_code": "stanbic",
            "account_number": number,
        },
    )
    assert added.status_code == 201, added.text
    destination_id = str(added.json()["id"])
    async with api.database.actor_transaction(owner) as session:
        await destinations.confirm_code(session, owner, destination_id, api.clock)
    sent = await api.http.post(
        f"/client/payout-destinations/{destination_id}/proof",
        data={"proof_kind": proof},
        files={"file": ("proof.png", PNG, "image/png")},
    )
    assert sent.status_code == 200, sent.text
    return owner, destination_id, number


async def test_one_person_checks_an_account_and_another_approves_it(api: Api) -> None:
    owner, destination_id, number = await in_review(api)
    checker = await api.staff("operations")
    queue = (await api.http.get(BASE)).json()
    [waiting] = [item for item in queue if item["id"] == destination_id]
    assert (waiting["status"], waiting["check"], waiting["client_id"]) == (
        "in_review",
        None,
        owner.id,
    )
    assert number not in str(queue)

    opened = await api.http.get(f"{BASE}/{destination_id}")
    assert opened.status_code == 200, opened.text
    detail = opened.json()
    assert (detail["account_number"], detail["id_name"]) == (number, VERIFIED_NAME)
    assert detail["proof_link"]["url"].startswith(f"memory://payouts/{destination_id}/")

    checked = await api.http.post(
        f"{BASE}/{destination_id}/check",
        json={"proof_name": "Amara Nakato", "reason": "Letter names her"},
    )
    assert checked.status_code == 200, checked.text
    check = checked.json()["check"]
    assert (check["name_match"], check["checked_by"]) == ("exact", checker.id)
    assert checked.json()["status"] == "in_review"

    own = await api.http.post(f"{BASE}/{destination_id}/approve", json={"reason": "Fine"})
    assert own.status_code == 403
    assert own.json()["error"]["fields"][0]["code"] == "checked_by_you"

    approver = await api.staff("compliance")
    waiting = next(
        item for item in (await api.http.get(BASE)).json() if item["id"] == destination_id
    )
    assert waiting["check"]["checked_by"] == checker.id
    approved = await api.http.post(
        f"{BASE}/{destination_id}/approve", json={"reason": "Agree with the check"}
    )
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["approved_by"]) == ("approved", approver.id)
    assert approved.json()["payable_from"] is not None
    assert destination_id not in [item["id"] for item in (await api.http.get(BASE)).json()]

    async with api.database.actor_transaction(approver) as session:
        events = await audit.events_for_resource(session, "payout_destination", destination_id)
    actions = [(event.action, event.actor_id) for event in events]
    assert ("payouts.destination_opened", checker.id) in actions
    assert actions[:2] == [
        ("payouts.destination_approved", approver.id),
        ("payouts.destination_checked", checker.id),
    ]


async def test_a_check_is_refused_for_a_different_name_or_an_unverified_client(api: Api) -> None:
    _, destination_id, _ = await in_review(api)
    await api.staff("operations")
    different = await api.http.post(
        f"{BASE}/{destination_id}/check", json={"proof_name": "Okello Peter", "reason": "Read"}
    )
    assert different.status_code == 422
    problem = different.json()["error"]["fields"][0]
    assert (problem["field_path"][-1], problem["code"]) == ("proof_name", "name_different")
    unchecked = await api.http.post(f"{BASE}/{destination_id}/approve", json={"reason": "Fine"})
    assert unchecked.status_code == 409
    assert unchecked.json()["error"]["fields"][0]["code"] == "destination_not_checked"

    _, unverified, _ = await in_review(api, verified=False)
    await api.staff("operations")
    assert (await api.http.get(f"{BASE}/{unverified}")).json()["id_name"] is None
    refused = await api.http.post(
        f"{BASE}/{unverified}/check", json={"proof_name": VERIFIED_NAME, "reason": "Read"}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["fields"][0]["code"] == "client_not_verified"


async def test_one_person_refuses_proof_and_staff_remove_an_account_with_a_reason(
    api: Api,
) -> None:
    _, destination_id, _ = await in_review(api)
    await api.staff("compliance")
    refused = await api.http.post(
        f"{BASE}/{destination_id}/reject",
        json={"rejection_reason": "proof_unreadable", "reason": "Blurred photo"},
    )
    assert refused.status_code == 200, refused.text
    assert (refused.json()["status"], refused.json()["rejection_reason"]) == (
        "rejected",
        "proof_unreadable",
    )
    again = await api.http.post(
        f"{BASE}/{destination_id}/reject",
        json={"rejection_reason": "proof_unreadable", "reason": "Again"},
    )
    assert again.status_code == 409

    without_reason = await api.http.post(f"{BASE}/{destination_id}/remove", json={"reason": ""})
    assert without_reason.status_code == 422
    removed = await api.http.post(
        f"{BASE}/{destination_id}/remove", json={"reason": "Client asked us by phone"}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["status"] == "removed"


async def test_the_staff_routes_need_the_right_permission(api: Api) -> None:
    _, destination_id, _ = await in_review(api)
    decisions = [
        ("POST", f"{BASE}/{destination_id}/check", {"proof_name": "Amara Nakato", "reason": "R"}),
        ("POST", f"{BASE}/{destination_id}/approve", {"reason": "R"}),
        (
            "POST",
            f"{BASE}/{destination_id}/reject",
            {"rejection_reason": "proof_unreadable", "reason": "R"},
        ),
        ("POST", f"{BASE}/{destination_id}/remove", {"reason": "R"}),
    ]
    # Support cannot see the queue. Finance sees it but decides nothing.
    await api.staff("support")
    assert (await api.http.get(BASE)).status_code == 403
    assert (await api.http.get(f"{BASE}/{destination_id}")).status_code == 403
    await api.staff("finance")
    assert (await api.http.get(BASE)).status_code == 200
    for method, path, body in decisions:
        assert (await api.http.request(method, path, json=body)).status_code == 403, path
    # A client is not staff at all.
    await api.client()
    assert (await api.http.get(BASE)).status_code in (401, 403)
    await api.staff("admin")
    opened = (await api.http.get(f"{BASE}/{destination_id}")).json()
    assert (opened["status"], opened["check"]) == ("in_review", None)


async def test_a_bank_statement_is_checked_with_its_date(api: Api) -> None:
    _, destination_id, _ = await in_review(api, proof="bank_statement")
    await api.staff("operations")
    path = f"{BASE}/{destination_id}/check"
    body = {"proof_name": "Amara Nakato", "reason": "Statement names her"}
    missing = await api.http.post(path, json=body)
    assert missing.status_code == 422
    problem = missing.json()["error"]["fields"][0]
    assert (problem["field_path"][-1], problem["code"]) == (
        "statement_date",
        "statement_date_required",
    )
    too_old = await api.http.post(path, json={**body, "statement_date": "2020-01-31"})
    assert too_old.status_code == 422
    assert too_old.json()["error"]["fields"][0]["code"] == "statement_too_old"

    dated = api.clock.now().date().replace(day=1).isoformat()
    checked = await api.http.post(path, json={**body, "statement_date": dated})
    assert checked.status_code == 200, checked.text
    assert checked.json()["check"]["statement_date"] == dated
    await api.staff("compliance")
    approved = await api.http.post(f"{BASE}/{destination_id}/approve", json={"reason": "Agree"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"


async def test_a_checked_account_waits_in_the_approvals_inbox(api: Api) -> None:
    _, destination_id, number = await in_review(api)
    checker = await api.staff("operations")
    inbox = (await api.http.get("/staff/approvals")).json()["items"]
    # Waiting for a first check is the review queue's business, not the inbox's.
    assert destination_id not in [item["id"] for item in inbox]
    checked = await api.http.post(
        f"{BASE}/{destination_id}/check",
        json={"proof_name": "Amara Nakato", "reason": "Letter names her"},
    )
    assert checked.status_code == 200, checked.text

    own = (await api.http.get("/staff/approvals")).json()["items"]
    [mine] = [item for item in own if item["id"] == destination_id]
    assert (mine["kind"], mine["proposed_by"], mine["yours"], mine["can_decide"]) == (
        "payout_account",
        checker.id,
        True,
        False,
    )
    # The subject is the kind of account, never the number or the holder's name.
    assert mine["subject"] == "bank"
    assert number not in str(own) and "Amara" not in str(own)

    await api.staff("compliance")
    theirs = (await api.http.get("/staff/approvals")).json()["items"]
    [waiting] = [item for item in theirs if item["id"] == destination_id]
    assert (waiting["yours"], waiting["can_decide"]) == (False, True)
    approved = await api.http.post(f"{BASE}/{destination_id}/approve", json={"reason": "Agree"})
    assert approved.status_code == 200, approved.text
    after = (await api.http.get("/staff/approvals")).json()["items"]
    assert destination_id not in [item["id"] for item in after]
