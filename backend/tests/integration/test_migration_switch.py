"""The switch: proposed by one member of staff, approved by another, through HTTP.

Each test has a database of its own, since at most one switch is ever approved.
"""

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer
from app.modules.identity.service import create_unclaimed_client
from app.shared.actors import Actor
from tests.integration.test_identity import HASHER, STAFF_ORIGIN
from tests.integration.test_migration_api import AS_OF, NOW, Console, code
from tests.support import database as harness
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.unit.test_migration_sheet import clients, investments
from tests.unit.test_migration_trial import client, investment

pytestmark = pytest.mark.integration

SEALER = Sealer(bytes(range(32)), purpose="identity")


@pytest.fixture
def test_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_switch_{os.getpid()}")
    try:
        result = harness.migrate(database)
        if result.returncode != 0:
            pytest.fail(f"migrations failed:\n{result.stdout}\n{result.stderr}")
        yield database
    finally:
        harness.drop(database)


@pytest.fixture
async def console(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Console]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"staff_allowed_origins": [STAFF_ORIGIN], "trusted_proxies": ["127.0.0.1"]}
    )
    app = create_app(settings, app_database, FakeClock(NOW), HASHER, identity_sealer=SEALER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Console(http, app.state.identity, app_database)


def sheets(count: int) -> dict[str, str]:
    """`count` clients, each with one investment whose interest is what the formula gives."""
    refs = [str(n) for n in range(1, count + 1)]
    return {
        "clients_csv": clients(*(client(ref) for ref in refs)),
        "investments_csv": investments(
            *(
                investment(
                    f"INV-{ref}",
                    ref,
                    start_date="2026-07-29",
                    maturity_date="2026-10-28",
                    interest_before_tax="11643",
                )
                for ref in refs
            )
        ),
    }


async def cleared(console: Console, files: dict[str, str], count: int) -> None:
    """A trial with nothing to sign off, and Finvest 1.0's totals confirmed by a second person."""
    await console.sign_in("operations")
    ran = await console.post("/trials", {"as_of": AS_OF, **files})
    assert ran.status_code == 200, ran.text
    await console.sign_in("finance")
    entered = await console.put(
        "/control-totals",
        {
            "as_of": AS_OF,
            "clients": count,
            "verified_identities": count,
            "running_investments": count,
            "pending_orders": 0,
            "invested": [{"currency": "UGX", "minor": 1_000_000 * count}],
            "interest_before_tax": [{"currency": "UGX", "minor": 11_643 * count}],
        },
    )
    assert entered.status_code == 200, entered.text
    await console.sign_in("finance")
    confirmed = await console.post(
        f"/control-totals/{entered.json()['totals']['id']}/confirmation", {}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["blockers"] == []


async def table(database: Database, sql: str, **values: Any) -> list[Any]:
    async with database.transaction() as session:
        return list((await session.execute(text(sql), values)).all())


async def test_one_person_proposes_the_switch_and_another_approves_it(
    console: Console, migrator_database: Database
) -> None:
    files = sheets(2)
    await cleared(console, files, 2)

    proposer = await console.sign_in("operations")
    proposed = await console.post("/switch", files)
    assert proposed.status_code == 201, proposed.text
    switch = proposed.json()
    assert (switch["state"], switch["proposed_by"], switch["as_of"]) == (
        "proposed",
        proposer,
        AS_OF,
    )
    assert switch["clients_carried"] == 0
    # A second proposal waits for this one to be decided.
    assert code(await console.post("/switch", files)) == (409, "conflict")
    overview = await console.overview()
    assert overview["switch"] == switch
    assert overview["allowed"] == ["run_trial", "propose_switch"]
    # Operations cannot decide it, and nothing in the answer comes from a cell of the sheets.
    decision = f"/switch/{switch['id']}/decision"
    assert code(await console.post(decision, {"approve": True}))[0] == 403
    assert "Person 1" not in proposed.text and "+256" not in proposed.text

    # The sheets are kept sealed, never as they were sent.
    [kept] = await table(migrator_database, "SELECT clients_csv FROM migration_switch_files")
    assert b"Person 1" not in bytes(kept.clients_csv)

    approver = await console.sign_in("finance")
    approved = await console.post(decision, {"approve": True})
    assert approved.status_code == 200, approved.text
    assert (approved.json()["state"], approved.json()["decided_by"]) == ("approved", approver)
    assert approved.json()["clients_carried"] == 2
    assert code(await console.post(decision, {"approve": True})) == (409, "conflict")

    carried = await table(
        migrator_database,
        "SELECT c.legacy_client_ref, c.carried_by, i.status, i.password_hash "
        "FROM migration_carried_clients AS c JOIN identity_clients AS i ON i.id = c.client_id "
        "ORDER BY c.legacy_client_ref",
    )
    assert [(row.legacy_client_ref, row.carried_by, row.status) for row in carried] == [
        ("1", approver, "unclaimed"),
        ("2", approver, "unclaimed"),
    ]
    assert all(row.password_hash is None for row in carried)

    # Once approved, the switch is never proposed again.
    await console.sign_in("operations")
    assert code(await console.post("/switch", files)) == (409, "conflict")


async def test_only_the_sheets_of_the_cleared_trial_can_be_switched(console: Console) -> None:
    files = sheets(1)
    await console.sign_in("operations")
    # Nothing has been checked yet.
    blocked = await console.post("/switch", files)
    assert code(blocked) == (409, "conflict")
    assert blocked.json()["error"]["fields"] == [{"field_path": ["blockers"], "code": "no_trial"}]

    await cleared(console, files, 1)
    await console.sign_in("operations")
    edited = files | {"investments_csv": files["investments_csv"] + "\n"}
    refused = await console.post("/switch", edited)
    assert code(refused) == (422, "validation_failed")
    assert refused.json()["error"]["fields"] == [
        {"field_path": ["investments_csv"], "code": "not_the_trial_sheet"}
    ]


async def test_a_client_without_a_phone_number_holds_the_switch(console: Console) -> None:
    files = {
        "clients_csv": clients(client("1", phone="")),
        "investments_csv": sheets(1)["investments_csv"],
    }
    await cleared(console, files, 1)
    await console.sign_in("operations")
    refused = await console.post("/switch", files)
    assert code(refused) == (422, "validation_failed")
    assert refused.json()["error"]["fields"] == [
        {"field_path": ["clients", 2, "phone"], "code": "missing"}
    ]


async def test_a_number_that_already_has_an_account_refuses_the_whole_approval(
    console: Console, migrator_database: Database
) -> None:
    files = sheets(2)
    await cleared(console, files, 2)
    await console.sign_in("operations")
    switch = (await console.post("/switch", files)).json()
    # Someone signed up in Finvest 2.0 with the second client's number in the meantime.
    staff = Actor("staff", await console.sign_in("operations"))
    async with console.database.actor_transaction(staff) as session:
        await create_unclaimed_client(
            session, FakeClock(NOW), staff, phone=client("2")["phone"], email=None
        )

    await console.sign_in("finance")
    refused = await console.post(f"/switch/{switch['id']}/decision", {"approve": True})
    assert code(refused) == (409, "phone_taken")
    assert refused.json()["error"]["fields"] == [
        {"field_path": ["clients", 3, "phone"], "code": "phone_taken"}
    ]
    # No account, no decision: the switch still waits for one.
    assert await table(migrator_database, "SELECT * FROM migration_carried_clients") == []
    assert (await console.overview())["switch"]["state"] == "proposed"
    clients_made = await table(migrator_database, "SELECT id FROM identity_clients")
    assert len(clients_made) == 1


async def test_a_rejection_gives_a_reason_and_the_switch_can_be_proposed_again(
    console: Console,
) -> None:
    files = sheets(1)
    await cleared(console, files, 1)
    admin = await console.sign_in("admin")
    switch = (await console.post("/switch", files)).json()
    decision = f"/switch/{switch['id']}/decision"
    # An admin holds both permissions, and still cannot decide their own proposal.
    assert code(await console.post(decision, {"approve": True})) == (403, "forbidden")

    await console.sign_in("finance")
    assert code(await console.post(decision, {"approve": False}))[0] == 422
    rejected = await console.post(decision, {"approve": False, "reason": "Wait for Monday"})
    assert rejected.status_code == 200
    assert (rejected.json()["state"], rejected.json()["decision_reason"]) == (
        "rejected",
        "Wait for Monday",
    )
    assert rejected.json()["clients_carried"] == 0

    await console.sign_in("admin")
    again = await console.post("/switch", files)
    assert again.status_code == 201
    assert again.json()["proposed_by"] != admin


async def test_the_database_holds_the_switch_to_two_people(
    console: Console, app_database: Database, migrator_database: Database
) -> None:
    files = sheets(1)
    await cleared(console, files, 1)
    proposer = await console.sign_in("operations")
    switch = (await console.post("/switch", files)).json()
    staff = Actor("staff", proposer)

    async def insert(sql: str, **values: Any) -> None:
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text(sql), values)

    with pytest.raises(Exception, match="second person"):
        await insert(
            "INSERT INTO migration_switch_decisions (switch_id, outcome, decided_by, decided_at) "
            "VALUES (:id, 'approved', :staff, now())",
            id=switch["id"],
            staff=proposer,
        )
    # A client is carried over only in the transaction that approved the switch.
    with pytest.raises(Exception, match="approval of the switch"):
        await insert(
            "INSERT INTO migration_carried_clients (legacy_client_ref, switch_id, client_id, "
            "carried_by, carried_at) VALUES ('1', :id, '-Nabc0000000000000C1', :staff, now())",
            id=switch["id"],
            staff=proposer,
        )
    # Not even the table owner changes the record afterwards.
    with pytest.raises(Exception, match="insert-only"):
        async with migrator_database.transaction() as session:
            await session.execute(text("UPDATE migration_switches SET as_of = as_of"))
