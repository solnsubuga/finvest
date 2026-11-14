"""The worker carries every client of an approved switch over, against real PostgreSQL.

Each test has a database of its own, since at most one switch is ever approved.
"""

from typing import Any

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker
from app.modules.identity.service import create_unclaimed_client
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import DocumentType, IdentityDetails
from app.modules.migration import carry_over
from app.modules.migration.jobs import MigrationJobs
from app.shared.actors import SYSTEM, Actor
from tests.integration.test_migration_api import NOW, Console
from tests.integration.test_migration_switch import (
    SEALER,
    cleared,
    console,
    person,
    sheets,
    table,
    test_database,
)
from tests.support.investments import PRESENT
from tests.unit.test_migration_sheet import clients

pytestmark = pytest.mark.integration

__all__ = ["console", "test_database"]  # the fixtures, shared with the switch tests

CLIENT_APP = "https://app.finvestug.com"


async def approved(
    console: Console, files: dict[str, str], count: int, *, verified: int | None = None
) -> str:
    await cleared(console, files, count, verified=verified)
    await console.sign_in("operations")
    proposed = await console.post("/switch", files)
    assert proposed.status_code == 201, proposed.text
    await console.sign_in("finance")
    decided = await console.post(f"/switch/{proposed.json()['id']}/decision", {"approve": True})
    assert decided.status_code == 200, decided.text
    return str(proposed.json()["id"])


def worker(database: Database) -> Worker:
    clock = PRESENT
    return Worker(
        database, MigrationJobs(database, SEALER, clock, CLIENT_APP).handlers(), clock=clock
    )


async def rows(database: Database, sql: str, **values: Any) -> list[Any]:
    return await table(database, sql, **values)


async def test_the_worker_carries_every_client_over_once(
    console: Console, worker_database: Database, migrator_database: Database
) -> None:
    files = sheets(2) | {
        # The second client was never checked by Finvest 1.0, so they do the full check.
        "clients_csv": clients(person("1"), person("2", id_verified="no")),
    }
    switch_id = await approved(console, files, 2, verified=1)
    [job] = await rows(
        migrator_database, "SELECT kind, payload FROM jobs WHERE kind LIKE 'migration.%'"
    )
    assert (job.kind, job.payload) == ("migration.carry_over", {"switch_id": switch_id})

    assert await worker(worker_database).run_once() == 1

    claims = await rows(
        migrator_database,
        "SELECT c.legacy_client_ref, c.status, c.needs_id_check FROM migration_claims AS c "
        "ORDER BY c.legacy_client_ref",
    )
    assert [(c.legacy_client_ref, c.status, c.needs_id_check) for c in claims] == [
        ("1", "invited", False),
        ("2", "invited", True),
    ]
    checks = await rows(migrator_database, "SELECT legacy_reference, status FROM kyc_checks")
    assert [(c.legacy_reference, c.status) for c in checks] == [("1", "approved")]

    # Each investment is opened with its original terms and the interest the check showed.
    opened = await rows(
        migrator_database,
        "SELECT legacy_reference, principal_minor, start_date, accrued_through, "
        "accrued_gross_minor, accrued_tax_minor, status FROM investments ORDER BY legacy_reference",
    )
    assert [
        (i.legacy_reference, i.principal_minor, str(i.start_date), str(i.accrued_through))
        for i in opened
    ] == [
        ("INV-1", 1_000_000, "2026-07-29", "2026-09-01"),
        ("INV-2", 1_000_000, "2026-07-29", "2026-09-01"),
    ]
    assert {(i.accrued_gross_minor, i.accrued_tax_minor, i.status) for i in opened} == {
        (11_643, 1_746, "active")
    }

    # The link goes by text and email, and expires with the message.
    messages = await rows(
        migrator_database,
        "SELECT channel, template, variables, deliver_by - created_at AS within "
        "FROM notification_messages WHERE template = 'claim_invitation' ORDER BY channel",
    )
    assert sorted(m.channel for m in messages) == ["email", "email", "sms", "sms"]
    assert all(m.variables["link"].startswith(f"{CLIENT_APP}/claim#") for m in messages)
    assert {m.within.total_seconds() for m in messages} == {72 * 3600}

    # Done: the sealed sheets are gone, and nothing was left behind.
    assert await rows(migrator_database, "SELECT * FROM migration_switch_files") == []
    seen = await console.overview()
    assert seen["switch"]["not_carried"] == []
    assert seen["claims"]["invited"] == 2


async def test_a_second_run_changes_nothing(
    console: Console, worker_database: Database, migrator_database: Database
) -> None:
    switch_id = await approved(console, sheets(1), 1)
    result = await carry_over.carry_over(
        worker_database, SEALER, PRESENT, switch_id, client_app=CLIENT_APP
    )
    assert (result.carried, result.problems) == (1, 0)
    again = await carry_over.carry_over(
        worker_database, SEALER, PRESENT, switch_id, client_app=CLIENT_APP
    )
    assert (again.carried, again.problems) == (1, 0)
    assert len(await rows(migrator_database, "SELECT id FROM investments")) == 1
    assert len(await rows(migrator_database, "SELECT id FROM migration_claim_links")) == 1
    entries = await rows(
        migrator_database,
        "SELECT id FROM ledger_journal_entries WHERE entry_type = 'investment.carried_over'",
    )
    assert len(entries) == 1


async def test_a_client_who_cannot_be_carried_over_is_noted_and_the_rest_go_on(
    console: Console, worker_database: Database, migrator_database: Database
) -> None:
    files = sheets(2)
    switch_id = await approved(console, files, 2)
    # Someone in Finvest 2.0 already holds the first client's identity document.
    staff = Actor("staff", await console.sign_in("operations"))
    async with console.database.actor_transaction(staff) as session:
        other = await create_unclaimed_client(
            session, PRESENT, staff, phone="+256772999999", email=None
        )
    first = person("1")
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
                document_number=first["id_number"],
                date_of_birth=NOW.date().replace(year=1990),
                document_expires_on=None,
            ),
            legacy_reference="OTHER-1",
            reason="Set-up for the test",
            clock=PRESENT,
        )

    result = await carry_over.carry_over(
        worker_database, SEALER, PRESENT, switch_id, client_app=CLIENT_APP
    )
    assert (result.carried, result.problems) == (1, 1)
    # Nothing of the first client is kept: no investment, no claim, no link.
    opened = await rows(migrator_database, "SELECT legacy_reference FROM investments")
    assert [i.legacy_reference for i in opened] == ["INV-2"]
    claims = await rows(migrator_database, "SELECT legacy_client_ref FROM migration_claims")
    assert [c.legacy_client_ref for c in claims] == ["2"]
    seen = await console.overview()
    assert seen["switch"]["not_carried"] == [{"legacy_client_ref": "1", "code": "document_taken"}]
    # A run again does not try the noted client a second time.
    again = await carry_over.carry_over(
        worker_database, SEALER, PRESENT, switch_id, client_app=CLIENT_APP
    )
    assert (again.carried, again.problems) == (1, 1)

    # The job finishes, and keeps the sealed sheets: they are the only copy of the noted client.
    assert await worker(worker_database).run_once() == 1
    [job] = await rows(
        migrator_database, "SELECT status FROM jobs WHERE kind = 'migration.carry_over'"
    )
    assert job.status == "succeeded"
    assert len(await rows(migrator_database, "SELECT switch_id FROM migration_switch_files")) == 1


async def test_only_the_worker_notes_a_client_it_could_not_carry_over(
    console: Console, app_database: Database
) -> None:
    switch_id = await approved(console, sheets(1), 1)
    staff = Actor("staff", await console.sign_in("admin"))
    with pytest.raises(Exception, match="permission denied"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO migration_carry_problems (switch_id, legacy_client_ref, code, "
                    "noted_at) VALUES (:id, '1', 'refused', now())"
                ),
                {"id": switch_id},
            )
