"""The yearly tax certificate as a document, against real PostgreSQL: asked for with the June
statements, built from the tax module's figures, stored, listed and emailed without a figure."""

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.infrastructure.sealing import Sealer
from app.modules.documents import pdf, service
from app.modules.documents.domain.statements import Period
from app.modules.documents.jobs import DocumentJobs
from app.modules.documents.service import DocumentKind, DocumentStatus, DocumentView
from app.modules.identity import service as identity_service
from app.modules.identity.service import ClientContact
from app.modules.tax import service as tax
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.integration.test_documents import FakeEmail, closed
from tests.integration.test_tax import LAST_MONTH, TIN, approved_client, running, withhold
from tests.support.clock import FakeClock
from tests.support.investments import at
from tests.support.kyc import SEALER
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

YEAR = tax.tax_year_of(LAST_MONTH)
YEAR_PERIOD = Period(YEAR.first_day, YEAR.last_day)
INSIDE = max(LAST_MONTH, YEAR.first_day)
# The day after the year ends, when the June run builds its certificates.
ISSUED = at(date(YEAR.starts + 1, 7, 2))


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    queue = "tax" + "".join(c for c in push_id().lower() if c.isalnum())
    monkeypatch.setattr(service, "QUEUE", queue)
    yield queue


async def run_jobs(
    worker_database: Database,
    *,
    sealer: Sealer | None = SEALER,
    email: FakeEmail | None = None,
    kinds: tuple[str, ...] | None = None,
    clock: FakeClock = ISSUED,
) -> int:
    jobs = DocumentJobs(
        worker_database,
        clock,
        email=email or FakeEmail(),
        closed_through=closed(YEAR.last_day),
        sealer=sealer,
    )
    handlers = {k: h for k, h in jobs.handlers().items() if kinds is None or k in kinds}
    runner = Worker(worker_database, handlers, WorkerConfig(queue=service.QUEUE), clock)
    done = 0
    while await runner.run_once():
        done += 1
    return done


async def ask_certificate(worker_database: Database, client: Actor) -> DocumentView:
    assert client.id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await service.ask(
            session,
            client_id=client.id,
            kind=DocumentKind.TAX_CERTIFICATE,
            key=YEAR.label,
            period=YEAR_PERIOD,
            now=ISSUED.now(),
        )


async def document(worker_database: Database, document_id: str) -> DocumentView:
    async with worker_database.actor_transaction(SYSTEM) as session:
        found = await service.get_document(session, document_id)
    assert found is not None
    return found


async def test_a_certificate_is_built_from_the_tax_figures_listed_and_downloaded(
    app_database: Database, worker_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await approved_client(app_database, tin=TIN)
    investment = await running(app_database, worker_database, client)
    await withhold(worker_database, investment, INSIDE, 31_164, 4_674)
    drawn: list[tax.Certificate] = []
    original = pdf.tax_certificate_pdf

    def keep(certificate: tax.Certificate, *, reference: str, issued_on: date) -> bytes:
        drawn.append(certificate)
        return original(certificate, reference=reference, issued_on=issued_on)

    monkeypatch.setattr(pdf, "tax_certificate_pdf", keep)

    async def reachable(session: AsyncSession, client_id: str) -> ClientContact:
        return ClientContact("+256772000001", "amara@example.com", True, email_verified=True)

    monkeypatch.setattr(identity_service, "client_contact", reachable)
    asked = await ask_certificate(worker_database, client)
    # Asking again for the same year asks once.
    assert (await ask_certificate(worker_database, client)).id == asked.id
    email = FakeEmail()
    await run_jobs(worker_database, email=email)

    built = await document(worker_database, asked.id)
    assert built.status is DocumentStatus.READY
    assert built.file_name == f"Finvest tax certificate {YEAR.label}.pdf"
    (certificate,) = drawn
    assert certificate.tin == TIN
    assert [(line.investment_id, line.tax) for line in certificate.lines] == [
        (investment.id, ugx(4_674))
    ]
    async with app_database.actor_transaction(client) as session:
        listed = await service.client_documents(session, client)
        file = await service.client_file(session, client, asked.id)
    assert [view.kind for view in listed if view.id == asked.id] == [DocumentKind.TAX_CERTIFICATE]
    assert file.content.startswith(b"%PDF-")
    # The email carries the PDF and no figure.
    (message,) = email.sent
    assert message.subject == f"Your Finvest tax certificate for {YEAR.label}"
    assert [a.file_name for a in message.attachments] == [built.file_name]
    assert "UGX" not in message.text and "UGX" not in message.html


async def test_a_year_with_no_interest_is_empty_and_never_shown(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database)
    asked = await ask_certificate(worker_database, client)
    await run_jobs(worker_database)
    assert (await document(worker_database, asked.id)).status is DocumentStatus.EMPTY
    async with app_database.actor_transaction(client) as session:
        listed = await service.client_documents(session, client)
    assert asked.id not in {view.id for view in listed}


async def test_a_worker_without_the_identity_key_holds_certificates_back(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, tin=TIN)
    investment = await running(app_database, worker_database, client)
    await withhold(worker_database, investment, INSIDE, 1_000, 150)
    asked = await ask_certificate(worker_database, client)
    await run_jobs(worker_database, sealer=None)
    assert (await document(worker_database, asked.id)).status is DocumentStatus.PENDING
    async with worker_database.actor_transaction(SYSTEM) as session:
        job = (
            await session.execute(
                text(
                    "SELECT status, last_error_code FROM jobs "
                    "WHERE idempotency_key = :key AND queue = :queue"
                ),
                {"key": f"document:{asked.id}", "queue": service.QUEUE},
            )
        ).one()
    assert job.last_error_code == "identity_key_missing"


async def test_the_june_run_asks_for_each_clients_certificate_for_the_year(
    app_database: Database, worker_database: Database, own_queue: str
) -> None:
    client = await approved_client(app_database)
    await running(app_database, worker_database, client)
    june = f"{YEAR.last_day:%Y-%m}"
    async with worker_database.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(
                kind=service.MONTHLY_KIND,
                idempotency_key=f"documents.monthly:{june}:start",
                payload={"data": {"month": june, "after": ""}},
                queue=own_queue,
            ),
            now=ISSUED.now(),
        )
    # Only the run itself: building every client's documents is covered elsewhere.
    await run_jobs(worker_database, kinds=(service.MONTHLY_KIND,))
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT kind, idempotency_key, period_start, period_end FROM documents "
                    "WHERE client_id = :client ORDER BY kind"
                ),
                {"client": client.id},
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("monthly_statement", june, YEAR.last_day.replace(day=1), YEAR.last_day),
        ("tax_certificate", YEAR.label, YEAR.first_day, YEAR.last_day),
    ]


async def test_the_database_holds_a_certificate_to_one_tax_year(
    worker_database: Database,
) -> None:
    for start, end, label in (
        (date(2026, 1, 1), date(2026, 12, 31), "2026-27"),
        (date(2026, 7, 1), date(2027, 6, 29), "2026-27"),
        (date(2026, 7, 1), date(2027, 6, 30), "2027-28"),
    ):
        with pytest.raises(DBAPIError, match="ck_documents_tax_year"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await service.ask(
                    session,
                    client_id=push_id(),
                    kind=DocumentKind.TAX_CERTIFICATE,
                    key=label,
                    period=Period(start, end),
                    now=ISSUED.now(),
                )
