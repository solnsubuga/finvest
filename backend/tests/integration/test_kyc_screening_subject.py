"""What the screening module reads about a client from their identity check."""

from datetime import date

import pytest

from app.infrastructure.database import Database
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import DocumentType
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import SYSTEM, Actor
from tests.support.clock import FakeClock
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

SUBJECT = kyc.ScreeningSubject(
    surname="NAKATO",
    given_names="Amara",
    date_of_birth=date(1994, 6, 22),
    document_type=DocumentType.NATIONAL_ID,
    document_country="UG",
)


async def subject(database: Database, client: Actor) -> kyc.ScreeningSubject | None:
    async with database.actor_transaction(SYSTEM) as session:
        return await kyc.screening_subject(session, SEALER, client.id or "")


async def handed_in(database: Database, clock: FakeClock) -> tuple[Actor, str]:
    client = new_client()
    async with database.actor_transaction(client) as session:
        await ready_draft(session, client, clock)
        check = await kyc.submit(session, SEALER, client, ManualProvider(), clock)
    return client, check.id


async def test_a_handed_in_check_is_screened_by_its_details_without_the_id_number(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, check_id = await handed_in(app_database, fake_clock)
    found = await subject(worker_database, client)
    assert found == SUBJECT
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.approve(session, SEALER, staff, check_id, "Documents match", fake_clock)
        assert await kyc.screening_subject(session, SEALER, client.id or "") == found


async def test_nothing_before_hand_in_or_after_a_rejection(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    drafting = new_client()
    async with app_database.actor_transaction(drafting) as session:
        await ready_draft(session, drafting, fake_clock)
    assert await subject(worker_database, drafting) is None
    assert await subject(worker_database, new_client()) is None

    client, check_id = await handed_in(app_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.reject(session, SEALER, staff, check_id, "Forged document", fake_clock)
    assert await subject(worker_database, client) is None


async def test_a_client_cannot_read_someone_elses(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, _ = await handed_in(app_database, fake_clock)
    intruder = new_client()
    async with app_database.actor_transaction(intruder) as session:
        assert await kyc.screening_subject(session, SEALER, client.id or "") is None
