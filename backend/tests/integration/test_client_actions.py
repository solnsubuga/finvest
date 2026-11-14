"""Staff notes on a client, and acting for a client with two people: resetting the app PIN."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.console import actions
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def phone_of(owner: Database, client: Actor) -> str:
    async with owner.transaction() as session:
        return str(
            await session.scalar(
                text("SELECT phone FROM identity_clients WHERE id = :id"), {"id": client.id}
            )
        )


async def queued(owner: Database, recipient: str) -> list[str]:
    async with owner.transaction() as session:
        rows = await session.execute(
            text("SELECT template FROM notification_messages WHERE recipient = :r ORDER BY id"),
            {"r": recipient},
        )
        return [row.template for row in rows]


async def propose(api: Api, client: Actor, kind: str = "pin_reset") -> httpx.Response:
    return await api.http.post(
        f"/staff/clients/{client.id}/actions",
        json={"kind": kind, "reason": "Client called: phone stolen"},
    )


async def decide(api: Api, action_id: str, decision: str = "approved") -> httpx.Response:
    return await api.http.post(
        f"/staff/clients/actions/{action_id}/decision",
        json={"decision": decision, "reason": "Called the client back"},
    )


async def test_staff_write_notes_that_every_staff_member_reads(api: Api) -> None:
    client = await api.client()
    await api.staff("support", name="Grace Auma")
    written = await api.http.post(
        f"/staff/clients/{client.id}/notes", json={"body": "  Chasing a failed payout.  "}
    )
    assert written.status_code == 201, written.text
    assert written.json()["body"] == "Chasing a failed payout."
    assert written.json()["written_by_name"] == "Grace Auma"
    await api.http.post(f"/staff/clients/{client.id}/notes", json={"body": "Resent it."})
    await api.staff("finance")
    page = (await api.http.get(f"/staff/clients/{client.id}/notes")).json()
    assert [note["body"] for note in page["notes"]] == ["Resent it.", "Chasing a failed payout."]
    assert page["can_write"] is True


async def test_a_note_needs_words_and_a_known_client(api: Api) -> None:
    client = await api.client()
    await api.staff("support")
    blank = await api.http.post(f"/staff/clients/{client.id}/notes", json={"body": "   "})
    assert blank.status_code == 422
    long = await api.http.post(f"/staff/clients/{client.id}/notes", json={"body": "x" * 1001})
    assert long.status_code == 422
    nobody = await api.http.post(
        "/staff/clients/-Nabc0000000000000Z9/notes", json={"body": "Hello"}
    )
    assert nobody.status_code == 404


async def test_a_note_is_never_changed_or_deleted_and_clients_never_read_one(api: Api) -> None:
    client = await api.client()
    staff = await api.staff("support")
    note = (
        await api.http.post(f"/staff/clients/{client.id}/notes", json={"body": "Called."})
    ).json()
    for statement in (
        "UPDATE console_client_notes SET body = 'Changed' WHERE id = :id",
        "DELETE FROM console_client_notes WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with api.database.actor_transaction(staff) as session:
                await session.execute(text(statement), {"id": note["id"]})
    async with api.database.actor_transaction(client) as session:
        seen = await session.scalar(text("SELECT count(*) FROM console_client_notes"))
    assert seen == 0
    # Nobody writes a note under someone else's name.
    other = await api.staff("support")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with api.database.actor_transaction(other) as session:
            await session.execute(
                text(
                    "INSERT INTO console_client_notes (id, client_id, body, written_by, "
                    "written_at) VALUES ('-Nabc0000000000000Z8', :client, 'x', :staff, now())"
                ),
                {"client": client.id, "staff": staff.id},
            )


async def test_a_pin_reset_takes_two_people_and_signs_the_phone_app_out(
    api: Api, migrator_database: Database
) -> None:
    owner = migrator_database
    client = await api.client()
    token = api.http.headers["Authorization"]
    phone = await phone_of(owner, client)
    approver = await api.staff("operations")
    await api.staff("support")
    proposed = await propose(api, client)
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["status"] == "proposed" and action["yours"] is True
    assert (await decide(api, action["id"])).status_code == 403
    assert await queued(owner, phone) == []

    await api.as_staff(approver)
    inbox = (await api.http.get("/staff/approvals")).json()["items"]
    mine = [item for item in inbox if item["id"] == action["id"]]
    assert mine and mine[0]["kind"] == "client_pin_reset" and mine[0]["can_decide"] is True
    approved = await decide(api, action["id"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert await queued(owner, phone) == ["app_signed_out"]

    api.http.cookies.clear()
    api.http.headers.pop("Origin", None)
    api.http.headers["Authorization"] = token
    assert (await api.http.get("/client/session")).status_code == 401


async def test_a_pin_reset_leaves_a_browser_session_alone(
    api: Api, migrator_database: Database
) -> None:
    owner = migrator_database
    client = await api.client()
    browser = await api.identity.issue_session(client, second_step="passkey", transport="cookie")
    maker, checker = await api.staff("support"), await api.staff("operations")
    async with api.database.actor_transaction(maker) as session:
        action = await actions.propose(
            session, maker, str(client.id), "pin_reset", "Phone lost", api.clock
        )
    async with api.database.actor_transaction(checker) as session:
        await actions.decide(
            session,
            checker,
            action.id,
            "approved",
            "Checked",
            api.clock,
            permissions={"client_pins.reset"},
        )
    async with owner.transaction() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT transport, revoked_reason FROM identity_sessions "
                    "WHERE actor_kind = 'client' AND actor_id = :id"
                ),
                {"id": client.id},
            )
        ).all()
    reasons = {(row.transport, row.revoked_reason) for row in rows}
    assert ("token", "pin_reset") in reasons and ("cookie", None) in reasons
    assert browser.token


async def test_withdrawn_and_rejected_actions_change_nothing(api: Api) -> None:
    client = await api.client()
    other = await api.staff("operations")
    await api.staff("support")
    action = (await propose(api, client)).json()
    assert (await decide(api, action["id"], "withdrawn")).json()["status"] == "withdrawn"
    action = (await propose(api, client)).json()
    assert (await propose(api, client)).status_code == 409
    await api.as_staff(other)
    assert (await decide(api, action["id"], "withdrawn")).status_code == 403
    assert (await decide(api, action["id"], "rejected")).json()["status"] == "rejected"
    assert (await decide(api, action["id"], "approved")).status_code == 404
    page = (await api.http.get(f"/staff/clients/{client.id}/actions")).json()
    assert [item["status"] for item in page["actions"]] == ["rejected", "withdrawn"]
    assert page["allowed"] == ["pin_reset", "withdrawal_limits", "statement", "withdrawal"]


async def test_finance_cannot_reset_a_pin(api: Api) -> None:
    client = await api.client()
    await api.staff("support")
    action = (await propose(api, client)).json()
    await api.staff("finance")
    assert (await propose(api, client)).status_code == 403
    assert (await decide(api, action["id"])).status_code == 403
    assert (await api.http.get(f"/staff/clients/{client.id}/actions")).json()["allowed"] == [
        "withdrawal_limits"
    ]


async def test_staff_act_only_for_an_open_account(api: Api) -> None:
    client = await api.client()
    first, second = await api.staff("operations"), await api.staff("compliance")
    await api.as_staff(first)
    lock = (
        await api.http.post(
            f"/staff/clients/{client.id}/locks", json={"kind": "lock", "reason": "Fraud"}
        )
    ).json()
    await api.as_staff(second)
    await api.http.post(
        f"/staff/clients/locks/{lock['id']}/decision",
        json={"decision": "approved", "reason": "Checked"},
    )
    refused = await propose(api, client)
    assert refused.status_code == 409
    assert refused.json()["error"]["fields"][0]["code"] == "not_active"


async def test_two_approvals_at_once_apply_it_once(api: Api, migrator_database: Database) -> None:
    owner = migrator_database
    client = await api.client()
    proposer = await api.staff("support")
    async with api.database.actor_transaction(proposer) as session:
        action = await actions.propose(
            session, proposer, str(client.id), "pin_reset", "Phone lost", api.clock
        )
    deciders = [await api.staff("operations"), await api.staff("admin")]

    async def approve(decider: Actor) -> str:
        try:
            async with api.database.actor_transaction(decider) as session:
                await actions.decide(
                    session,
                    decider,
                    action.id,
                    "approved",
                    "Confirmed",
                    api.clock,
                    permissions={"client_pins.reset"},
                )
        except ApplicationError as error:
            return error.code
        return "approved"

    outcomes = await asyncio.gather(*(approve(decider) for decider in deciders))
    assert sorted(outcomes) == ["approved", "not_found"]
    assert await queued(owner, await phone_of(owner, client)) == ["app_signed_out"]


async def test_two_proposals_at_once_make_one(api: Api) -> None:
    client = await api.client()
    proposers = [await api.staff("support"), await api.staff("operations")]

    async def propose_reset(proposer: Actor) -> str:
        try:
            async with api.database.actor_transaction(proposer) as session:
                await actions.propose(
                    session, proposer, str(client.id), "pin_reset", "Phone lost", api.clock
                )
        except ApplicationError as error:
            return error.code
        return "proposed"

    outcomes = await asyncio.gather(*(propose_reset(proposer) for proposer in proposers))
    assert sorted(outcomes) == ["conflict", "proposed"]


async def test_the_database_refuses_one_person_deciding_their_own_action(api: Api) -> None:
    client = await api.client()
    staff = await api.staff("support")
    async with api.database.actor_transaction(staff) as session:
        action = await actions.propose(
            session, staff, str(client.id), "pin_reset", "Phone lost", api.clock
        )
    with pytest.raises(DBAPIError, match="two_people"):
        async with api.database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE console_client_actions SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x', decided_in = pg_current_xact_id() "
                    "WHERE id = :id"
                ),
                {"me": staff.id, "id": action.id},
            )
    other = await api.staff("operations")
    # Only the decision columns can change at all.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with api.database.actor_transaction(other) as session:
            await session.execute(
                text(
                    "UPDATE console_client_actions SET status = 'rejected', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x', reason = 'Changed' WHERE id = :id"
                ),
                {"me": other.id, "id": action.id},
            )


async def test_each_step_is_audited_without_the_notes_words(
    api: Api, migrator_database: Database
) -> None:
    client = await api.client()
    other = await api.staff("operations")
    await api.staff("support")
    action = (await propose(api, client)).json()
    note = (
        await api.http.post(f"/staff/clients/{client.id}/notes", json={"body": "Secret words"})
    ).json()
    await api.as_staff(other)
    await decide(api, action["id"])
    async with migrator_database.transaction() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action, details::text AS details FROM audit_events "
                    "WHERE resource_id IN (:action, :note) ORDER BY occurred_at, id"
                ),
                {"action": action["id"], "note": note["id"]},
            )
        ).all()
    assert [row.action for row in rows] == [
        "console.client.pin_reset_proposed",
        "console.client.note_written",
        "console.client.pin_reset_approved",
    ]
    assert all("Secret" not in row.details for row in rows)
