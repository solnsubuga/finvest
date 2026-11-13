"""Lifting an email suppression takes two people, and a new bounce after a lift suppresses again."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.identity import team
from app.modules.notifications import suppression_lifts as lifts
from app.modules.notifications.domain.email_events import address_hash, keyed_hash
from app.modules.notifications.email_delivery import is_suppressed
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


def an_address() -> str:
    return f"client.{secrets.token_hex(6)}@example.com"


async def bounce(
    migrator: Database,
    address: str,
    at: datetime,
    delivery: str = "bounced",
    permanent: bool = True,
) -> None:
    """A suppressing event from Resend, as the webhook and the worker would leave it."""
    hashed = address_hash(address)
    svix_id = f"msg_{secrets.token_hex(8)}"
    async with migrator.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO notification_email_events (svix_id, delivery, email_id, recipients, "
                "permanent, received_at, processed_at, outcome) VALUES (:svix, :delivery, "
                ":email_id, ARRAY[:hash]::text[], :permanent, :at, :at, 'applied')"
            ),
            {
                "svix": svix_id,
                "delivery": delivery,
                "email_id": secrets.token_hex(8),
                "hash": hashed,
                "permanent": permanent,
                "at": at,
            },
        )
        await session.execute(
            text(
                "INSERT INTO notification_email_suppressions (address_hash, reason, svix_id, "
                "created_at) VALUES (:hash, :reason, :svix, :at) ON CONFLICT DO NOTHING"
            ),
            {
                "hash": hashed,
                "reason": "complained" if delivery == "complained" else "bounced",
                "svix": svix_id,
                "at": at,
            },
        )


async def suppressed(worker: Database, address: str) -> bool:
    async with worker.transaction() as session:
        return await is_suppressed(session, address)


async def lift(api: Api, address: str, maker: Actor, checker: Actor) -> str:
    hashed = address_hash(address)
    await api.as_staff(maker)
    proposed = await api.http.post(
        f"/staff/email-suppressions/{hashed}/lifts", json={"reason": "Client fixed their inbox"}
    )
    assert proposed.status_code == 201, proposed.text
    await api.as_staff(checker)
    decided = await api.http.post(
        f"/staff/email-suppressions/lifts/{proposed.json()['id']}/decision",
        json={"decision": "approved", "reason": "Confirmed on a call"},
    )
    assert decided.status_code == 200, decided.text
    return str(proposed.json()["id"])


async def test_a_lift_two_people_approve_lets_email_through_until_the_next_bounce(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now() - timedelta(days=1))
    assert await suppressed(worker_database, address)
    maker = await api.staff("support", name="Ruth Namata")
    checker = await api.staff("operations", name="Daniel Kato")
    await api.as_staff(maker)
    proposed = await api.http.post(
        f"/staff/email-suppressions/{address_hash(address)}/lifts", json={"reason": "Fixed inbox"}
    )
    assert proposed.json()["proposed_by_name"] == "Ruth Namata"
    await api.as_staff(checker)
    # The second person sees who asked, on the lookup and on the list.
    waiting = (
        await api.http.post("/staff/email-suppressions/lookup", json={"email": address})
    ).json()["suppression"]["open_lift"]
    assert (waiting["proposed_by_name"], waiting["decided_by_name"]) == ("Ruth Namata", None)
    listed = (await api.http.get("/staff/email-suppressions")).json()["suppressions"]
    mine = [item for item in listed if item["address_hash"] == address_hash(address)]
    assert mine[0]["open_lift"]["proposed_by_name"] == "Ruth Namata"
    decided = await api.http.post(
        f"/staff/email-suppressions/lifts/{proposed.json()['id']}/decision",
        json={"decision": "approved", "reason": "Confirmed on a call"},
    )
    assert decided.json()["decided_by_name"] == "Daniel Kato"
    assert not await suppressed(worker_database, address)

    api.clock.advance(hours=1)
    await bounce(migrator_database, address, api.clock.now())
    assert await suppressed(worker_database, address)

    await api.as_staff(checker)
    found = (
        await api.http.post("/staff/email-suppressions/lookup", json={"email": address.upper()})
    ).json()["suppression"]
    assert found["suppressed"] is True and found["lifted_at"] is not None


async def test_a_transient_bounce_after_a_lift_does_not_suppress(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now() - timedelta(days=1))
    await lift(api, address, await api.staff("support"), await api.staff("admin"))
    api.clock.advance(hours=1)
    await bounce(migrator_database, address, api.clock.now(), permanent=False)
    assert not await suppressed(worker_database, address)


async def test_an_address_that_complained_is_never_lifted(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    address = an_address()
    hashed = address_hash(address)
    await bounce(migrator_database, address, api.clock.now(), "complained")
    await api.staff("admin")
    found = (
        await api.http.post("/staff/email-suppressions/lookup", json={"email": address})
    ).json()["suppression"]
    assert (found["suppressed"], found["complained"]) == (True, True)
    refused = await api.http.post(
        f"/staff/email-suppressions/{hashed}/lifts", json={"reason": "Client asked"}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["fields"][0]["code"] == "complained"
    assert await suppressed(worker_database, address)


async def test_a_complaint_after_a_lifted_bounce_blocks_any_further_lift(
    api: Api, migrator_database: Database
) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now() - timedelta(days=1))
    await lift(api, address, await api.staff("support"), await api.staff("admin"))
    api.clock.advance(hours=1)
    await bounce(migrator_database, address, api.clock.now(), "complained")
    staff = await api.staff("support")
    with pytest.raises(ApplicationError) as refused:
        async with api.database.actor_transaction(staff) as session:
            await lifts.propose(session, staff, address_hash(address), "Again", api.clock, None)
    assert refused.value.code == "conflict"


async def test_the_database_refuses_to_lift_a_complaint(
    api: Api, migrator_database: Database
) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now(), "complained")
    staff = await api.staff("support")
    with pytest.raises(DBAPIError, match="complained is never lifted"):
        async with api.database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO notification_email_suppression_lifts (id, address_hash, "
                    "reason, proposed_by, proposed_at) VALUES (:id, :hash, 'x', :me, now())"
                ),
                {"id": push_id(), "hash": address_hash(address), "me": staff.id},
            )


async def test_a_lift_keeps_the_keyed_hash_beside_the_plain_one(
    api: Api, migrator_database: Database
) -> None:
    address, key = an_address(), secrets.token_bytes(32)
    await bounce(migrator_database, address, api.clock.now())
    staff = await api.staff("support")
    async with api.database.actor_transaction(staff) as session:
        proposal = await lifts.propose(
            session, staff, address_hash(address), "Fixed", api.clock, key
        )
    async with migrator_database.transaction() as session:
        stored = await session.scalar(
            text("SELECT address_key FROM notification_email_suppression_lifts WHERE id = :id"),
            {"id": proposal.id},
        )
    assert stored == keyed_hash(address_hash(address), key)


async def test_a_member_of_staff_no_longer_active_cannot_decide(
    api: Api, migrator_database: Database
) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now())
    maker, checker = await api.staff("support"), await api.staff("operations")
    async with api.database.actor_transaction(maker) as session:
        proposal = await lifts.propose(
            session, maker, address_hash(address), "Fixed", api.clock, None
        )
    # Deactivated the only way there is: two admins (migration 0150).
    first, second = await api.staff("admin"), await api.staff("admin")
    async with api.database.actor_transaction(first) as session:
        change = await team.propose(
            session,
            first,
            team.Proposed(kind="deactivate", reason="Left", staff_id=checker.id),
            api.clock,
        )
    async with api.database.actor_transaction(second) as session:
        await team.decide(
            session, second, change.id, "approved", "Checked", api.clock, console_origin=None
        )
    with pytest.raises(DBAPIError, match="only an active member of staff"):
        async with api.database.actor_transaction(checker) as session:
            await lifts.decide(session, checker, proposal.id, "approved", "Checked", api.clock)


async def test_nobody_approves_their_own_lift(api: Api, migrator_database: Database) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now())
    hashed = address_hash(address)
    await api.staff("support")
    proposed = (
        await api.http.post(f"/staff/email-suppressions/{hashed}/lifts", json={"reason": "Fixed"})
    ).json()
    own = await api.http.post(
        f"/staff/email-suppressions/lifts/{proposed['id']}/decision",
        json={"decision": "approved", "reason": "Mine"},
    )
    assert own.status_code == 403
    again = await api.http.post(
        f"/staff/email-suppressions/{hashed}/lifts", json={"reason": "Fixed"}
    )
    assert again.status_code == 409
    withdrawn = await api.http.post(
        f"/staff/email-suppressions/lifts/{proposed['id']}/decision",
        json={"decision": "withdrawn", "reason": "Wrong address"},
    )
    assert withdrawn.json()["status"] == "withdrawn"


async def test_only_a_suppressed_address_can_be_lifted(
    api: Api, migrator_database: Database
) -> None:
    address = an_address()
    await api.staff("support")
    unknown = await api.http.post(
        f"/staff/email-suppressions/{address_hash(address)}/lifts", json={"reason": "Fixed"}
    )
    assert unknown.status_code == 404
    await bounce(migrator_database, address, api.clock.now() - timedelta(days=1))
    await lift(api, address, await api.staff("support"), await api.staff("operations"))
    await api.staff("support")
    lifted = await api.http.post(
        f"/staff/email-suppressions/{address_hash(address)}/lifts", json={"reason": "Fixed"}
    )
    assert lifted.json()["error"]["fields"][0]["code"] == "not_suppressed"


async def test_who_may_see_and_who_may_lift(api: Api, migrator_database: Database) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now())
    await api.staff("finance")
    listed = (await api.http.get("/staff/email-suppressions")).json()
    assert listed["can_lift"] is False
    assert address_hash(address) in [item["address_hash"] for item in listed["suppressions"]]
    refused = await api.http.post(
        f"/staff/email-suppressions/{address_hash(address)}/lifts", json={"reason": "Fixed"}
    )
    assert refused.status_code == 403


async def test_a_lookup_for_an_address_never_suppressed_finds_nothing(api: Api) -> None:
    await api.staff("support")
    result = await api.http.post("/staff/email-suppressions/lookup", json={"email": an_address()})
    assert result.json() == {"suppression": None}
    bad = await api.http.post("/staff/email-suppressions/lookup", json={"email": "not an email"})
    assert bad.status_code == 422


async def test_two_approvals_at_once_decide_once(api: Api, migrator_database: Database) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now())
    maker = await api.staff("support")
    async with api.database.actor_transaction(maker) as session:
        proposal = await lifts.propose(
            session, maker, address_hash(address), "Fixed", api.clock, None
        )
    checkers = [await api.staff("operations"), await api.staff("admin")]

    async def approve(checker: Actor) -> str:
        try:
            async with api.database.actor_transaction(checker) as session:
                await lifts.decide(session, checker, proposal.id, "approved", "Ok", api.clock)
        except ApplicationError as error:
            return error.code
        return "approved"

    outcomes = await asyncio.gather(*(approve(checker) for checker in checkers))
    assert sorted(outcomes) == ["approved", "not_found"]


async def test_the_database_refuses_a_one_person_lift(
    api: Api, migrator_database: Database
) -> None:
    address = an_address()
    await bounce(migrator_database, address, api.clock.now())
    staff = await api.staff("support")
    async with api.database.actor_transaction(staff) as session:
        proposal = await lifts.propose(
            session, staff, address_hash(address), "Fixed", api.clock, None
        )
    with pytest.raises(DBAPIError, match="two_people"):
        async with api.database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE notification_email_suppression_lifts SET status = 'approved', "
                    "decided_by = :me, decided_at = now(), decision_reason = 'x' WHERE id = :id"
                ),
                {"me": staff.id, "id": proposal.id},
            )


async def test_the_list_stays_hidden_from_clients_and_unsigned_requests(
    api: Api, migrator_database: Database
) -> None:
    await bounce(migrator_database, an_address(), api.clock.now())
    async with api.database.transaction() as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM notification_email_suppressions"))
        ) == 0
    client = await api.client()
    async with api.database.actor_transaction(client) as session:
        assert (await session.scalar(text("SELECT count(*) FROM notification_email_events"))) == 0
