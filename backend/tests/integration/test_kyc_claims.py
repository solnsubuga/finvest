"""Claim checks: the selfie and ID photo of a client carried over from Finvest 1.0."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.kyc import claims
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import DocumentType, PhotoKind
from app.modules.kyc.domain.claims import ClaimCheckStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.kyc import SEALER, SHA256, details, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


async def carried_over(worker_database: Database, clock: FakeClock) -> tuple[Actor, str]:
    """A client whose identity check came over from Finvest 1.0. Returns them and the check."""
    client = new_client()
    async with worker_database.actor_transaction(SYSTEM) as session:
        view = await kyc.record_carried_over_check(
            session,
            SEALER,
            SYSTEM,
            client_id=client.id or "",
            document_type=DocumentType.NATIONAL_ID,
            document_country="UG",
            details=details(),
            legacy_reference=f"FV1-{push_id()}",
            reason="Carried over at the switch",
            clock=clock,
        )
    return client, view.id


async def take(
    database: Database, client: Actor, clock: FakeClock, *kinds: PhotoKind
) -> claims.ClaimCheckView:
    async with database.actor_transaction(client) as session:
        for kind in kinds:
            await claims.add_photo(
                session,
                client,
                kind,
                content_type="image/jpeg",
                byte_size=2048,
                sha256=SHA256,
                clock=clock,
            )
        view = await claims.for_client(session, client)
    assert view is not None
    return view


async def handed_in(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> tuple[Actor, claims.ClaimCheckView]:
    client, _ = await carried_over(worker_database, clock)
    async with app_database.actor_transaction(client) as session:
        await claims.start(session, client, clock)
    await take(app_database, client, clock, PhotoKind.FRONT, PhotoKind.SELFIE)
    async with app_database.actor_transaction(client) as session:
        view = await claims.submit(session, client, clock)
    return client, view


async def decided_events(worker_database: Database, claim_check_id: str) -> list[object]:
    async with worker_database.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT payload FROM outbox_events WHERE topic = 'kyc.claim_check_decided' "
                "AND idempotency_key = :id"
            ),
            {"id": claim_check_id},
        )
        return list(rows.scalars())


async def test_a_carried_over_client_hands_in_a_selfie_and_the_front_of_their_id(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, check_id = await carried_over(worker_database, fake_clock)
    async with app_database.actor_transaction(client) as session:
        assert await claims.for_client(session, client) is None
        started = await claims.start(session, client, fake_clock)
        again = await claims.start(session, client, fake_clock)
    assert started.id == again.id
    assert started.check_id == check_id
    assert started.status is ClaimCheckStatus.DRAFT
    assert started.missing == ["front", "selfie"]

    view = await take(app_database, client, fake_clock, PhotoKind.SELFIE)
    assert view.missing == ["front"]
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(client) as session:
            await claims.submit(session, client, fake_clock)
    assert [p.field_path[0] for p in error.value.fields] == ["front"]
    with pytest.raises(ApplicationError) as error:
        await take(app_database, client, fake_clock, PhotoKind.BACK)
    assert error.value.fields[0].code == "photo_not_part_of_claim"

    await take(app_database, client, fake_clock, PhotoKind.FRONT)
    async with app_database.actor_transaction(client) as session:
        submitted = await claims.submit(session, client, fake_clock)
    assert submitted.status is ClaimCheckStatus.NEEDS_REVIEW
    assert submitted.submission == 1
    assert submitted.submitted_at == fake_clock.now()
    # Handed in, the photos can no longer change.
    with pytest.raises(ApplicationError) as error:
        await take(app_database, client, fake_clock, PhotoKind.SELFIE)
    assert error.value.code == "conflict"


async def test_only_a_carried_over_client_has_a_claim_check(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        await ready_draft(session, client, fake_clock)
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(client) as session:
            await claims.start(session, client, fake_clock)
    assert error.value.code == "not_found"


async def test_staff_say_the_photos_match_and_the_decision_is_published(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, view = await handed_in(app_database, worker_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        queue = await claims.review_queue(session, limit=200)
        assert view.id in [item.id for item in queue]
        photos = await claims.photos_for_viewing(session, staff, view.id, fake_clock)
        matched = await claims.decide(
            session, staff, view.id, matched=True, reason="Same face as the ID", clock=fake_clock
        )
    assert {photo.kind for photo in photos} == {PhotoKind.FRONT, PhotoKind.SELFIE}
    assert matched.status is ClaimCheckStatus.MATCHED
    assert matched.decided_by == staff.id
    assert await decided_events(worker_database, view.id) == [
        {"client_id": client.id, "claim_check_id": view.id, "matched": True}
    ]
    async with app_database.actor_transaction(staff) as session:
        events = await events_for_resource(session, claims.RESOURCE, view.id)
        with pytest.raises(ApplicationError) as error:
            await claims.decide(
                session, staff, view.id, matched=False, reason="Changed my mind", clock=fake_clock
            )
    assert error.value.code == "conflict"
    assert [event.action for event in reversed(events)] == [
        "kyc.claim_check.submitted",
        "kyc.claim_check.photos_viewed",
        "kyc.claim_check.matched",
    ]


async def test_photos_that_do_not_match_are_final(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, view = await handed_in(app_database, worker_database, fake_clock)
    staff = new_staff()
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await claims.decide(
                session, staff, view.id, matched=False, reason=" ", clock=fake_clock
            )
    assert error.value.fields[0].code == "missing"
    async with app_database.actor_transaction(staff) as session:
        refused = await claims.decide(
            session, staff, view.id, matched=False, reason="Different person", clock=fake_clock
        )
    assert refused.status is ClaimCheckStatus.NOT_MATCHED
    assert await decided_events(worker_database, view.id) == [
        {"client_id": client.id, "claim_check_id": view.id, "matched": False}
    ]


async def test_staff_ask_for_the_selfie_again_and_the_client_retakes_only_that(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, view = await handed_in(app_database, worker_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as error:
            await claims.ask_for_photos(
                session, staff, view.id, frozenset({PhotoKind.BACK}), "Retake", fake_clock
            )
        assert error.value.fields[0].code == "photo_not_part_of_claim"
        asked = await claims.ask_for_photos(
            session,
            staff,
            view.id,
            frozenset({PhotoKind.SELFIE}),
            "Your face is hidden. Please take it again.",
            fake_clock,
        )
    assert asked.status is ClaimCheckStatus.AWAITING_CLIENT
    async with app_database.actor_transaction(client) as session:
        mine = await claims.for_client(session, client)
    assert mine is not None
    assert (mine.missing, mine.retaken) == (["selfie"], frozenset())
    assert mine.client_message == "Your face is hidden. Please take it again."

    fake_clock.advance(minutes=2)
    with pytest.raises(ApplicationError) as error:
        await take(app_database, client, fake_clock, PhotoKind.FRONT)
    assert error.value.code == "conflict"
    retaken = await take(app_database, client, fake_clock, PhotoKind.SELFIE)
    assert (retaken.missing, retaken.retaken) == ([], frozenset({PhotoKind.SELFIE}))
    async with app_database.actor_transaction(client) as session:
        again = await claims.submit(session, client, fake_clock)
    assert (again.status, again.submission, again.client_message) == (
        ClaimCheckStatus.NEEDS_REVIEW,
        2,
        None,
    )


async def test_a_client_cannot_decide_and_staff_cannot_hand_in(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client, view = await handed_in(app_database, worker_database, fake_clock)
    for actor, statement in (
        (
            client,
            "UPDATE kyc_claim_checks SET status = 'matched', decided_at = now(), "
            "decided_by = client_id WHERE id = :id",
        ),
        (new_staff(), "UPDATE kyc_claim_checks SET submission = 5 WHERE id = :id"),
    ):
        with pytest.raises(DBAPIError, match="claim check cannot go from"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(text(statement), {"id": view.id})


async def test_a_decided_claim_check_never_changes(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, view = await handed_in(app_database, worker_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await claims.decide(session, staff, view.id, matched=True, reason="Match", clock=fake_clock)
    with pytest.raises(DBAPIError, match="a decided claim check never changes"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("UPDATE kyc_claim_checks SET client_message = 'x' WHERE id = :id"),
                {"id": view.id},
            )


async def test_a_client_cannot_start_a_claim_check_on_someone_elses_identity(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await carried_over(worker_database, fake_clock)
    intruder = new_client()
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(intruder) as session:
            await session.execute(
                text(
                    "INSERT INTO kyc_claim_checks (id, client_id, check_id, created_at, "
                    "updated_at) VALUES (:id, :client_id, :check_id, now(), now())"
                ),
                {"id": push_id(), "client_id": intruder.id, "check_id": check_id},
            )


async def test_the_worker_only_reads_claim_checks(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, view = await handed_in(app_database, worker_database, fake_clock)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE kyc_claim_checks SET status = 'matched' WHERE id = :id"),
                {"id": view.id},
            )
