"""The note that confirms an investment started, against real PostgreSQL: asked for when the
investment starts, built from the client's own view of it, stored, listed and emailed locked
and without a figure."""

from collections.abc import Iterator
from io import BytesIO

import pytest
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.documents import pdf, service
from app.modules.documents.domain.investment_notes import InvestmentNote
from app.modules.documents.events import INVESTMENT_NOTE_KIND
from app.modules.documents.service import DocumentKind, DocumentStatus
from app.modules.identity import service as identity_service
from app.modules.identity.service import ClientContact
from app.modules.investments import holdings
from app.shared.actors import SYSTEM
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.integration.test_documents import LATER, FakeEmail, invested, run_jobs
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    queue = "notes" + "".join(c for c in push_id().lower() if c.isalnum())
    monkeypatch.setattr(service, "QUEUE", queue)
    yield queue


@pytest.fixture(autouse=True)
def reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def contact(session: AsyncSession, client_id: str) -> ClientContact:
        return ClientContact("+256772000001", "amara@example.com", True, email_verified=True)

    monkeypatch.setattr(identity_service, "client_contact", contact)


async def deliver(worker_database: Database, investment_id: str, client_id: str) -> None:
    """As the outbox delivers `investments.started` to the documents consumer. Twice."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        for _ in range(2):
            await enqueue(
                session,
                JobRequest(
                    kind=INVESTMENT_NOTE_KIND,
                    idempotency_key=f"note-test:{investment_id}",
                    payload={"data": {"investment_id": investment_id, "client_id": client_id}},
                    queue=service.QUEUE,
                ),
                now=LATER.now(),
            )


async def test_a_started_investment_gets_one_note_built_listed_and_emailed_locked(
    app_database: Database, worker_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, investment = await invested(app_database, worker_database)
    assert client.id is not None
    # Starting it named the documents consumer on the event.
    async with worker_database.actor_transaction(SYSTEM) as session:
        published = await session.scalar(
            text("SELECT count(*) FROM jobs WHERE kind = :kind AND payload::text LIKE :id"),
            {"kind": INVESTMENT_NOTE_KIND, "id": f"%{investment.id}%"},
        )
    assert published == 1
    drawn: list[InvestmentNote] = []
    original = pdf.investment_note_pdf

    def keep(note: InvestmentNote, **kwargs: object) -> bytes:
        drawn.append(note)
        return original(note, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pdf, "investment_note_pdf", keep)
    await deliver(worker_database, investment.id, client.id)
    email = FakeEmail()
    await run_jobs(worker_database, email=email)

    async with app_database.actor_transaction(client) as session:
        (note,) = await service.client_documents(session, client)
        file = await service.client_file(session, client, note.id)
    assert (note.kind, note.status, note.source_id) == (
        DocumentKind.INVESTMENT_NOTE,
        DocumentStatus.READY,
        investment.id,
    )
    assert note.file_name == f"Finvest investment note IN-{note.id}.pdf"
    assert file.content.startswith(b"%PDF-")
    (figures,) = drawn
    assert figures.amount == investment.amount
    assert figures.term_days == 91 and figures.maturity_date is not None
    assert figures.at_maturity is not None
    assert figures.at_maturity.total == investment.amount + figures.at_maturity.interest
    assert figures.at_maturity.interest > ugx(0)
    # The email carries the locked PDF and no figure.
    (message,) = email.sent
    assert message.subject == f"Your Finvest investment note: {investment.product_name}"
    (attached,) = message.attachments
    assert attached.file_name == note.file_name
    assert PdfReader(BytesIO(attached.content)).is_encrypted
    assert "UGX" not in message.text and "UGX" not in message.html
    # Delivered twice, built and sent once.
    assert await run_jobs(worker_database, email=email) == 0
    assert len(email.sent) == 1


async def test_the_database_holds_a_note_to_its_investment(worker_database: Database) -> None:
    investment_id = push_id()
    for key, source in ((f"started:{push_id()}", investment_id), ("started:", investment_id)):
        with pytest.raises(DBAPIError, match="ck_documents_investment_note"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await service.ask(
                    session,
                    client_id=push_id(),
                    kind=DocumentKind.INVESTMENT_NOTE,
                    key=key,
                    source_id=source,
                    now=LATER.now(),
                )
    with pytest.raises(DBAPIError, match="ck_documents_shape"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await service.ask(
                session,
                client_id=push_id(),
                kind=DocumentKind.INVESTMENT_NOTE,
                key="started:x",
                now=LATER.now(),
            )


async def test_an_investment_undone_before_its_note_is_built_gets_none(
    app_database: Database, worker_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, investment = await invested(app_database, worker_database)
    assert client.id is not None

    # As investments answers once staff have undone it (investment_corrections): clients
    # never see an unwound investment. The corrections tests cover the undo itself.
    async def undone(*args: object, **kwargs: object) -> None:
        raise ApplicationError("not_found")

    monkeypatch.setattr(holdings, "client_investment", undone)
    await deliver(worker_database, investment.id, client.id)
    email = FakeEmail()
    await run_jobs(worker_database, email=email)
    async with worker_database.actor_transaction(SYSTEM) as session:
        status = await session.scalar(
            text("SELECT status FROM documents WHERE kind = 'investment_note' AND source_id = :id"),
            {"id": investment.id},
        )
    assert status == "empty"
    assert email.sent == []
    async with app_database.actor_transaction(client) as session:
        assert await service.client_documents(session, client) == []
