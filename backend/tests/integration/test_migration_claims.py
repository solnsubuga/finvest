"""Claims of migrated accounts against real PostgreSQL, as the API and the worker see them."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.migration import claims
from app.modules.migration.domain.claim import ClaimStatus, IdentityStep, LinkProblem
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

START = datetime(2026, 12, 1, 9, tzinfo=UTC)


async def a_claim(worker: Database, clock: FakeClock, *, needs_id_check: bool = False) -> str:
    client_id = push_id()
    async with worker.actor_transaction(SYSTEM) as session:
        await claims.create_claim(
            session,
            SYSTEM,
            clock,
            client_id=client_id,
            legacy_client_ref=f"CL-{client_id[-8:]}",
            needs_id_check=needs_id_check,
        )
    return client_id


async def invite(worker: Database, clock: FakeClock, client_id: str) -> str:
    async with worker.actor_transaction(SYSTEM) as session:
        return await claims.send_link(session, SYSTEM, clock, client_id)


async def open_link(app: Database, clock: FakeClock, token: str) -> claims.OpenedLink:
    async with app.transaction() as session:
        return await claims.open_link(session, clock, token)


async def refusal(app: Database, clock: FakeClock, token: str) -> LinkProblem:
    with pytest.raises(claims.LinkRefused) as error:
        await open_link(app, clock, token)
    assert error.value.code == "not_found"
    return error.value.problem


async def as_client(app: Database, client_id: str, step, *args, **kwargs):  # type: ignore[no-untyped-def]
    client = Actor("client", client_id)
    async with app.actor_transaction(client) as session:
        return await step(session, client, *args, **kwargs)


async def test_a_client_claims_from_invitation_to_confirmation(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock, needs_id_check=True)
    token = await invite(worker_database, clock, client_id)
    assert len(token) == 43

    link = await open_link(app_database, clock, token)
    assert link.client_id == client_id
    view = await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    assert view.status is ClaimStatus.SIGNED_IN
    assert view.identity_step is IdentityStep.FULL_ID_CHECK
    # A link works once.
    assert await refusal(app_database, clock, token) is LinkProblem.USED

    await as_client(app_database, client_id, claims.submit_identity, clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        back = await claims.record_identity_result(session, SYSTEM, clock, client_id, matched=False)
    assert back.status is ClaimStatus.SIGNED_IN
    await as_client(app_database, client_id, claims.submit_identity, clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await claims.record_identity_result(session, SYSTEM, clock, client_id, matched=True)
    done = await as_client(app_database, client_id, claims.confirm_investments, clock)
    assert done.status is ClaimStatus.CONFIRMED

    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        events = await events_for_resource(session, "migration_claim", client_id)
    assert sorted(event.action for event in events) == sorted(
        [
            "migration.claim_invited",
            "migration.claim_link_sent",
            "migration.claim_signed_in",
            "migration.claim_identity_submitted",
            "migration.claim_signed_in",
            "migration.claim_identity_submitted",
            "migration.claim_identity_confirmed",
            "migration.claim_confirmed",
        ]
    )


async def test_the_database_keeps_only_the_hash_of_a_link(
    worker_database: Database, migrator_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    token = await invite(worker_database, clock, client_id)
    async with migrator_database.transaction() as session:
        stored = (
            await session.execute(
                text("SELECT token_hash FROM migration_claim_links WHERE client_id = :id"),
                {"id": client_id},
            )
        ).scalar_one()
    assert stored != token and token not in stored and len(stored) == 64


async def test_a_link_that_does_not_open_says_only_that(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    first = await invite(worker_database, clock, client_id)

    assert await refusal(app_database, clock, "x" * 43) is LinkProblem.NOT_FOUND
    assert await refusal(app_database, clock, "not a token") is LinkProblem.NOT_FOUND

    # Staff send a fresh link, which cancels the first.
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        second = await claims.send_link(session, staff, clock, client_id)
    assert await refusal(app_database, clock, first) is LinkProblem.CANCELLED

    clock.advance(hours=72)
    assert await refusal(app_database, clock, second) is LinkProblem.EXPIRED


async def test_reminders_go_weekly_and_stop_after_eight(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    await invite(worker_database, clock, client_id)

    with pytest.raises(ApplicationError) as error:
        await invite(worker_database, clock, client_id)
    assert error.value.code == "conflict"

    for sent in range(1, 9):
        clock.advance(days=7)
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert client_id in await claims.reminders_due(session, clock, limit=100_000)
        await invite(worker_database, clock, client_id)
        async with worker_database.actor_transaction(SYSTEM) as session:
            view = await claims.claim_for(session, client_id)
        assert view is not None and view.reminders_sent == sent

    clock.advance(days=7)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert client_id not in await claims.reminders_due(session, clock, limit=100_000)
    with pytest.raises(ApplicationError):
        await invite(worker_database, clock, client_id)
    # Staff can still send one by hand, after calling the client.
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await claims.send_link(session, staff, clock, client_id)


async def test_a_reported_problem_is_closed_by_staff_with_a_reason(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    await as_client(app_database, client_id, claims.submit_identity, clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await claims.record_identity_result(session, SYSTEM, clock, client_id, matched=True)

    with pytest.raises(ApplicationError) as error:
        await as_client(app_database, client_id, claims.report_problem, clock, description="  ")
    assert error.value.code == "validation_failed"
    view = await as_client(
        app_database,
        client_id,
        claims.report_problem,
        clock,
        description="  My  fixed deposit of 2025 is missing ",
    )
    assert (view.status, view.problem_text) == (
        ClaimStatus.PROBLEM_REPORTED,
        "My fixed deposit of 2025 is missing",
    )

    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        closed = await claims.close_problem(
            session, staff, clock, client_id, reason="Found on the 1.0 paper register"
        )
    assert (closed.status, closed.problem_text) == (ClaimStatus.IDENTITY_CONFIRMED, None)


async def test_a_step_can_only_be_taken_by_whom_the_rules_name(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    other = push_id()

    # A client cannot confirm before their identity is confirmed, nor touch another's claim.
    with pytest.raises(ApplicationError) as error:
        await as_client(app_database, client_id, claims.confirm_investments, clock)
    assert error.value.code == "conflict"
    with pytest.raises(ApplicationError) as error:
        await as_client(app_database, other, claims.submit_identity, clock)
    assert error.value.code == "not_found"
    # Only the system creates claims and reports an identity check.
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(new_staff()) as session:
            await claims.create_claim(
                session,
                new_staff(),
                clock,
                client_id=other,
                legacy_client_ref="X",
                needs_id_check=False,
            )
    assert error.value.code == "forbidden"

    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    with pytest.raises(ApplicationError) as error:
        await as_client(app_database, other, claims.record_signed_in, clock, link)
    assert error.value.code == "forbidden"


async def test_the_database_refuses_a_step_the_rules_do_not_allow(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    jump = text("UPDATE migration_claims SET status = :status WHERE client_id = :id")

    # A claim starts not invited, with no reminders.
    start = text(
        "INSERT INTO migration_claims (client_id, legacy_client_ref, needs_id_check, status, "
        "reminders_sent, created_at, updated_at) VALUES (:id, :ref, false, :status, :sent, "
        "now(), now())"
    )
    for status, sent in (("invited", 0), ("confirmed", 0), ("not_invited", 1)):
        with pytest.raises(DBAPIError, match="a claim starts not invited"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                other = push_id()
                await session.execute(
                    start, {"id": other, "ref": f"CL-{other[-8:]}", "status": status, "sent": sent}
                )
    # Straight to confirmed, skipping every step.
    with pytest.raises(DBAPIError, match="cannot move from not_invited to confirmed"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(jump, {"status": "confirmed", "id": client_id})
    # A client cannot invite themselves, and nobody removes a claim.
    with pytest.raises(DBAPIError, match="cannot move from not_invited to invited"):
        async with app_database.actor_transaction(Actor("client", client_id)) as session:
            await session.execute(jump, {"status": "invited", "id": client_id})
    with pytest.raises(DBAPIError, match=r"never removed|permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("DELETE FROM migration_claims WHERE client_id = :id"), {"id": client_id}
            )
    # A used link stays used.
    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    with pytest.raises(DBAPIError, match="used or cancelled once"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE migration_claim_links SET used_at = NULL WHERE id = :id"),
                {"id": link.link_id},
            )


async def test_two_tabs_using_one_link_move_the_claim_once(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    barrier = AsyncBarrier(2)

    async def claim() -> str:
        client = Actor("client", client_id)
        try:
            async with app_database.actor_transaction(client) as session:
                await barrier.wait()
                await claims.record_signed_in(session, client, clock, link)
        except ApplicationError as error:
            return error.code
        return "signed_in"

    # Signing in may be recorded again with the same link, so both tabs succeed, and the claim
    # moves once.
    assert list(await asyncio.gather(claim(), claim())) == ["signed_in", "signed_in"]
    async with app_database.actor_transaction(new_staff()) as session:
        events = await events_for_resource(session, "migration_claim", client_id)
    assert [event.action for event in events].count("migration.claim_signed_in") == 1


async def test_signing_in_again_with_the_same_link_changes_nothing(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    first = await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    await as_client(app_database, client_id, claims.submit_identity, clock)

    again = await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    assert first.status is ClaimStatus.SIGNED_IN
    assert again.status is ClaimStatus.IDENTITY_SUBMITTED


async def test_a_link_that_expires_after_it_was_opened_is_refused_at_use(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    clock.advance(hours=72)

    with pytest.raises(claims.LinkRefused) as refused:
        await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    assert refused.value.problem is LinkProblem.EXPIRED
    async with app_database.actor_transaction(new_staff()) as session:
        view = await claims.claim_for(session, client_id)
    assert view is not None and view.status is ClaimStatus.INVITED


async def test_progress_counts_every_claim(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    async with app_database.actor_transaction(new_staff()) as session:
        before = await claims.claim_progress(session)
    await a_claim(worker_database, clock)
    invited = await a_claim(worker_database, clock)
    await invite(worker_database, clock, invited)
    async with app_database.actor_transaction(new_staff()) as session:
        after = await claims.claim_progress(session)
    assert after.cannot_be_invited == before.cannot_be_invited + 1
    assert after.not_yet_claimed == before.not_yet_claimed + 1
    assert after.invited == before.invited + 1
    # A client sees only their own claim, and no staff figures.
    async with app_database.actor_transaction(Actor("client", invited)) as session:
        mine = await claims.claim_progress(session)
    assert (mine.invited, mine.cannot_be_invited) == (1, 0)


async def test_a_selfie_that_does_not_match_sends_the_client_to_the_full_id_check(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    signed_in = await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    assert signed_in.identity_step is IdentityStep.SELFIE
    await as_client(app_database, client_id, claims.submit_identity, clock)

    async with worker_database.actor_transaction(SYSTEM) as session:
        back = await claims.record_identity_result(session, SYSTEM, clock, client_id, matched=False)
    assert (back.status, back.identity_step) == (ClaimStatus.SIGNED_IN, IdentityStep.FULL_ID_CHECK)
    async with worker_database.actor_transaction(SYSTEM) as session:
        kept = await claims.claim_for(session, client_id)
    assert kept is not None
    assert (kept.status, kept.identity_step) == (ClaimStatus.SIGNED_IN, IdentityStep.FULL_ID_CHECK)

    # The full check failing sends them back again, still to the full check; passing moves on.
    await as_client(app_database, client_id, claims.submit_identity, clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        again = await claims.record_identity_result(
            session, SYSTEM, clock, client_id, matched=False
        )
    assert again.identity_step is IdentityStep.FULL_ID_CHECK
    await as_client(app_database, client_id, claims.submit_identity, clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        done = await claims.record_identity_result(session, SYSTEM, clock, client_id, matched=True)
    assert done.status is ClaimStatus.IDENTITY_CONFIRMED

    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        events = await events_for_resource(session, "migration_claim", client_id)
    assert "migration.claim_sent_to_full_id_check" in {event.action for event in events}


async def test_the_database_changes_the_identity_step_only_for_a_failed_selfie(
    app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock(START)
    client_id = await a_claim(worker_database, clock)
    flip = text("UPDATE migration_claims SET needs_id_check = :value WHERE client_id = :id")
    refused = "only a selfie that did not match"

    # Not on its own, not by the client, and never back to the selfie.
    with pytest.raises(DBAPIError, match=refused):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(flip, {"value": True, "id": client_id})
    link = await open_link(app_database, clock, await invite(worker_database, clock, client_id))
    await as_client(app_database, client_id, claims.record_signed_in, clock, link)
    await as_client(app_database, client_id, claims.submit_identity, clock)
    with pytest.raises(DBAPIError, match=refused):
        async with app_database.actor_transaction(Actor("client", client_id)) as session:
            await session.execute(
                text(
                    "UPDATE migration_claims SET needs_id_check = true, status = 'signed_in' "
                    "WHERE client_id = :id"
                ),
                {"id": client_id},
            )
    async with worker_database.actor_transaction(SYSTEM) as session:
        await claims.record_identity_result(session, SYSTEM, clock, client_id, matched=False)
    with pytest.raises(DBAPIError, match=refused):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(flip, {"value": False, "id": client_id})
