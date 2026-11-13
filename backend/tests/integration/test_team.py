"""Changing the team: each invite, role change, deactivation and set-up link takes two admins."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.identity import team
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import STAFF_ORIGIN, Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def propose(api: Api, **body: object) -> httpx.Response:
    return await api.http.post("/staff/team/changes", json={"reason": "Needed", **body})


async def decide(api: Api, change_id: str, decision: str = "approved") -> httpx.Response:
    return await api.http.post(
        f"/staff/team/changes/{change_id}/decision",
        json={"decision": decision, "reason": "Checked"},
    )


async def member(database: Database, staff_id: str | None):  # type: ignore[no-untyped-def]
    async with database.transaction() as session:
        return (
            await session.execute(
                text("SELECT role, status, created_by FROM identity_staff WHERE id = :id"),
                {"id": staff_id},
            )
        ).one_or_none()


async def links_sent(migrator: Database, email: str) -> list[str]:
    async with migrator.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT variables->>'link' AS link FROM notification_messages "
                "WHERE recipient = :email AND template = 'staff_enrolment' ORDER BY id"
            ),
            {"email": email},
        )
        return [row.link for row in rows]


def an_email() -> str:
    return f"new.{push_id().lower().replace('_', 'a').replace('-', 'b')}@finvestug.com"


async def test_an_invite_takes_effect_only_when_a_second_admin_approves(
    api: Api, migrator_database: Database
) -> None:
    second = await api.staff("admin", "Second Admin")
    first = await api.staff("admin", "First Admin")
    email = an_email()
    proposed = await propose(api, kind="invite", email=email, name="Ada Nakato", role="finance")
    assert proposed.status_code == 201, proposed.text
    change = proposed.json()
    assert change["status"] == "proposed" and change["yours"] is True
    assert await member(api.database, change["staff_id"]) is None

    # The proposer cannot approve their own change.
    assert (await decide(api, change["id"])).status_code == 403
    await api.as_staff(second)
    approved = await decide(api, change["id"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    added = await member(api.database, change["staff_id"])
    assert added.role == "finance" and added.status == "invited"
    assert added.created_by == first.id
    links = await links_sent(migrator_database, email)
    assert len(links) == 1 and links[0].startswith(f"{STAFF_ORIGIN}/enrol#")

    listed = (await api.http.get("/staff/team")).json()
    assert any(m["id"] == change["staff_id"] for m in listed["members"])
    assert all(c["id"] != change["id"] for c in listed["changes"])


async def test_a_rejected_or_withdrawn_change_changes_nothing(api: Api) -> None:
    other = await api.staff("admin")
    subject = await api.staff("support")
    proposer = await api.staff("admin")
    first = (await propose(api, kind="change_role", staff_id=subject.id, role="finance")).json()
    await api.as_staff(other)
    assert (await decide(api, first["id"], "withdrawn")).status_code == 403
    assert (await decide(api, first["id"], "rejected")).json()["status"] == "rejected"
    assert (await member(api.database, subject.id)).role == "support"

    await api.as_staff(proposer)
    second = (await propose(api, kind="change_role", staff_id=subject.id, role="finance")).json()
    assert (await decide(api, second["id"], "withdrawn")).json()["status"] == "withdrawn"
    assert (await member(api.database, subject.id)).role == "support"
    # A decided change is never decided again.
    await api.as_staff(other)
    assert (await decide(api, second["id"])).status_code == 404


async def test_a_role_change_applies_on_approval(api: Api) -> None:
    other = await api.staff("admin")
    subject = await api.staff("support")
    await api.staff("admin")
    change = (await propose(api, kind="change_role", staff_id=subject.id, role="compliance")).json()
    assert change["current_role"] == "support" and change["role"] == "compliance"
    await api.as_staff(other)
    assert (await decide(api, change["id"])).status_code == 200
    assert (await member(api.database, subject.id)).role == "compliance"
    await api.as_staff(subject)
    session = (await api.http.get("/staff/session")).json()
    assert session["role"] == "compliance"


async def test_deactivating_ends_every_session_at_once_and_reactivating_restores(
    api: Api,
) -> None:
    other = await api.staff("admin")
    subject = await api.staff("operations")
    subject_session = await api.identity.issue_session(
        subject, second_step="passkey", transport="cookie"
    )
    await api.staff("admin")
    change = (await propose(api, kind="deactivate", staff_id=subject.id)).json()
    await api.as_staff(other)
    assert (await decide(api, change["id"])).status_code == 200
    assert (await member(api.database, subject.id)).status == "disabled"
    assert await api.identity.staff_session(subject_session.token) is None

    # Nothing but a reactivation brings them back, and it takes two admins too.
    again = (await propose(api, kind="deactivate", staff_id=subject.id)).json()
    assert again["error"]["fields"][0]["code"] == "already_disabled"
    back = (await propose(api, kind="reactivate", staff_id=subject.id)).json()
    await api.as_staff(subject)
    assert (await api.http.get("/staff/team")).status_code == 401
    await api.staff("admin")
    assert (await decide(api, back["id"])).status_code == 200
    assert (await member(api.database, subject.id)).status == "active"


async def test_a_new_set_up_link_replaces_the_last_one(
    api: Api, migrator_database: Database
) -> None:
    other = await api.staff("admin")
    subject = await api.staff("finance")
    await api.staff("admin")
    async with api.database.transaction() as session:
        email = await session.scalar(
            text("SELECT email FROM identity_staff WHERE id = :id"), {"id": subject.id}
        )
    change = (await propose(api, kind="reset_sign_in", staff_id=subject.id)).json()
    assert await links_sent(migrator_database, email) == []
    await api.as_staff(other)
    assert (await decide(api, change["id"])).status_code == 200
    assert len(await links_sent(migrator_database, email)) == 1


async def test_nobody_changes_their_own_account(api: Api) -> None:
    admin = await api.staff("admin")
    refused = await propose(api, kind="change_role", staff_id=admin.id, role="support")
    assert refused.status_code == 403
    assert refused.json()["error"]["fields"][0]["code"] == "own_account"

    # Nor approves a change about themselves that someone else proposed.
    await api.staff("admin")
    change = (await propose(api, kind="reset_sign_in", staff_id=admin.id)).json()
    await api.as_staff(admin)
    assert (await decide(api, change["id"])).status_code == 403


async def test_only_admins_may_change_the_team(api: Api) -> None:
    subject = await api.staff("support")
    await api.staff("operations")
    assert (await api.http.get("/staff/team")).status_code == 403
    refused = await propose(api, kind="deactivate", staff_id=subject.id)
    assert refused.status_code == 403


async def test_one_open_change_per_person_and_per_invited_address(api: Api) -> None:
    subject = await api.staff("support")
    await api.staff("admin")
    assert (await propose(api, kind="deactivate", staff_id=subject.id)).status_code == 201
    assert (await propose(api, kind="reset_sign_in", staff_id=subject.id)).status_code == 409
    email = an_email()
    assert (
        await propose(api, kind="invite", email=email, name="A", role="support")
    ).status_code == 201
    taken = await propose(api, kind="invite", email=email, name="B", role="support")
    assert taken.status_code == 409


async def test_an_address_already_on_the_team_cannot_be_invited(api: Api) -> None:
    subject = await api.staff("support")
    async with api.database.transaction() as session:
        email = await session.scalar(
            text("SELECT email FROM identity_staff WHERE id = :id"), {"id": subject.id}
        )
    await api.staff("admin")
    taken = await propose(api, kind="invite", email=email.upper(), name="Again", role="support")
    assert taken.status_code == 409
    assert taken.json()["error"]["fields"][0]["code"] == "taken"


async def test_two_admins_approving_the_same_change_at_once_apply_it_once(api: Api) -> None:
    subject = await api.staff("support")
    proposer = await api.staff("admin")
    change_id = await _proposed(api, proposer, "change_role", subject.id, role="finance")
    deciders = [await api.staff("admin"), await api.staff("admin")]

    async def approve(decider: Actor) -> str:
        try:
            async with api.database.actor_transaction(decider) as session:
                await team.decide(
                    session,
                    decider,
                    change_id,
                    "approved",
                    "Checked",
                    api.clock,
                    console_origin=STAFF_ORIGIN,
                )
        except ApplicationError as error:
            return error.code
        return "approved"

    outcomes = await asyncio.gather(*(approve(decider) for decider in deciders))
    assert sorted(outcomes) == ["approved", "not_found"]
    assert (await member(api.database, subject.id)).role == "finance"


async def _proposed(
    api: Api, by: Actor, kind: str, staff_id: str | None, role: str | None = None
) -> str:
    async with api.database.actor_transaction(by) as session:
        change = await team.propose(
            session,
            by,
            team.Proposed(kind=kind, reason="Needed", staff_id=staff_id, role=role),  # type: ignore[arg-type]
            api.clock,
        )
    return change.id


async def _approve(api: Api, by: Actor, change_id: str) -> None:
    async with api.database.actor_transaction(by) as session:
        await team.decide(
            session, by, change_id, "approved", "Checked", api.clock, console_origin=STAFF_ORIGIN
        )


async def test_the_database_refuses_a_role_or_status_change_without_an_approval(
    api: Api,
) -> None:
    subject = await api.staff("support")
    admin = await api.staff("admin")
    for statement in (
        "UPDATE identity_staff SET role = 'admin' WHERE id = :id",
        "UPDATE identity_staff SET status = 'disabled' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="approved change"):
            async with api.database.actor_transaction(admin) as session:
                await session.execute(text(statement), {"id": subject.id})


async def test_the_database_refuses_a_proposal_from_anyone_but_an_active_admin(
    api: Api,
) -> None:
    operations = await api.staff("operations")
    with pytest.raises(DBAPIError, match="only an active admin"):
        async with api.database.actor_transaction(operations) as session:
            await session.execute(
                text(
                    "INSERT INTO identity_staff_changes (id, kind, staff_id, email, name, role, "
                    "reason, proposed_by, proposed_at) VALUES (:id, 'invite', :new, "
                    "'someone@finvestug.com', 'Someone', 'admin', 'x', :me, now())"
                ),
                {"id": push_id(), "new": push_id(), "me": operations.id},
            )


async def test_the_database_refuses_one_person_deciding_their_own_proposal(api: Api) -> None:
    subject = await api.staff("support")
    admin = await api.staff("admin")
    change_id = await _proposed(api, admin, "deactivate", subject.id)
    with pytest.raises(DBAPIError, match="two_people"):
        async with api.database.actor_transaction(admin) as session:
            await session.execute(
                text(
                    "UPDATE identity_staff_changes SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x', decided_in = pg_current_xact_id() "
                    "WHERE id = :id"
                ),
                {"me": admin.id, "id": change_id},
            )


async def test_an_approval_from_an_earlier_transaction_opens_nothing(api: Api) -> None:
    subject = await api.staff("support")
    proposer = await api.staff("admin")
    decider = await api.staff("admin")
    await _approve(
        api, decider, await _proposed(api, proposer, "change_role", subject.id, "finance")
    )
    await _approve(
        api, decider, await _proposed(api, proposer, "change_role", subject.id, "support")
    )
    # The approval to finance is on record, but it was for another transaction.
    with pytest.raises(DBAPIError, match="approved change"):
        async with api.database.actor_transaction(decider) as session:
            await session.execute(
                text("UPDATE identity_staff SET role = 'finance' WHERE id = :id"),
                {"id": subject.id},
            )
    assert (await member(api.database, subject.id)).role == "support"


async def test_every_step_is_on_the_audit_trail(api: Api, migrator_database: Database) -> None:
    other = await api.staff("admin")
    subject = await api.staff("support")
    await api.staff("admin")
    change = (await propose(api, kind="deactivate", staff_id=subject.id)).json()
    await api.as_staff(other)
    await decide(api, change["id"])
    async with migrator_database.transaction() as session:
        actions = (
            await session.scalars(
                text(
                    "SELECT action FROM audit_events WHERE resource_type = 'staff_change' "
                    "AND resource_id = :id ORDER BY occurred_at, id"
                ),
                {"id": change["id"]},
            )
        ).all()
    assert actions == ["identity.team.deactivate_proposed", "identity.team.change_approved"]
