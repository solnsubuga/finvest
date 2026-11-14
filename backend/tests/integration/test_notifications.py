"""Email is queued by the API role, sent once by the worker, and its content is then gone."""

import itertools
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.email import (
    EmailDeferred,
    EmailMessage,
    EmailRejected,
    InvalidEmail,
    SendResult,
)
from app.infrastructure.jobs import JobRequest, RetryPolicy, Worker, WorkerConfig, enqueue
from app.infrastructure.sms import (
    DisabledSms,
    InvalidSms,
    SmsMessage,
    SmsResult,
    SmsTransport,
    SmsUncertain,
)
from app.modules.notifications import jobs, service
from app.modules.notifications.domain.templates import InvalidTemplate
from app.modules.notifications.service import PURGE_MESSAGES, queue_message
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.email import RecordingTransport

pytestmark = pytest.mark.integration

_numbers = itertools.count(1)


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> str:
    """Messages queued by other tests wait on the shared queue. These tests use their own."""
    name = f"messages_{secrets.token_hex(8)}"
    monkeypatch.setattr(service, "MESSAGE_QUEUE", name)
    return name


def an_address() -> str:
    return f"person{next(_numbers)}-{push_id()[-6:].encode().hex()}@example.com"


def a_worker(
    database: Database,
    transport: RecordingTransport,
    clock: FakeClock,
    sms: SmsTransport | None = None,
) -> Worker:
    config = WorkerConfig(
        queue=service.MESSAGE_QUEUE,
        worker_id="email-test",
        retry=RetryPolicy(base=timedelta(seconds=10), cap=timedelta(minutes=1)),
    )
    return Worker(
        database, jobs.handlers(database, clock, transport, sms or DisabledSms()), config, clock
    )


async def queue(
    database: Database, clock: FakeClock, address: str, key: str, code: str = "482913"
) -> None:
    async with database.transaction() as session:
        await queue_message(
            session,
            channel="email",
            recipient=address,
            template="sign_in_code",
            variables={"code": code, "minutes": 10},
            idempotency_key=key,
            now=clock.now(),
            deliver_within=timedelta(minutes=10),
        )


async def stored(database: Database, key: str):  # type: ignore[no-untyped-def]
    async with database.transaction() as session:
        return (
            await session.execute(
                text("SELECT * FROM notification_messages WHERE idempotency_key = :key"),
                {"key": key},
            )
        ).one()


async def test_a_queued_email_is_sent_once_and_its_content_cleared(
    app_database: Database, worker_database: Database
) -> None:
    clock, transport, address = FakeClock(), RecordingTransport(), an_address()
    key = f"test:{push_id()}"
    await queue(app_database, clock, address, key)
    # The same decision made twice, by a retried request, queues one email.
    await queue(app_database, clock, address, key)
    worker = a_worker(worker_database, transport, clock)
    await worker.run_once()
    await worker.run_once()

    [message] = transport.to(address)
    assert message.idempotency_key == key
    assert "482913" in message.text
    row = await stored(worker_database, key)
    assert (row.status, row.provider, row.provider_message_id) == ("sent", "recording", "msg-1")
    assert row.variables == {}
    assert row.finished_at == clock.now()


async def test_a_deferred_send_is_retried_with_the_same_key(
    app_database: Database, worker_database: Database
) -> None:
    clock, address = FakeClock(), an_address()
    transport = RecordingTransport(failures=[EmailDeferred("Resend deferred the message (503)")])
    key = f"test:{push_id()}"
    await queue(app_database, clock, address, key)
    worker = a_worker(worker_database, transport, clock)
    await worker.run_once()
    assert transport.to(address) == []
    assert (await stored(worker_database, key)).status == "queued"
    clock.advance(minutes=2)
    await worker.run_once()
    [message] = transport.to(address)
    assert message.idempotency_key == key
    assert (await stored(worker_database, key)).status == "sent"


class BrokenTransport(RecordingTransport):
    """Fails in a way the transport did not sort, after the message may have left."""

    async def send(self, message: EmailMessage) -> SendResult:
        self.sent.append(message)
        raise RuntimeError("connection lost")


async def test_a_send_that_ends_in_an_unsorted_error_is_never_sent_again(
    app_database: Database, worker_database: Database
) -> None:
    clock, transport, address = FakeClock(), BrokenTransport(), an_address()
    key = f"test:{push_id()}"
    await queue(app_database, clock, address, key)
    worker = a_worker(worker_database, transport, clock)
    await worker.run_once()
    clock.advance(minutes=2)
    await worker.run_once()

    assert len(transport.to(address)) == 1
    async with worker_database.transaction() as session:
        failure = await session.scalar(
            text("SELECT last_error_code FROM jobs WHERE queue = :q AND status = 'failed'"),
            {"q": service.MESSAGE_QUEUE},
        )
    assert failure == "uncertain_outcome"


async def test_a_rejected_email_fails_and_keeps_no_content(
    app_database: Database, worker_database: Database
) -> None:
    clock, address = FakeClock(), an_address()
    transport = RecordingTransport(
        failures=[EmailRejected("Resend rejected the message (422 validation_error)")]
    )
    key = f"test:{push_id()}"
    await queue(app_database, clock, address, key)
    worker = a_worker(worker_database, transport, clock)
    await worker.run_once()
    clock.advance(minutes=2)
    await worker.run_once()
    assert transport.to(address) == []
    row = await stored(worker_database, key)
    assert (row.status, row.failure_code, row.variables) == ("failed", "email_rejected", {})
    async with worker_database.transaction() as session:
        job = (
            await session.execute(
                text("SELECT status, last_error_code FROM jobs WHERE idempotency_key = :key"),
                {"key": key},
            )
        ).one()
    assert (job.status, job.last_error_code) == ("failed", "email_rejected")


async def test_a_code_that_is_too_late_is_not_sent(
    app_database: Database, worker_database: Database
) -> None:
    clock, transport, address = FakeClock(), RecordingTransport(), an_address()
    key = f"test:{push_id()}"
    await queue(app_database, clock, address, key)
    clock.advance(minutes=11)
    await a_worker(worker_database, transport, clock).run_once()
    assert transport.to(address) == []


async def test_nothing_is_queued_for_a_bad_address_or_template(app_database: Database) -> None:
    clock = FakeClock()
    with pytest.raises(InvalidEmail):
        await queue(app_database, clock, "not an address", f"test:{push_id()}")
    with pytest.raises(InvalidTemplate):
        async with app_database.transaction() as session:
            await queue_message(
                session,
                channel="email",
                recipient=an_address(),
                template="sign_in_code",
                variables={"code": "1"},
                idempotency_key=f"test:{push_id()}",
                now=clock.now(),
            )


async def test_the_api_can_queue_email_but_never_read_it(app_database: Database) -> None:
    clock, key = FakeClock(), f"test:{push_id()}"
    await queue(app_database, clock, an_address(), key)
    for statement in (
        "SELECT recipient FROM notification_messages",
        "SELECT variables FROM notification_messages",
        "UPDATE notification_messages SET status = 'sent'",
        "DELETE FROM notification_messages",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.transaction() as session:
                await session.execute(text(statement))


async def test_the_job_carries_no_address_and_no_code(
    app_database: Database, worker_database: Database
) -> None:
    clock, address, key = FakeClock(), an_address(), f"test:{push_id()}"
    await queue(app_database, clock, address, key, code="771204")
    async with worker_database.transaction() as session:
        payload = await session.scalar(
            text("SELECT payload::text FROM jobs WHERE idempotency_key = :key"), {"key": key}
        )
    assert address not in payload
    assert "771204" not in payload


async def test_old_content_and_addresses_are_cleared(
    app_database: Database, worker_database: Database
) -> None:
    clock, transport = FakeClock(), RecordingTransport()
    sent_key, stuck_key = f"test:{push_id()}", f"test:{push_id()}"
    worker = a_worker(worker_database, transport, clock)
    await queue(app_database, clock, an_address(), sent_key)
    await worker.run_once()

    async def purge() -> None:
        async with worker_database.transaction() as session:
            await enqueue(
                session,
                JobRequest(
                    kind=PURGE_MESSAGES,
                    idempotency_key=f"purge:{push_id()}",
                    payload={},
                    queue=service.MESSAGE_QUEUE,
                ),
                now=clock.now(),
            )
        await worker.run_once()

    clock.advance(minutes=30)
    # Never sent: its job has expired, so only the purge will clear the code. It was worth
    # sending for ten minutes, and the first purge after that empties it.
    await queue(app_database, clock, an_address(), stuck_key)
    clock.advance(minutes=11)

    await purge()
    stuck = await stored(worker_database, stuck_key)
    assert (stuck.status, stuck.failure_code, stuck.variables) == ("failed", "expired", {})
    assert (await stored(worker_database, sent_key)).recipient is not None
    clock.advance(days=30)
    await purge()
    assert (await stored(worker_database, sent_key)).recipient is None
    assert (await stored(worker_database, stuck_key)).recipient is None


class RecordingSms:
    name = "recording-sms"

    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.sent: list[SmsMessage] = []
        self.failures = failures or []

    async def send(self, message: SmsMessage) -> SmsResult:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(message)
        return SmsResult(provider=self.name, provider_message_id="sms-1")


async def queue_sms(database: Database, clock: FakeClock, phone: str, key: str) -> None:
    async with database.transaction() as session:
        await queue_message(
            session,
            channel="sms",
            recipient=phone,
            template="signup_code",
            variables={"code": "482913", "minutes": 10},
            idempotency_key=key,
            now=clock.now(),
            deliver_within=timedelta(minutes=10),
        )


async def test_a_text_message_is_sent_once(
    app_database: Database, worker_database: Database
) -> None:
    clock, sms, key = FakeClock(), RecordingSms(), f"test:{push_id()}"
    phone = f"+2567{next(_numbers):08d}"
    await queue_sms(app_database, clock, phone, key)
    worker = a_worker(worker_database, RecordingTransport(), clock, sms)
    await worker.run_once()
    await worker.run_once()
    [message] = [m for m in sms.sent if m.to == phone]
    assert "482913" in message.text
    assert len(message.text) <= 160
    row = await stored(worker_database, key)
    assert (row.status, row.channel, row.variables) == ("sent", "sms", {})


async def test_a_text_that_may_have_been_sent_is_never_sent_again(
    app_database: Database, worker_database: Database
) -> None:
    clock, key = FakeClock(), f"test:{push_id()}"
    sms = RecordingSms(failures=[SmsUncertain("The provider did not answer")])
    phone = f"+2567{next(_numbers):08d}"
    await queue_sms(app_database, clock, phone, key)
    worker = a_worker(worker_database, RecordingTransport(), clock, sms)
    await worker.run_once()
    clock.advance(minutes=2)
    await worker.run_once()
    assert [m for m in sms.sent if m.to == phone] == []
    row = await stored(worker_database, key)
    # The row says it may have arrived, which is not the same as refused.
    assert (row.status, row.failure_code, row.variables) == ("failed", "uncertain_outcome", {})
    async with worker_database.transaction() as session:
        code = await session.scalar(
            text("SELECT last_error_code FROM jobs WHERE idempotency_key = :key"), {"key": key}
        )
    assert code == "uncertain_outcome"


async def test_a_bad_number_queues_nothing(app_database: Database) -> None:
    with pytest.raises(InvalidSms):
        await queue_sms(app_database, FakeClock(), "0772123456", f"test:{push_id()}")
