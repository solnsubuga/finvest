"""Identity checks against real PostgreSQL: the lifecycle, the fences and the races."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.sealing import SealError
from app.modules.account_numbers.domain.numbers import AccountNumber, AccountType
from app.modules.account_numbers.service import is_issued, issue_account_number
from app.modules.audit.service import events_for_resource
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus, DocumentType, IdentityDetails, PhotoKind
from app.modules.kyc.domain.provider import AutomaticCheck, CheckOutcome, Verdict
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.kyc import SEALER, FakeProvider, add_photos, all_passed, details, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

MANUAL = ManualProvider()


async def in_review(
    database: Database,
    clock: FakeClock,
    *,
    document_type: DocumentType = DocumentType.NATIONAL_ID,
    document_number: str | None = None,
) -> tuple[Actor, str]:
    """A client whose check waits for a person."""
    client = new_client()
    async with database.actor_transaction(client) as session:
        await ready_draft(
            session, client, clock, document_type=document_type, document_number=document_number
        )
        view = await kyc.submit(session, SEALER, client, MANUAL, clock)
    assert view.status is CheckStatus.NEEDS_REVIEW
    return client, view.id


async def submitted(database: Database, clock: FakeClock, **draft: str) -> tuple[Actor, str]:
    """A client whose check waits for the provider."""
    client = new_client()
    async with database.actor_transaction(client) as session:
        await ready_draft(session, client, clock, **draft)  # type: ignore[arg-type]
        view = await kyc.submit(session, SEALER, client, FakeProvider(), clock)
    assert view.status is CheckStatus.SUBMITTED
    return client, view.id


async def actions(database: Database, check_id: str) -> list[str]:
    async with database.actor_transaction(new_staff()) as session:
        events = await events_for_resource(session, kyc.RESOURCE, check_id)
    return [event.action for event in reversed(events)]


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


# The lifecycle ----------------------------------------------------------------------------------


async def test_a_client_builds_a_draft_step_by_step(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        assert await kyc.check_for_client(session, SEALER, client) is None
        view = await kyc.choose_document(
            session, SEALER, client, DocumentType.NATIONAL_ID, "UG", fake_clock
        )
        assert view.status is CheckStatus.DRAFT
        assert view.missing == ["front", "back", "selfie", "details"]
        await add_photos(session, client, fake_clock, PhotoKind.FRONT, PhotoKind.SELFIE)
        view = await kyc.confirm_details(session, SEALER, client, details(), fake_clock)
        assert view.missing == ["back"]
        with pytest.raises(ApplicationError) as error:
            await kyc.submit(session, SEALER, client, MANUAL, fake_clock)
        assert refused(error) == ("validation_failed", [("back", "missing")])
    async with app_database.actor_transaction(client) as session:
        view = await kyc.check_for_client(session, SEALER, client)  # type: ignore[assignment]
    assert view.status is CheckStatus.DRAFT
    assert view.submission == 0
    assert [photo.storage_key for photo in view.photos] == [
        f"kyc/{view.id}/{photo.id}" for photo in view.photos
    ]


async def test_changing_the_document_keeps_one_check_and_asks_for_details_again(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        first = await ready_draft(session, client, fake_clock)
        again = await kyc.choose_document(
            session, SEALER, client, DocumentType.NATIONAL_ID, "UG", fake_clock
        )
        assert again.details_confirmed
        changed = await kyc.choose_document(
            session, SEALER, client, DocumentType.PASSPORT, "KE", fake_clock
        )
    assert changed.id == first.id
    assert (changed.document_type, changed.document_country) == (DocumentType.PASSPORT, "KE")
    assert changed.missing == ["details"]


async def test_bad_details_are_refused_field_by_field(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    bad = IdentityDetails("N4KATO", "Amara", "x", fake_clock.now().date(), fake_clock.now().date())
    async with app_database.actor_transaction(client) as session:
        await kyc.choose_document(session, SEALER, client, DocumentType.PASSPORT, "UG", fake_clock)
        with pytest.raises(ApplicationError) as error:
            await kyc.confirm_details(session, SEALER, client, bad, fake_clock)
        with pytest.raises(ApplicationError) as photo_error:
            await kyc.add_photo(
                session,
                client,
                PhotoKind.BACK,
                content_type="image/jpeg",
                byte_size=1,
                sha256="ab" * 32,
                clock=fake_clock,
            )
        with pytest.raises(ApplicationError) as type_error:
            await kyc.add_photo(
                session,
                client,
                PhotoKind.FRONT,
                content_type="application/pdf",
                byte_size=1,
                sha256="ab" * 32,
                clock=fake_clock,
            )
    assert refused(error) == (
        "validation_failed",
        [
            ("surname", "invalid_name"),
            ("document_number", "invalid_document_number"),
            ("date_of_birth", "under_age"),
        ],
    )
    assert refused(photo_error) == ("validation_failed", [("kind", "photo_not_part_of_document")])
    assert refused(type_error) == (
        "validation_failed",
        [("content_type", "unsupported_content_type")],
    )


async def test_without_a_provider_staff_approve_and_the_account_number_is_issued(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, check_id = await in_review(app_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        queue = await kyc.review_queue(session, limit=200)
        waiting = await kyc.check_for_staff(session, SEALER, check_id)
        approved = await kyc.approve(
            session, SEALER, staff, check_id, "Photos match the ID", fake_clock
        )
    assert check_id in [item.id for item in queue]
    assert waiting.review_flags == ("no_automatic_checks",)
    assert waiting.provider == "manual"
    assert approved.status is CheckStatus.APPROVED
    assert approved.decided_by == staff.id
    assert approved.decided_at == fake_clock.now()
    assert approved.account_number is not None
    number = AccountNumber(approved.account_number)
    assert number.account_type is AccountType.PERSONAL
    async with app_database.actor_transaction(staff) as session:
        assert await is_issued(session, number)
    async with app_database.actor_transaction(client) as session:
        seen = await kyc.check_for_client(session, SEALER, client)
    assert seen is not None
    assert seen.account_number == number.value
    assert await actions(app_database, check_id) == ["kyc.check.submitted", "kyc.check.approved"]


async def test_a_staff_decision_needs_a_reason(
    app_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await in_review(app_database, fake_clock)
    staff = new_staff()
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await kyc.approve(session, SEALER, staff, check_id, "  ", fake_clock)
    assert refused(error) == ("validation_failed", [("reason", "missing")])
    # The refusal rolled the approval and its account number back.
    async with app_database.actor_transaction(staff) as session:
        view = await kyc.check_for_staff(session, SEALER, check_id)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.account_number is None


async def test_the_worker_approves_a_clean_result_when_that_is_switched_on(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await submitted(app_database, fake_clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        view = await kyc.record_provider_result(
            session,
            SEALER,
            check_id,
            1,
            "fake-reference",
            all_passed(),
            fake_clock,
            automatic_approval=True,
        )
        assert view.account_number is not None
        assert await is_issued(session, AccountNumber(view.account_number))
    assert view.status is CheckStatus.APPROVED
    assert view.decided_by is None
    assert view.provider_reference == "fake-reference"
    assert view.automatic_checks == all_passed()
    assert await actions(app_database, check_id) == ["kyc.check.submitted", "kyc.check.approved"]


async def test_a_clean_result_goes_to_a_person_unless_approval_is_switched_on(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await submitted(app_database, fake_clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        view = await kyc.record_provider_result(
            session, SEALER, check_id, 1, "fake-reference", all_passed(), fake_clock
        )
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("automatic_checks_passed",)
    assert view.automatic_checks == all_passed()
    assert view.account_number is None
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        approved = await kyc.approve(session, SEALER, staff, check_id, "Confirmed", fake_clock)
    assert approved.decided_by == staff.id


async def test_a_document_that_expired_while_waiting_is_not_approved(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, waiting = await in_review(app_database, fake_clock)
    _, automatic = await submitted(app_database, fake_clock)
    # The sample document expires on 18 September 2030.
    fake_clock.current = datetime(2030, 9, 19, tzinfo=UTC)
    staff = new_staff()
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await kyc.approve(session, SEALER, staff, waiting, "Checked", fake_clock)
    assert refused(error) == ("validation_failed", [("document_expires_on", "document_expired")])
    async with worker_database.actor_transaction(SYSTEM) as session:
        view = await kyc.record_provider_result(
            session, SEALER, automatic, 1, "ref", all_passed(), fake_clock, automatic_approval=True
        )
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("document_expired",)


async def test_the_verified_name_exists_only_once_the_client_is_approved(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, check_id = await in_review(app_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        assert await kyc.verified_name(session, client.id or "") is None
        await kyc.approve(session, SEALER, staff, check_id, "Checked", fake_clock)
        assert await kyc.verified_name(session, client.id or "") == "NAKATO Amara"
        assert await kyc.verified_name(session, new_client().id or "") is None


async def test_a_failed_automatic_check_goes_to_a_person_and_never_rejects(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await submitted(app_database, fake_clock)
    outcomes = (
        CheckOutcome(AutomaticCheck.DOCUMENT_GENUINE, Verdict.PASS),
        CheckOutcome(AutomaticCheck.SELFIE_MATCH, Verdict.UNSURE, 62),
        CheckOutcome(AutomaticCheck.SCREENING, Verdict.FAIL),
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        view = await kyc.record_provider_result(
            session, SEALER, check_id, 1, "ref-2", outcomes, fake_clock
        )
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("selfie_match_unsure", "screening_fail")
    assert view.automatic_checks == outcomes
    assert view.account_number is None
    assert (await actions(app_database, check_id))[-1] == "kyc.check.sent_to_review"


async def test_a_late_provider_answer_changes_nothing(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await submitted(app_database, fake_clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        stale = await kyc.record_provider_result(
            session, SEALER, check_id, 7, "old", all_passed(), fake_clock, automatic_approval=True
        )
        assert stale.status is CheckStatus.SUBMITTED
        await kyc.record_provider_result(
            session, SEALER, check_id, 1, "ref", all_passed(), fake_clock, automatic_approval=True
        )
        repeat = await kyc.record_provider_result(
            session, SEALER, check_id, 1, "ref", (), fake_clock
        )
    assert repeat.status is CheckStatus.APPROVED
    assert (await actions(app_database, check_id)).count("kyc.check.approved") == 1


async def test_a_refugee_id_goes_to_a_person_even_with_a_provider(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        await ready_draft(session, client, fake_clock, document_type=DocumentType.REFUGEE_ID)
        view = await kyc.submit(session, SEALER, client, FakeProvider(), fake_clock)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("document_needs_person",)


async def test_staff_ask_for_one_photo_and_the_client_retakes_only_that(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, check_id = await in_review(app_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as error:
            await kyc.ask_for_photos(
                session, SEALER, staff, check_id, frozenset(), "Retake", fake_clock
            )
        assert refused(error) == ("validation_failed", [("photos", "no_photos_named")])
        asked = await kyc.ask_for_photos(
            session,
            SEALER,
            staff,
            check_id,
            frozenset({PhotoKind.SELFIE}),
            "  Your selfie was too dark. Please retake it facing a window. ",
            fake_clock,
        )
    assert asked.status is CheckStatus.AWAITING_CLIENT
    assert asked.retake_photos == {PhotoKind.SELFIE}
    assert asked.client_message == "Your selfie was too dark. Please retake it facing a window."
    old_selfie = next(photo.id for photo in asked.photos if photo.kind is PhotoKind.SELFIE)

    fake_clock.advance(minutes=5)
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError) as error:
            await kyc.submit(session, SEALER, client, MANUAL, fake_clock)
        assert refused(error) == ("validation_failed", [("selfie", "missing")])
        with pytest.raises(ApplicationError) as error:
            await add_photos(session, client, fake_clock, PhotoKind.FRONT)
        assert error.value.code == "conflict"
        with pytest.raises(ApplicationError) as error:
            await kyc.confirm_details(session, SEALER, client, details(), fake_clock)
        assert error.value.code == "conflict"
        await add_photos(session, client, fake_clock, PhotoKind.SELFIE)
        again = await kyc.submit(session, SEALER, client, MANUAL, fake_clock)
    assert again.status is CheckStatus.NEEDS_REVIEW
    assert again.submission == 2
    assert again.retake_photos == frozenset()
    assert again.client_message is None
    assert old_selfie not in [photo.id for photo in again.photos]
    async with app_database.actor_transaction(staff) as session:
        kept = await session.execute(
            text("SELECT replaced_at FROM kyc_photos WHERE id = :id"), {"id": old_selfie}
        )
        assert kept.scalar_one() == fake_clock.now()
    assert "kyc.check.photos_requested" in await actions(app_database, check_id)


async def test_compliance_decides_an_escalated_check_and_a_rejection_is_final(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, check_id = await in_review(app_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        escalated = await kyc.escalate(
            session, SEALER, staff, check_id, "Name is on a watch list", fake_clock
        )
        assert escalated.status is CheckStatus.WITH_COMPLIANCE
        with pytest.raises(ApplicationError) as error:
            await kyc.escalate(session, SEALER, staff, check_id, "Again", fake_clock)
        assert error.value.code == "conflict"
        rejected = await kyc.reject(
            session,
            SEALER,
            staff,
            check_id,
            "Confirmed match",
            fake_clock,
            client_message="We cannot open an account for you.",
        )
    assert rejected.status is CheckStatus.REJECTED
    assert rejected.account_number is None
    assert rejected.client_message == "We cannot open an account for you."
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await kyc.approve(session, SEALER, staff, check_id, "Changed my mind", fake_clock)
    assert error.value.code == "conflict"
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(client) as session:
            await kyc.choose_document(
                session, SEALER, client, DocumentType.PASSPORT, "UG", fake_clock
            )
    assert error.value.code == "conflict"


async def test_only_the_right_kind_of_actor_runs_each_step(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, check_id = await in_review(app_database, fake_clock)
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(client) as session:
            await kyc.approve(session, SEALER, client, check_id, "Me", fake_clock)
    assert error.value.code == "forbidden"
    staff = new_staff()
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await kyc.submit(session, SEALER, staff, MANUAL, fake_clock)
    assert error.value.code == "forbidden"


# One identity document, one client ----------------------------------------------------------------


async def test_an_identity_document_opens_only_one_account(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    number = details().document_number
    _, first = await in_review(app_database, fake_clock, document_number=number)
    _, second = await in_review(app_database, fake_clock, document_number=number)
    _, third = await submitted(app_database, fake_clock, document_number=number)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.approve(session, SEALER, staff, first, "Checked", fake_clock)
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await kyc.approve(session, SEALER, staff, second, "Checked", fake_clock)
    assert error.value.code == "conflict"
    async with worker_database.actor_transaction(SYSTEM) as session:
        view = await kyc.record_provider_result(
            session, SEALER, third, 1, "ref", all_passed(), fake_clock, automatic_approval=True
        )
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("duplicate_document",)


async def test_two_approvals_racing_for_one_document_issue_one_account_number(
    test_database: object, app_database: Database, fake_clock: FakeClock
) -> None:
    number = details().document_number
    checks = [
        (await in_review(app_database, fake_clock, document_number=number))[1] for _ in range(2)
    ]
    barrier = AsyncBarrier(2)

    async def approve(check_id: str) -> str | None:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await session.execute(text("SELECT 1"))
                await barrier.wait()
                return (
                    await kyc.approve(session, SEALER, staff, check_id, "Checked", fake_clock)
                ).account_number
        except ApplicationError as error:
            assert error.code == "conflict"
            return None

    issued = await asyncio.gather(*(approve(check_id) for check_id in checks))
    winners = [value for value in issued if value is not None]
    assert len(winners) == 1
    async with app_database.actor_transaction(new_staff()) as session:
        held = await session.execute(
            text("SELECT account_number FROM kyc_checks WHERE id = ANY(:ids)"), {"ids": checks}
        )
        assert sorted(held.scalars(), key=str) == sorted([winners[0], None], key=str)


async def test_two_staff_approving_the_same_check_issue_one_account_number(
    app_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await in_review(app_database, fake_clock)
    barrier = AsyncBarrier(2)

    async def approve() -> str | None:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await session.execute(text("SELECT 1"))
                await barrier.wait()
                return (
                    await kyc.approve(session, SEALER, staff, check_id, "Checked", fake_clock)
                ).account_number
        except ApplicationError as error:
            assert error.code == "conflict"
            return None

    issued = await asyncio.gather(approve(), approve())
    assert sum(value is not None for value in issued) == 1
    assert (await actions(app_database, check_id)).count("kyc.check.approved") == 1


# Personal details are sealed --------------------------------------------------------------------


async def test_the_database_holds_no_readable_document_number_birth_date_or_tin(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    given = IdentityDetails(
        surname="NAKATO",
        given_names="Amara",
        document_number="CM94062210ABCD",
        date_of_birth=date(1994, 6, 22),
        document_expires_on=date(2030, 9, 18),
        tin="1000123456",
    )
    async with app_database.actor_transaction(client) as session:
        await kyc.choose_document(
            session, SEALER, client, DocumentType.NATIONAL_ID, "UG", fake_clock
        )
        view = await kyc.confirm_details(session, SEALER, client, given, fake_clock)
        row = (
            await session.execute(
                text(
                    "SELECT to_jsonb(kyc_checks)::text AS everything FROM kyc_checks WHERE id = :id"
                ),
                {"id": view.id},
            )
        ).one()
        sealed = (
            await session.execute(
                text(
                    "SELECT document_number_sealed, date_of_birth_sealed, tin_sealed, "
                    "document_fingerprint FROM kyc_checks WHERE id = :id"
                ),
                {"id": view.id},
            )
        ).one()
    assert view.details == given
    for secret in ("CM94062210ABCD", "1994-06-22", "1000123456"):
        assert secret not in row.everything
        for value in sealed[:3]:
            assert secret.encode() not in bytes(value)
    assert sealed.document_fingerprint == SEALER.fingerprint("UG|national_id|CM94062210ABCD")


async def test_a_sealed_value_copied_from_another_check_does_not_open(
    app_database: Database, fake_clock: FakeClock
) -> None:
    first, second = new_client(), new_client()
    async with app_database.actor_transaction(first) as session:
        source = await ready_draft(session, first, fake_clock)
        copied = (
            await session.execute(
                text("SELECT document_number_sealed FROM kyc_checks WHERE id = :id"),
                {"id": source.id},
            )
        ).scalar_one()
    with pytest.raises(SealError):
        async with app_database.actor_transaction(second) as session:
            target = await ready_draft(session, second, fake_clock)
            await session.execute(
                text("UPDATE kyc_checks SET document_number_sealed = :sealed WHERE id = :id"),
                {"id": target.id, "sealed": copied},
            )
            await kyc.check_for_client(session, SEALER, second)


async def test_changing_the_document_clears_its_fingerprint(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        view = await ready_draft(session, client, fake_clock)
        await kyc.choose_document(session, SEALER, client, DocumentType.PASSPORT, "UG", fake_clock)
        fingerprint = await session.execute(
            text("SELECT document_fingerprint FROM kyc_checks WHERE id = :id"), {"id": view.id}
        )
        assert fingerprint.scalar_one() is None


# The database holds even if a service is wrong ----------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        "provider_reference = 'made-up'",
        "automatic_checks = '[1]'",
        "review_flags = '{}', submission = 5",
        "retake_photos = '{selfie}'",
        "client_message = 'x'",
    ],
)
async def test_a_client_writes_only_their_document_and_details_on_a_draft(
    app_database: Database, fake_clock: FakeClock, change: str
) -> None:
    client = new_client()
    with pytest.raises(DBAPIError, match="this change to an identity check is not allowed"):
        async with app_database.actor_transaction(client) as session:
            view = await ready_draft(session, client, fake_clock)
            await session.execute(
                text(f"UPDATE kyc_checks SET {change} WHERE id = :id"),  # noqa: S608
                {"id": view.id},
            )


@pytest.mark.parametrize(
    "change",
    [
        "submission = submission + 1, provider_reference = 'made-up'",
        "submission = submission + 1, automatic_checks = '[1]'",
        "submission = submission + 2",
        "submission = submission + 1, surname = 'OTHER'",
    ],
)
async def test_a_client_cannot_hand_in_a_check_with_a_provider_answer_on_it(
    app_database: Database, fake_clock: FakeClock, change: str
) -> None:
    client = new_client()
    with pytest.raises(DBAPIError, match="identity check cannot go from draft to submitted"):
        async with app_database.actor_transaction(client) as session:
            view = await ready_draft(session, client, fake_clock)
            await session.execute(
                text(
                    "UPDATE kyc_checks SET status = 'submitted', submitted_at = now(), "  # noqa: S608
                    f"{change} WHERE id = :id"
                ),
                {"id": view.id},
            )


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ("surname = 'OTHER'", "this change to an identity check is not allowed"),
        ("document_expires_on = '2040-01-01'", "this change to an identity check is not allowed"),
        ("updated_at = now()", "this change to an identity check is not allowed"),
        (
            "status = 'with_compliance', surname = 'OTHER'",
            "identity check cannot go from needs_review to with_compliance",
        ),
        (
            "status = 'with_compliance', document_fingerprint = repeat('0', 64)",
            "identity check cannot go from needs_review to with_compliance",
        ),
        (
            "status = 'with_compliance', review_flags = '{}'",
            "identity check cannot go from needs_review to with_compliance",
        ),
        (
            "status = 'with_compliance', provider_reference = 'made-up'",
            "identity check cannot go from needs_review to with_compliance",
        ),
    ],
)
async def test_staff_decide_but_never_edit_a_check(
    app_database: Database, fake_clock: FakeClock, change: str, refusal: str
) -> None:
    _, check_id = await in_review(app_database, fake_clock)
    with pytest.raises(DBAPIError, match=refusal):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text(f"UPDATE kyc_checks SET {change} WHERE id = :id"),  # noqa: S608
                {"id": check_id},
            )


async def test_the_worker_notes_the_provider_reference_and_nothing_else(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await submitted(app_database, fake_clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await session.execute(
            text("UPDATE kyc_checks SET provider_reference = 'ref-1' WHERE id = :id"),
            {"id": check_id},
        )
    for change, refusal in (
        ("surname = 'OTHER'", "this change to an identity check is not allowed"),
        ("review_flags = '{x}'", "this change to an identity check is not allowed"),
        (
            "status = 'needs_review', given_names = 'Other'",
            "identity check cannot go from submitted to needs_review",
        ),
        (
            "status = 'needs_review', decided_by = '-Nabc0000000000000A1'",
            "identity check cannot go from submitted to needs_review",
        ),
    ):
        with pytest.raises(DBAPIError, match=refusal):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(f"UPDATE kyc_checks SET {change} WHERE id = :id"),  # noqa: S608
                    {"id": check_id},
                )


async def test_an_identity_check_opens_only_a_personal_account(
    app_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await in_review(app_database, fake_clock)
    with pytest.raises(DBAPIError, match="ck_kyc_checks_account_number_type"):
        async with app_database.actor_transaction(new_staff()) as session:
            number = await issue_account_number(session, AccountType.COMPANY, fake_clock)
            await session.execute(
                text(
                    "UPDATE kyc_checks SET status = 'approved', decided_at = now(), "
                    "account_number = :number WHERE id = :id"
                ),
                {"id": check_id, "number": number.value},
            )


async def test_a_client_sees_only_their_own_check_and_photos(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, check_id = await in_review(app_database, fake_clock)
    for actor, expected in ((owner, 1), (new_client(), 0), (new_staff(), 1), (SYSTEM, 0)):
        async with app_database.actor_transaction(actor) as session:
            checks = await session.execute(
                text("SELECT count(*) FROM kyc_checks WHERE id = :id"), {"id": check_id}
            )
            photos = await session.execute(
                text("SELECT count(*) FROM kyc_photos WHERE check_id = :id"), {"id": check_id}
            )
            assert (checks.scalar_one(), photos.scalar_one()) == (expected, expected * 3)
    async with app_database.transaction() as session:
        assert (await session.execute(text("SELECT count(*) FROM kyc_checks"))).scalar_one() == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE kyc_checks SET status = 'approved', account_number = '1001004827', "
        "decided_at = now() WHERE id = :id",
        "UPDATE kyc_checks SET status = 'rejected', decided_at = now() WHERE id = :id",
        "UPDATE kyc_checks SET surname = 'OTHER' WHERE id = :id",
    ],
)
async def test_a_client_cannot_decide_or_edit_a_check_under_review(
    app_database: Database, fake_clock: FakeClock, statement: str
) -> None:
    owner, check_id = await in_review(app_database, fake_clock)
    with pytest.raises(DBAPIError, match="identity check"):
        async with app_database.actor_transaction(owner) as session:
            await session.execute(text(statement), {"id": check_id})


async def test_the_api_cannot_approve_by_calling_itself_the_system(
    app_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await submitted(app_database, fake_clock)
    async with app_database.actor_transaction(SYSTEM) as session:
        changed = await session.execute(
            text("UPDATE kyc_checks SET status = 'needs_review' WHERE id = :id"), {"id": check_id}
        )
        assert changed.rowcount == 0  # type: ignore[attr-defined]
    # Staff reach the row, but a check still with the provider is not theirs to decide.
    with pytest.raises(DBAPIError, match="identity check cannot go from"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text(
                    "UPDATE kyc_checks SET status = 'rejected', decided_at = now() WHERE id = :id"
                ),
                {"id": check_id},
            )


async def test_a_decided_check_never_changes_and_nothing_is_deleted(
    application_database: Database, app_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await in_review(app_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.reject(session, SEALER, staff, check_id, "Forged document", fake_clock)
    actor = SYSTEM if application_database.engine.url.username == "finvest_worker" else staff
    with pytest.raises(DBAPIError, match="a decided identity check never changes"):
        async with application_database.actor_transaction(actor) as session:
            await session.execute(
                text("UPDATE kyc_checks SET client_message = 'x' WHERE id = :id"), {"id": check_id}
            )
    for table in ("kyc_checks", "kyc_photos"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with application_database.actor_transaction(actor) as session:
                await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608


async def test_an_approval_must_carry_a_number_from_the_registry(
    app_database: Database, fake_clock: FakeClock
) -> None:
    _, check_id = await in_review(app_database, fake_clock)
    for number, refusal in (
        (None, "ck_kyc_checks_account_number"),
        ("1001004819", "fk_kyc_checks"),
    ):
        with pytest.raises(DBAPIError, match=refusal):
            async with app_database.actor_transaction(new_staff()) as session:
                await session.execute(
                    text(
                        "UPDATE kyc_checks SET status = 'approved', decided_at = now(), "
                        "account_number = :number WHERE id = :id"
                    ),
                    {"id": check_id, "number": number},
                )


INSERT_PHOTO = text(
    "INSERT INTO kyc_photos (id, check_id, kind, storage_key, content_type, byte_size, sha256, "
    "created_at) VALUES (:id, :check_id, :kind, :key, 'image/jpeg', 1, :sha, now())"
)


def photo_row(check_id: str, kind: str) -> dict[str, str]:
    return {"id": push_id(), "check_id": check_id, "kind": kind, "key": push_id(), "sha": "ab" * 32}


async def test_staff_cannot_add_or_replace_a_client_photo(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        check_id = (await ready_draft(session, client, fake_clock)).id
    async with app_database.actor_transaction(new_staff()) as session:
        replaced = await session.execute(
            text("UPDATE kyc_photos SET replaced_at = now() WHERE check_id = :id"), {"id": check_id}
        )
        assert replaced.rowcount == 0  # type: ignore[attr-defined]
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(INSERT_PHOTO, photo_row(check_id, "front"))


@pytest.mark.parametrize("decided", [False, True])
@pytest.mark.parametrize("change", ["retire", "insert"])
async def test_a_client_cannot_swap_photos_once_the_check_is_with_staff(
    app_database: Database, fake_clock: FakeClock, decided: bool, change: str
) -> None:
    owner, check_id = await in_review(app_database, fake_clock)
    if decided:
        staff = new_staff()
        async with app_database.actor_transaction(staff) as session:
            await kyc.approve(session, SEALER, staff, check_id, "Checked", fake_clock)
    with pytest.raises(DBAPIError, match="identity photos cannot change"):
        async with app_database.actor_transaction(owner) as session:
            if change == "retire":
                await session.execute(
                    text(
                        "UPDATE kyc_photos SET replaced_at = now() "
                        "WHERE check_id = :id AND kind = 'selfie'"
                    ),
                    {"id": check_id},
                )
            else:
                await session.execute(INSERT_PHOTO, photo_row(check_id, "selfie"))
    async with app_database.actor_transaction(owner) as session:
        view = await kyc.check_for_client(session, SEALER, owner)
    assert view is not None
    assert len(view.photos) == 3


async def test_a_client_asked_for_one_photo_cannot_swap_another(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, check_id = await in_review(app_database, fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.ask_for_photos(
            session, SEALER, staff, check_id, frozenset({PhotoKind.SELFIE}), "Retake", fake_clock
        )
    for statement in (
        "UPDATE kyc_photos SET replaced_at = now() WHERE check_id = :id AND kind = 'front'",
        "INSERT INTO kyc_photos (id, check_id, kind, storage_key, content_type, byte_size, "
        "sha256, created_at) VALUES ('-Nabc0000000000000Z9', :id, 'front', 'k-front', "
        "'image/jpeg', 1, repeat('ab', 32), now())",
    ):
        with pytest.raises(DBAPIError, match="identity photos cannot change"):
            async with app_database.actor_transaction(owner) as session:
                await session.execute(text(statement), {"id": check_id})


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE kyc_photos SET replaced_at = NULL WHERE id = :id",
        "UPDATE kyc_photos SET replaced_at = replaced_at + interval '1 day' WHERE id = :id",
    ],
)
async def test_a_retired_photo_stays_retired(
    app_database: Database, fake_clock: FakeClock, statement: str
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        view = await ready_draft(session, client, fake_clock)
        old = next(photo.id for photo in view.photos if photo.kind is PhotoKind.FRONT)
        await add_photos(session, client, fake_clock, PhotoKind.FRONT)
    with pytest.raises(DBAPIError, match="only ever retired, once"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(text(statement), {"id": old})


async def test_a_photo_cannot_be_inserted_already_retired(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        check_id = (await ready_draft(session, client, fake_clock)).id
    with pytest.raises(DBAPIError, match="only ever retired, once"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO kyc_photos (id, check_id, kind, storage_key, content_type, "
                    "byte_size, sha256, created_at, replaced_at) VALUES (:id, :check_id, :kind, "
                    ":key, 'image/jpeg', 1, :sha, now(), now())"
                ),
                photo_row(check_id, "back"),
            )
