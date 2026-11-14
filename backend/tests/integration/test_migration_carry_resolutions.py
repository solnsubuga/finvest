"""Two members of staff retry or close a client the worker could not carry over.

Each test has a database of its own, since at most one switch is ever approved.
"""

import asyncio

import pytest

from app.infrastructure.database import Database
from app.modules.identity.service import create_unclaimed_client
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import DocumentType, IdentityDetails
from app.modules.migration import resolutions
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from tests.integration.test_migration_api import NOW, Console, code
from tests.integration.test_migration_carry_over import approved, rows, worker
from tests.integration.test_migration_switch import (
    SEALER,
    console,
    person,
    sheets,
    test_database,
)
from tests.support.investments import PRESENT

pytestmark = pytest.mark.integration

__all__ = ["console", "test_database"]  # the fixtures, shared with the switch tests


async def noted(console: Console, worker_database: Database) -> str:
    """An approved switch of two clients, whose first client the worker could not carry over."""
    switch_id = await approved(console, sheets(2), 2)
    # Someone in Finvest 2.0 already holds the first client's identity document.
    staff = Actor("staff", await console.sign_in("operations"))
    async with console.database.actor_transaction(staff) as session:
        other = await create_unclaimed_client(
            session, PRESENT, staff, phone="+256772999999", email=None
        )
    async with worker_database.actor_transaction(SYSTEM) as session:
        await kyc.record_carried_over_check(
            session,
            SEALER,
            SYSTEM,
            client_id=other,
            document_type=DocumentType.NATIONAL_ID,
            document_country="UG",
            details=IdentityDetails(
                surname="Other",
                given_names="Person",
                document_number=person("1")["id_number"],
                date_of_birth=NOW.date().replace(year=1990),
                document_expires_on=None,
            ),
            legacy_reference="OTHER-1",
            reason="Set-up for the test",
            clock=PRESENT,
        )
    assert await worker(worker_database).run_once() == 1
    return switch_id


def resolution(action: str, reason: str = "Checked with the client by phone") -> dict[str, str]:
    return {"legacy_client_ref": "1", "action": action, "reason": reason}


async def not_carried(console: Console) -> list[dict[str, object]]:
    seen = await console.overview()
    return list(seen["switch"]["not_carried"])


async def test_a_retry_refused_again_is_noted_as_the_next_attempt(
    console: Console, worker_database: Database, migrator_database: Database
) -> None:
    switch_id = await noted(console, worker_database)
    # An admin holds both permissions, and still cannot decide their own proposal.
    await console.sign_in("admin")
    proposed = await console.post(f"/switch/{switch_id}/resolutions", resolution("retry"))
    assert proposed.status_code == 201, proposed.text
    assert (proposed.json()["state"], proposed.json()["attempt"]) == ("proposed", 1)
    decision = f"/switch/resolutions/{proposed.json()['id']}/decision"
    assert code(await console.post(decision, {"approve": True})) == (403, "forbidden")
    [shown] = await not_carried(console)
    assert (shown["state"], shown["resolution_id"], shown["reason"]) == (
        "retry_proposed",
        proposed.json()["id"],
        "Checked with the client by phone",
    )

    await console.sign_in("finance")
    decided = await console.post(decision, {"approve": True})
    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "approved"
    [shown] = await not_carried(console)
    assert shown["state"] == "retrying"

    # The document is still taken, so the worker notes the client again as the second attempt.
    assert await worker(worker_database).run_once() == 1
    [shown] = await not_carried(console)
    assert (shown["code"], shown["attempt"], shown["state"]) == ("document_taken", 2, "noted")
    attempts = await rows(
        migrator_database,
        "SELECT attempt FROM migration_carry_problems WHERE legacy_client_ref = '1' "
        "ORDER BY attempt",
    )
    assert [a.attempt for a in attempts] == [1, 2]
    # The client is still only in the sheets, so they stay.
    assert len(await rows(migrator_database, "SELECT switch_id FROM migration_switch_files")) == 1
    assert len(await rows(migrator_database, "SELECT id FROM investments")) == 1


async def test_closing_the_last_noted_client_removes_the_sheets(
    console: Console, worker_database: Database, migrator_database: Database
) -> None:
    switch_id = await noted(console, worker_database)
    await console.sign_in("operations")
    proposed = await console.post(
        f"/switch/{switch_id}/resolutions", resolution("close", "Same person as the other account")
    )
    assert proposed.status_code == 201, proposed.text
    await console.sign_in("finance")
    decided = await console.post(
        f"/switch/resolutions/{proposed.json()['id']}/decision", {"approve": True}
    )
    assert decided.status_code == 200, decided.text
    # Closed, the client is no longer waited for.
    assert await not_carried(console) == []

    assert await worker(worker_database).run_once() == 1
    assert await rows(migrator_database, "SELECT * FROM migration_switch_files") == []
    claims = await rows(migrator_database, "SELECT legacy_client_ref FROM migration_claims")
    assert [c.legacy_client_ref for c in claims] == ["2"]
    [job] = await rows(
        migrator_database,
        "SELECT status FROM jobs WHERE kind = 'migration.carry_over' AND idempotency_key = :key",
        key=f"{switch_id}:{proposed.json()['id']}",
    )
    assert job.status == "succeeded"
    # Nothing is left to resolve.
    await console.sign_in("operations")
    again = await console.post(f"/switch/{switch_id}/resolutions", resolution("retry"))
    assert code(again) == (409, "conflict")


async def test_a_note_takes_one_proposal_at_a_time_and_a_rejection_gives_a_reason(
    console: Console, worker_database: Database
) -> None:
    switch_id = await noted(console, worker_database)
    route = f"/switch/{switch_id}/resolutions"
    await console.sign_in("operations")
    assert code(await console.post(route, {**resolution("retry"), "legacy_client_ref": "2"})) == (
        404,
        "not_found",
    )
    assert code(await console.post(route, resolution("retry", "   ")))[0] == 422
    first = await console.post(route, resolution("retry"))
    assert first.status_code == 201
    assert code(await console.post(route, resolution("close"))) == (409, "conflict")

    await console.sign_in("finance")
    decision = f"/switch/resolutions/{first.json()['id']}/decision"
    # Deciding takes migration.sign_off, and proposing takes migration.import.
    assert code(await console.post(route, resolution("close"))) == (403, "forbidden")
    assert code(await console.post(decision, {"approve": False}))[0] == 422
    rejected = await console.post(decision, {"approve": False, "reason": "Not fixed yet"})
    assert (rejected.json()["state"], rejected.json()["decision_reason"]) == (
        "rejected",
        "Not fixed yet",
    )
    assert code(await console.post(decision, {"approve": True})) == (409, "conflict")
    [shown] = await not_carried(console)
    assert (shown["state"], shown["resolution_id"]) == ("noted", None)

    # Rejected, the client may be proposed again.
    await console.sign_in("operations")
    assert (await console.post(route, resolution("close"))).status_code == 201


async def test_two_proposals_at_once_leave_one(
    console: Console, worker_database: Database, app_database: Database
) -> None:
    switch_id = await noted(console, worker_database)
    staff = [Actor("staff", await console.sign_in("operations")) for _ in range(2)]

    async def propose(actor: Actor, action: resolutions.Action) -> str:
        try:
            async with app_database.actor_transaction(actor) as session:
                await resolutions.propose_resolution(
                    session,
                    actor,
                    PRESENT,
                    switch_id,
                    legacy_client_ref="1",
                    action=action,
                    reason="Checked by hand",
                )
        except ApplicationError as error:
            return error.code
        return "proposed"

    outcomes = await asyncio.gather(propose(staff[0], "retry"), propose(staff[1], "close"))
    assert sorted(outcomes) == ["conflict", "proposed"]
