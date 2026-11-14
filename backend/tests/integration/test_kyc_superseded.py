"""A carried-over client whose claim photos did not match does the full ID check (0371)."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.kyc import claims
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus, DocumentType, PhotoKind
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.kyc import SEALER, VERIFIED_NAME, add_photos, details, ready_draft, verify
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

_STATE = text(
    "SELECT status, account_number, superseded_at, superseded_by, superseded_by_claim "
    "FROM kyc_checks WHERE id = :id"
)


async def carried_over(
    worker_database: Database, clock: FakeClock, document_number: str
) -> tuple[Actor, kyc.CheckView]:
    client = new_client()
    async with worker_database.actor_transaction(SYSTEM) as session:
        view = await kyc.record_carried_over_check(
            session,
            SEALER,
            SYSTEM,
            client_id=client.id or "",
            document_type=DocumentType.NATIONAL_ID,
            document_country="UG",
            details=details(document_number),
            legacy_reference=f"FV1-{push_id()}",
            reason="Carried over at the switch",
            clock=clock,
        )
    return client, view


async def claim_handed_in(app_database: Database, client: Actor, clock: FakeClock) -> str:
    async with app_database.actor_transaction(client) as session:
        await claims.start(session, client, clock)
        for kind in (PhotoKind.FRONT, PhotoKind.SELFIE):
            await claims.add_photo(
                session,
                client,
                kind,
                content_type="image/jpeg",
                byte_size=2048,
                sha256="ab" * 32,
                clock=clock,
            )
        return (await claims.submit(session, client, clock)).id


async def not_matched(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> tuple[Actor, Actor, kyc.CheckView, str, str]:
    """A carried-over client whose claim photos staff found did not match."""
    number = uuid.uuid4().hex.upper()
    client, check = await carried_over(worker_database, clock, number)
    claim_id = await claim_handed_in(app_database, client, clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await claims.decide(
            session, staff, claim_id, matched=False, reason="Different person", clock=clock
        )
    return client, staff, check, claim_id, number


async def test_photos_that_do_not_match_supersede_the_carried_over_check(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, staff, check, claim_id, _ = await not_matched(app_database, worker_database, fake_clock)
    assert check.account_number is not None
    async with app_database.actor_transaction(staff) as session:
        state = (await session.execute(_STATE, {"id": check.id})).one()
        summary = await kyc.check_summary(session, client.id or "")
        identity = (await kyc.identities(session, [client.id or ""]))[client.id or ""]
        events = await events_for_resource(session, kyc.RESOURCE, check.id)
        assert await kyc.verified_name(session, client.id or "") is None
        assert await kyc.account_holder(session, client.id or "") is None
        assert await kyc.client_for_account_number(session, check.account_number) == client.id
        staff_view = await kyc.check_for_staff(session, SEALER, check.id)
    assert (state.status, state.account_number) == ("superseded", check.account_number)
    assert state.superseded_at == fake_clock.now()
    assert (state.superseded_by, state.superseded_by_claim) == (staff.id, claim_id)
    assert staff_view.status is CheckStatus.SUPERSEDED
    # Staff still see the client and their number, but no longer as verified.
    assert summary is not None
    assert (summary.id, summary.status) == (check.id, "superseded")
    assert identity.verified_name is None
    assert identity.account_number == check.account_number
    assert events[0].action == "kyc.check.superseded"
    assert events[0].details == {"claim_check_id": claim_id}
    async with app_database.actor_transaction(client) as session:
        # The client has no check until they choose a document for the full one.
        assert await kyc.check_for_client(session, SEALER, client) is None
    async with worker_database.actor_transaction(SYSTEM) as session:
        notices = await session.execute(
            text("SELECT event, link_kind FROM notifications WHERE client_id = :id"),
            {"id": client.id},
        )
        assert [tuple(row) for row in notices] == [("kyc_full_check_needed", "identity_check")]


async def test_the_full_check_approved_keeps_the_account_number(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, _, check, _, number = await not_matched(app_database, worker_database, fake_clock)
    # The same document as the one carried over: the superseded check does not hold it.
    async with app_database.actor_transaction(client) as session:
        draft = await ready_draft(session, client, fake_clock, document_number=number)
        assert draft.id != check.id
        assert draft.status is CheckStatus.DRAFT
        handed = await kyc.submit(session, SEALER, client, ManualProvider(), fake_clock)
    reviewer = new_staff()
    async with app_database.actor_transaction(reviewer) as session:
        approved = await kyc.approve(
            session, SEALER, reviewer, handed.id, "Documents match", fake_clock
        )
        holder = await kyc.account_holder(session, client.id or "")
        summary = await kyc.check_summary(session, client.id or "")
        assert await kyc.verified_name(session, client.id or "") == VERIFIED_NAME
        assert await kyc.client_for_account_number(session, number_of(check)) == client.id
        old = (await session.execute(_STATE, {"id": check.id})).one()
    assert approved.account_number == check.account_number
    assert holder is not None and holder.account_number == check.account_number
    assert summary is not None and summary.id == handed.id
    assert (old.status, old.account_number) == ("superseded", check.account_number)


def number_of(check: kyc.CheckView) -> str:
    assert check.account_number is not None
    return check.account_number


async def test_photos_that_match_leave_the_carried_over_check_approved(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, check = await carried_over(worker_database, fake_clock, uuid.uuid4().hex.upper())
    claim_id = await claim_handed_in(app_database, client, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await claims.decide(session, staff, claim_id, matched=True, reason="Same", clock=fake_clock)
        state = (await session.execute(_STATE, {"id": check.id})).one()
    assert state.status == "approved"
    async with app_database.actor_transaction(client) as session:
        mine = await kyc.check_for_client(session, SEALER, client)
    assert mine is not None and mine.id == check.id


async def test_a_superseded_check_never_changes_and_the_client_has_one_live_check(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, staff, check, _, _ = await not_matched(app_database, worker_database, fake_clock)
    for statement in (
        "UPDATE kyc_checks SET status = 'approved', superseded_at = NULL, superseded_by = NULL, "
        "superseded_by_claim = NULL WHERE id = :id",
        "UPDATE kyc_checks SET client_message = 'x' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="a decided identity check never changes"):
            async with app_database.actor_transaction(staff) as session:
                await session.execute(text(statement), {"id": check.id})
    async with app_database.actor_transaction(client) as session:
        await kyc.choose_document(
            session, SEALER, client, DocumentType.NATIONAL_ID, "UG", fake_clock
        )
    # A second live check for the same client is refused by the database.
    with pytest.raises(DBAPIError, match="uq_kyc_checks_client"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO kyc_checks (id, client_id, document_type, document_country, "
                    "created_at, updated_at) VALUES (:id, :client, 'passport', 'UG', now(), now())"
                ),
                {"id": push_id(), "client": client.id},
            )


async def test_only_a_carried_over_check_with_unmatched_claim_photos_is_superseded(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    supersede = text(
        "UPDATE kyc_checks SET status = 'superseded', superseded_at = now(), "
        "superseded_by = :staff, superseded_by_claim = :claim WHERE id = :id"
    )
    staff = new_staff()
    # Carried over, but its claim check is still waiting for staff.
    client, check = await carried_over(worker_database, fake_clock, uuid.uuid4().hex.upper())
    claim_id = await claim_handed_in(app_database, client, fake_clock)
    with pytest.raises(DBAPIError, match="only a carried-over check whose claim photos"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(supersede, {"staff": staff.id, "claim": claim_id, "id": check.id})
    # A full check the client handed in and staff approved.
    own = new_client()
    await verify(app_database, own, fake_clock)
    async with app_database.actor_transaction(staff) as session:
        summary = await kyc.check_summary(session, own.id or "")
        assert summary is not None
        with pytest.raises(ApplicationError) as error:
            await kyc.supersede_carried_over(
                session, staff, summary.id, claim_check_id=claim_id, clock=fake_clock
            )
    assert error.value.code == "conflict"
    with pytest.raises(DBAPIError, match="only a carried-over check whose claim photos"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                supersede, {"staff": staff.id, "claim": claim_id, "id": summary.id}
            )


async def test_a_kept_account_number_is_never_given_to_another_client(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, _, check, _, _ = await not_matched(app_database, worker_database, fake_clock)
    other = new_client()
    async with app_database.actor_transaction(other) as session:
        await ready_draft(session, other, fake_clock)
        handed = await kyc.submit(session, SEALER, other, ManualProvider(), fake_clock)
    staff = new_staff()
    with pytest.raises(DBAPIError, match="an account number belongs to one client"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE kyc_checks SET status = 'approved', account_number = :number, "
                    "decided_at = now(), decided_by = :staff WHERE id = :id"
                ),
                {"number": check.account_number, "staff": staff.id, "id": handed.id},
            )


async def test_the_new_draft_takes_photos_of_its_own(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, _, _, _, _ = await not_matched(app_database, worker_database, fake_clock)
    async with app_database.actor_transaction(client) as session:
        await kyc.choose_document(session, SEALER, client, DocumentType.PASSPORT, "UG", fake_clock)
        await add_photos(session, client, fake_clock, PhotoKind.FRONT)
        mine = await kyc.check_for_client(session, SEALER, client)
    assert mine is not None
    assert [photo.kind for photo in mine.photos] == [PhotoKind.FRONT]
