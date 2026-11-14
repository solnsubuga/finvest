"""Identity checks carried over from Finvest 1.0 at the switch."""

from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.account_numbers.domain.numbers import AccountNumber, AccountType
from app.modules.account_numbers.service import is_issued
from app.modules.audit.service import events_for_resource
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus, DocumentType, IdentityDetails
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.kyc import SEALER, details, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


async def carry_over(
    database: Database,
    clock: FakeClock,
    *,
    client_id: str | None = None,
    reference: str | None = None,
    identity: IdentityDetails | None = None,
    actor: Actor = SYSTEM,
) -> kyc.CheckView:
    async with database.actor_transaction(actor) as session:
        return await kyc.record_carried_over_check(
            session,
            SEALER,
            actor,
            client_id=client_id or new_client().id or "",
            document_type=DocumentType.NATIONAL_ID,
            document_country="UG",
            details=identity or details(),
            legacy_reference=reference or f"FV1-{push_id()}",
            reason="Checked by Finvest 1.0; carried over at the switch",
            clock=clock,
        )


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def test_the_worker_carries_over_an_approved_check_with_a_new_account_number(
    worker_database: Database, app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    reference = f"FV1-{push_id()}"
    view = await carry_over(worker_database, fake_clock, client_id=client.id, reference=reference)
    assert view.status is CheckStatus.APPROVED
    assert view.source == "finvest1"
    assert view.submission == 0
    assert view.submitted_at is None
    assert view.provider is None
    assert view.photos == ()
    assert view.decided_by is None
    assert view.decided_at == fake_clock.now()
    assert view.details is not None
    assert view.details.surname == "NAKATO"
    assert view.account_number is not None
    number = AccountNumber(view.account_number)
    assert number.account_type is AccountType.PERSONAL
    async with app_database.actor_transaction(client) as session:
        seen = await kyc.check_for_client(session, SEALER, client)
        assert await kyc.verified_name(session, client.id) is not None
        told = await session.execute(
            text("SELECT count(*) FROM notifications WHERE link_id = :id"), {"id": view.id}
        )
    assert seen is not None
    assert seen.account_number == number.value
    # The client hears at the claim, not at the switch.
    assert told.scalar_one() == 0
    async with app_database.actor_transaction(new_staff()) as session:
        assert await is_issued(session, number)
        events = await events_for_resource(session, kyc.RESOURCE, view.id)
    assert [(event.action, event.details) for event in events] == [
        ("kyc.check.carried_over", {"legacy_reference": reference})
    ]


async def test_an_unknown_expiry_is_accepted_and_an_expired_document_is_not(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    unknown = await carry_over(
        worker_database, fake_clock, identity=replace(details(), document_expires_on=None)
    )
    assert unknown.details is not None
    assert unknown.details.document_expires_on is None
    with pytest.raises(ApplicationError) as error:
        await carry_over(
            worker_database,
            fake_clock,
            identity=replace(details(), document_expires_on=date(2020, 1, 1)),
        )
    assert refused(error) == ("validation_failed", [("document_expires_on", "document_expired")])


async def test_carrying_over_again_returns_the_same_check(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    client_id = new_client().id
    reference = f"FV1-{push_id()}"
    first = await carry_over(worker_database, fake_clock, client_id=client_id, reference=reference)
    fake_clock.advance(minutes=5)
    again = await carry_over(worker_database, fake_clock, client_id=client_id, reference=reference)
    assert again.id == first.id
    assert again.account_number == first.account_number
    # The same reference for someone else is a mistake in the switch.
    with pytest.raises(ApplicationError) as error:
        await carry_over(worker_database, fake_clock, reference=reference)
    assert error.value.code == "conflict"


async def test_a_client_with_a_check_of_their_own_is_not_carried_over(
    worker_database: Database, app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        await ready_draft(session, client, fake_clock)
    with pytest.raises(ApplicationError) as error:
        await carry_over(worker_database, fake_clock, client_id=client.id)
    assert error.value.code == "conflict"


async def test_a_document_already_approved_for_someone_else_is_refused(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    identity = details()
    await carry_over(worker_database, fake_clock, identity=identity)
    with pytest.raises(ApplicationError) as error:
        await carry_over(worker_database, fake_clock, identity=identity)
    assert refused(error) == ("validation_failed", [("document_number", "document_taken")])


async def test_bad_input_is_refused_before_anything_is_written(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    with pytest.raises(ApplicationError) as error:
        await carry_over(worker_database, fake_clock, reference="not a reference")
    assert refused(error) == ("validation_failed", [("legacy_reference", "invalid")])
    with pytest.raises(ApplicationError) as error:
        await carry_over(
            worker_database, fake_clock, identity=replace(details(), document_number="?")
        )
    assert refused(error) == (
        "validation_failed",
        [("document_number", "invalid_document_number")],
    )


async def test_only_the_system_carries_a_check_over(
    app_database: Database, fake_clock: FakeClock
) -> None:
    with pytest.raises(ApplicationError) as error:
        await carry_over(app_database, fake_clock, actor=new_staff())
    assert error.value.code == "forbidden"


INSERT = (
    "INSERT INTO kyc_checks (id, client_id, status, document_type, document_country, surname, "
    "given_names, document_number_sealed, date_of_birth_sealed, document_fingerprint, "
    "details_confirmed_at, decided_at, decided_by, account_number, source, legacy_reference, "
    "created_at, updated_at) VALUES (:id, :client_id, :status, 'national_id', 'UG', 'NAKATO', "
    "'Amara', :sealed, :sealed, :fingerprint, now(), :decided_at, :decided_by, NULL, :source, "
    ":reference, now(), now())"
)


def row(client_id: str, **values: object) -> dict[str, object]:
    return {
        "id": push_id(),
        "client_id": client_id,
        "status": "approved",
        "sealed": b"\x01" * 40,
        "fingerprint": "0" * 64,
        "decided_at": None,
        "decided_by": None,
        "source": "finvest1",
        "reference": f"FV1-{push_id()}",
        **values,
    }


async def test_the_api_connection_can_never_insert_a_carried_over_check(
    app_database: Database,
) -> None:
    client = new_client()
    assert client.id is not None
    staff = new_staff()
    for actor, values in (
        (client, {"status": "draft"}),
        (staff, {"decided_by": staff.id}),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(text(INSERT), row(client.id, **values))


async def test_the_worker_inserts_only_approved_carried_over_checks(
    worker_database: Database,
) -> None:
    client_id = new_client().id or ""
    for values in (
        {"source": "finvest2", "reference": None},
        {"status": "needs_review"},
        {"decided_by": new_staff().id},
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(INSERT), row(client_id, **values))


async def test_a_carried_over_check_never_changes(
    worker_database: Database, app_database: Database, fake_clock: FakeClock
) -> None:
    view = await carry_over(worker_database, fake_clock)
    for database, actor in ((app_database, new_staff()), (worker_database, SYSTEM)):
        with pytest.raises(DBAPIError, match="a decided identity check never changes"):
            async with database.actor_transaction(actor) as session:
                await session.execute(
                    text("UPDATE kyc_checks SET surname = 'OTHER' WHERE id = :id"),
                    {"id": view.id},
                )
