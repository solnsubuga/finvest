"""Sending investment notes again, against real PostgreSQL: one person asks, a different one
approves, the worker emails each stored note again with its own record, and the database holds
everyone to their part."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.documents import reissues, service
from app.modules.documents.events import INVESTMENT_NOTE_KIND
from app.modules.identity import service as identity_service
from app.modules.identity.service import ClientContact
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.integration.test_documents import FakeEmail, invested, run_jobs
from tests.support.clock import AsyncBarrier
from tests.support.investments import PRESENT, today
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

REASON = "The client lost the email and asked for it again"


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    queue = "reissue" + "".join(c for c in push_id().lower() if c.isalnum())
    monkeypatch.setattr(service, "QUEUE", queue)
    yield queue


@pytest.fixture(autouse=True)
def reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def contact(session: AsyncSession, client_id: str) -> ClientContact:
        return ClientContact("+256772000001", "amara@example.com", True, email_verified=True)

    monkeypatch.setattr(identity_service, "client_contact", contact)


async def noted(
    app_database: Database, worker_database: Database, email: FakeEmail
) -> tuple[Actor, str]:
    """A client whose investment's note was built and emailed today. Returns the note's ID."""
    client, investment = await invested(app_database, worker_database)
    assert client.id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(
                kind=INVESTMENT_NOTE_KIND,
                idempotency_key=f"reissue-test:{investment.id}",
                payload={"data": {"investment_id": investment.id, "client_id": client.id}},
                queue=service.QUEUE,
            ),
            now=PRESENT.now(),
        )
    await run_jobs(worker_database, clock=PRESENT, email=email)
    async with app_database.actor_transaction(client) as session:
        (note,) = await service.client_documents(session, client)
    return client, note.id


async def propose(
    app_database: Database, staff: Actor, client_id: str | None, days: int = 0
) -> reissues.Reissue:
    async with app_database.actor_transaction(staff) as session:
        return await reissues.propose_reissue(
            session,
            staff,
            client_id=client_id,
            issued_from=today() - timedelta(days=days),
            issued_to=today(),
            reason=REASON,
            clock=PRESENT,
        )


async def approve(app_database: Database, staff: Actor, request_id: str) -> reissues.Reissue:
    async with app_database.actor_transaction(staff) as session:
        return await reissues.approve_reissue(session, staff, request_id, PRESENT)


async def test_a_second_person_approves_and_the_same_note_is_emailed_again(
    app_database: Database, worker_database: Database
) -> None:
    email = FakeEmail()
    client, note_id = await noted(app_database, worker_database, email)
    assert client.id is not None
    (first,) = email.sent
    maker, checker = new_staff(), new_staff()
    async with app_database.actor_transaction(maker) as session:
        count = await reissues.count_notes(
            session,
            maker,
            client_id=client.id,
            issued_from=today(),
            issued_to=today(),
            clock=PRESENT,
        )
    assert count == 1
    proposed = await propose(app_database, maker, client.id)
    assert proposed.status == "proposed"
    # Nothing is sent until a second person approves.
    assert await run_jobs(worker_database, clock=PRESENT, email=email) == 0

    approved = await approve(app_database, checker, proposed.id)
    assert approved.status == "approved" and approved.job_id is not None
    await run_jobs(worker_database, clock=PRESENT, email=email)

    # The same stored note went again, under its own key, and the first email's record stands.
    first_copy, again = email.sent
    assert first_copy is first
    assert again.idempotency_key.startswith("document-copy:")
    assert again.subject == first.subject
    assert [a.file_name for a in again.attachments] == [a.file_name for a in first.attachments]
    async with worker_database.actor_transaction(SYSTEM) as session:
        emails = await session.execute(
            text("SELECT status FROM document_emails WHERE document_id = :id"), {"id": note_id}
        )
        assert emails.scalars().all() == ["sent"]
    async with app_database.actor_transaction(checker) as session:
        done = await reissues.get_reissue(session, checker, proposed.id)
        counts = await reissues.copy_counts(session, checker, [proposed.id])
    assert (done.status, done.sent_count) == ("done", 1)
    assert counts[proposed.id] == reissues.CopyCounts(queued=0, sent=1, skipped=0, failed=0)
    # Running everything again sends nothing more.
    assert await run_jobs(worker_database, clock=PRESENT, email=email) == 0
    assert len(email.sent) == 2


async def test_every_clients_notes_of_a_day_are_sent_once_each(
    app_database: Database, worker_database: Database
) -> None:
    email = FakeEmail()
    _, note_id = await noted(app_database, worker_database, email)
    proposed = await propose(app_database, new_staff(), None)
    await approve(app_database, new_staff(), proposed.id)
    # Run the page twice, as a retried job would: one copy of each note.
    async with worker_database.actor_transaction(SYSTEM) as session:
        copies, _ = await reissues.queue_page(session, proposed.id, "", PRESENT.now())
        again, _ = await reissues.queue_page(session, proposed.id, "", PRESENT.now())
        mine = await session.scalar(
            text(
                "SELECT count(*) FROM document_email_copies "
                "WHERE reissue_id = :id AND document_id = :document"
            ),
            {"id": proposed.id, "document": note_id},
        )
    assert copies and again == [] and mine == 1


async def test_only_a_second_person_decides_and_only_the_asker_withdraws(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await noted(app_database, worker_database, FakeEmail())
    assert client.id is not None
    maker, other = new_staff(), new_staff()
    proposed = await propose(app_database, maker, client.id)
    with pytest.raises(ApplicationError) as refused:
        await approve(app_database, maker, proposed.id)
    assert refused.value.code == "forbidden"
    async with app_database.actor_transaction(other) as session:
        with pytest.raises(ApplicationError) as refused:
            await reissues.withdraw_reissue(session, other, proposed.id, PRESENT)
    assert refused.value.code == "forbidden"
    async with app_database.actor_transaction(other) as session:
        with pytest.raises(ApplicationError) as refused:
            await reissues.decline_reissue(session, other, proposed.id, "  ", PRESENT)
    assert refused.value.code == "validation_failed"
    async with app_database.actor_transaction(other) as session:
        declined = await reissues.decline_reissue(
            session, other, proposed.id, "The client already has it in the app", PRESENT
        )
    assert declined.status == "declined"
    with pytest.raises(ApplicationError) as refused:
        await approve(app_database, new_staff(), proposed.id)
    assert refused.value.code == "conflict"

    second = await propose(app_database, maker, client.id)
    async with app_database.actor_transaction(maker) as session:
        withdrawn = await reissues.withdraw_reissue(session, maker, second.id, PRESENT)
    assert withdrawn.status == "withdrawn"


@pytest.mark.parametrize(
    ("days_back", "days_forward", "field", "code"),
    [
        (0, 1, "issued_to", "not_yet"),
        (31, 0, "issued_to", "too_long"),
    ],
)
async def test_the_days_are_checked(
    app_database: Database, days_back: int, days_forward: int, field: str, code: str
) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as refused:
            await reissues.propose_reissue(
                session,
                staff,
                client_id=None,
                issued_from=today() - timedelta(days=days_back),
                issued_to=today() + timedelta(days=days_forward),
                reason=REASON,
                clock=PRESENT,
            )
    (problem,) = refused.value.fields
    assert (problem.field_path, problem.code) == ([field], code)


async def test_a_request_that_would_send_nothing_is_refused(app_database: Database) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as refused:
            await reissues.propose_reissue(
                session,
                staff,
                client_id=push_id(),
                issued_from=today(),
                issued_to=today(),
                reason=REASON,
                clock=PRESENT,
            )
    (problem,) = refused.value.fields
    assert (problem.field_path, problem.code) == (["issued_from"], "no_notes")


async def test_two_approvals_at_once_queue_one_sending(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await noted(app_database, worker_database, FakeEmail())
    proposed = await propose(app_database, new_staff(), client.id)
    barrier = AsyncBarrier(2)

    async def one() -> str:
        staff = new_staff()
        async with app_database.actor_transaction(staff) as session:
            await barrier.wait()
            try:
                await reissues.approve_reissue(session, staff, proposed.id, PRESENT)
            except ApplicationError as error:
                return error.code
            return "approved"

    assert sorted(await asyncio.gather(one(), one())) == ["approved", "conflict"]
    async with worker_database.actor_transaction(SYSTEM) as session:
        jobs = await session.scalar(
            text("SELECT count(*) FROM jobs WHERE idempotency_key = :key"),
            {"key": f"note-reissue:{proposed.id}"},
        )
    assert jobs == 1


async def test_the_database_holds_everyone_to_their_part(
    app_database: Database, worker_database: Database
) -> None:
    client, note_id = await noted(app_database, worker_database, FakeEmail())
    assert client.id is not None
    maker = new_staff()
    proposed = await propose(app_database, maker, client.id)

    # The proposer cannot approve it, whatever the code says.
    with pytest.raises(DBAPIError, match="second_person"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE note_reissues SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), job_id = :job WHERE id = :id"
                ),
                {"me": maker.id, "job": push_id(), "id": proposed.id},
            )
    # The worker sends nothing for a request nobody approved.
    with pytest.raises(DBAPIError, match="approved reissue"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO document_email_copies "
                    "(id, reissue_id, document_id, client_id, queued_at) "
                    "VALUES (:id, :reissue, :document, :client, now())"
                ),
                {
                    "id": push_id(),
                    "reissue": proposed.id,
                    "document": note_id,
                    "client": client.id,
                },
            )
    # Staff never write a copy themselves.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "INSERT INTO document_email_copies "
                    "(id, reissue_id, document_id, client_id, queued_at) "
                    "VALUES (:id, :reissue, :document, :client, now())"
                ),
                {
                    "id": push_id(),
                    "reissue": proposed.id,
                    "document": note_id,
                    "client": client.id,
                },
            )
    # Only the worker finishes a request, and nobody deletes one.
    approved = await approve(app_database, new_staff(), proposed.id)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE note_reissues SET status = 'done', sent_count = 0, "
                    "finished_at = now() WHERE id = :id"
                ),
                {"id": approved.id},
            )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text("DELETE FROM note_reissues WHERE id = :id"), {"id": approved.id}
            )
