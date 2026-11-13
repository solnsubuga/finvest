"""Resend's delivery events: verified and saved once by the API, applied once by the worker."""

import asyncio
import base64
import json
import secrets
import uuid
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.email.webhooks import WebhookVerifier
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.infrastructure.sms import DisabledSms
from app.modules.notifications import email_delivery, jobs, service
from app.modules.notifications.domain.email_events import address_hash, keyed_hash
from app.modules.notifications.service import queue_message
from app.modules.notifications.settings import EmailHashSettings
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.email import RecordingTransport

pytestmark = pytest.mark.integration

URL = "/webhooks/resend"
VERIFIER = WebhookVerifier(SecretStr("whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()))
# Made for this run. The real key comes only from the environment.
HASH_KEY = secrets.token_bytes(32)
HASH_SETTINGS = EmailHashSettings(
    email_hash_key=SecretStr(base64.urlsafe_b64encode(HASH_KEY).decode())
)


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"email_events_{secrets.token_hex(8)}"
    monkeypatch.setattr(service, "MESSAGE_QUEUE", name)
    return name


class ResendRecording(RecordingTransport):
    """Sends as Resend does, with an id of Resend's shape, so events can find the message."""

    def __init__(self) -> None:
        super().__init__(name="resend")
        self.ids: list[str] = []

    async def send(self, message):  # type: ignore[no-untyped-def]
        result = await super().send(message)
        self.ids.append(str(uuid.uuid4()))
        return type(result)(provider="resend", provider_message_id=self.ids[-1])


def a_worker(
    database: Database,
    clock: FakeClock,
    transport: RecordingTransport,
    key: bytes | None = HASH_KEY,
) -> Worker:
    handlers = {
        **jobs.handlers(database, clock, transport, DisabledSms()),
        **email_delivery.DeliveryEventJobs(database, clock, key).handlers(),
    }
    config = WorkerConfig(
        queue=service.MESSAGE_QUEUE,
        worker_id="email-events-test",
        retry=RetryPolicy(base=timedelta(seconds=10), cap=timedelta(minutes=1)),
    )
    return Worker(database, handlers, config, clock)


async def drain(worker: Worker) -> None:
    while await worker.run_once():
        pass


def an_address() -> str:
    return f"reader-{secrets.token_hex(5)}@example.com"


async def send_one(app_database: Database, worker: Worker, clock: FakeClock, address: str) -> str:
    key = f"test:{push_id()}"
    async with app_database.transaction() as session:
        await queue_message(
            session,
            channel="email",
            recipient=address,
            template="sign_in_code",
            variables={"code": "482913", "minutes": 10},
            idempotency_key=key,
            now=clock.now(),
            deliver_within=timedelta(minutes=10),
        )
    await drain(worker)
    return key


def body(kind: str, email_id: str, address: str, **data: object) -> bytes:
    return json.dumps(
        {
            "type": kind,
            "created_at": "2026-11-13T11:59:58.000Z",
            "data": {"email_id": email_id, "to": [address], "subject": "x", **data},
        }
    ).encode()


def headers(content: bytes, clock: FakeClock, svix_id: str | None = None) -> dict[str, str]:
    svix_id = svix_id or f"msg_{secrets.token_hex(12)}"
    stamp = int(clock.now().timestamp())
    return {
        "svix-id": svix_id,
        "svix-timestamp": str(stamp),
        "svix-signature": VERIFIER.sign(svix_id, stamp, content),
        "content-type": "application/json",
    }


def api(
    test_database: TestDatabase,
    database: Database,
    clock: FakeClock,
    email_hash: EmailHashSettings = HASH_SETTINGS,
) -> httpx.AsyncClient:
    app = create_app(
        test_database.settings("finvest_app"),
        database,
        clock,
        email_hooks=VERIFIER,
        email_hash=email_hash,
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="https://api.finvestug.com")


async def scalar(database: Database, sql: str, **values: object) -> object:
    async with database.transaction() as session:
        return (await session.execute(text(sql), values)).scalar_one_or_none()


async def message_row(database: Database, key: str):  # type: ignore[no-untyped-def]
    async with database.transaction() as session:
        return (
            await session.execute(
                text(
                    "SELECT status, delivery, delivery_at, failure_code, provider_message_id "
                    "FROM notification_messages WHERE idempotency_key = :key"
                ),
                {"key": key},
            )
        ).one()


async def test_a_signed_event_is_recorded_once_and_applied(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, transport, address = FakeClock(), ResendRecording(), an_address()
    worker = a_worker(worker_database, clock, transport)
    key = await send_one(app_database, worker, clock, address)
    [email_id] = transport.ids
    content = body("email.delivered", email_id, address)
    sent = headers(content, clock)
    async with api(test_database, app_database, clock) as http:
        assert (await http.post(URL, content=content, headers=sent)).status_code == 204
        # The router retries an event it thinks was lost. The same svix-id is saved once.
        assert (await http.post(URL, content=content, headers=sent)).status_code == 204
    assert (
        await scalar(
            worker_database,
            "SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :key",
            kind=email_delivery.APPLY_EVENT,
            key=f"email-event:{sent['svix-id']}",
        )
        == 1
    )
    await drain(worker)
    row = await message_row(worker_database, key)
    assert (row.status, row.delivery) == ("sent", "delivered")
    assert row.delivery_at.isoformat().startswith("2026-11-13T11:59:58")
    stored = await scalar(
        worker_database,
        "SELECT outcome FROM notification_email_events WHERE svix_id = :id",
        id=sent["svix-id"],
    )
    assert stored == "applied"


async def test_the_same_event_arriving_twice_at_once_is_saved_once(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock()
    content = body("email.complained", str(uuid.uuid4()), an_address())
    sent = headers(content, clock)
    async with api(test_database, app_database, clock) as http:
        answers = await asyncio.gather(
            *(http.post(URL, content=content, headers=sent) for _ in range(6))
        )
    assert {answer.status_code for answer in answers} == {204}
    for table, column, value in (
        ("notification_email_events", "svix_id", sent["svix-id"]),
        ("jobs", "idempotency_key", f"email-event:{sent['svix-id']}"),
    ):
        found = await scalar(
            worker_database,
            f"SELECT count(*) FROM {table} WHERE {column} = :v",  # noqa: S608
            v=value,
        )
        assert found == 1


async def test_bad_signatures_are_refused_with_401_and_nothing_is_saved(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock()
    content = body("email.bounced", str(uuid.uuid4()), an_address())
    good = headers(content, clock)
    async with api(test_database, app_database, clock) as http:
        for forged in (
            {**good, "svix-signature": "v1,bm9wZQ=="},
            {k: v for k, v in good.items() if k != "svix-signature"},
            {**good, "svix-id": "msg_other"},
            {},
        ):
            answer = await http.post(URL, content=content, headers=forged)
            assert answer.status_code == 401
            assert answer.json()["error"]["code"] == "unauthorized"
        assert (await http.post(URL, content=content + b" ", headers=good)).status_code == 401
        clock.advance(minutes=6)
        assert (await http.post(URL, content=content, headers=good)).status_code == 401
        big = b"x" * (70 * 1024)
        assert (await http.post(URL, content=big, headers=headers(big, clock))).status_code == 413
    assert (
        await scalar(
            worker_database,
            "SELECT count(*) FROM notification_email_events WHERE svix_id = :id",
            id=good["svix-id"],
        )
        == 0
    )


async def test_without_a_secret_the_route_is_not_there(
    test_database: TestDatabase, app_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESEND_HOOK_SECRET", raising=False)
    clock = FakeClock()
    app = create_app(test_database.settings("finvest_app"), app_database, clock)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        content = body("email.delivered", str(uuid.uuid4()), an_address())
        answer = await http.post(URL, content=content, headers=headers(content, clock))
    assert answer.status_code == 404


async def test_ignored_and_unreadable_events(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock()
    opened = body("email.opened", str(uuid.uuid4()), an_address())
    opened_headers = headers(opened, clock)
    junk = b'{"type": "email.bounced", "data": {}}'
    async with api(test_database, app_database, clock) as http:
        assert (await http.post(URL, content=opened, headers=opened_headers)).status_code == 204
        answer = await http.post(URL, content=junk, headers=headers(junk, clock))
        assert answer.status_code == 422
    assert (
        await scalar(
            worker_database,
            "SELECT count(*) FROM notification_email_events WHERE svix_id = :id",
            id=opened_headers["svix-id"],
        )
        == 0
    )


async def test_a_lasting_bounce_suppresses_the_address(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, transport, address = FakeClock(), ResendRecording(), an_address()
    worker = a_worker(worker_database, clock, transport)
    first = await send_one(app_database, worker, clock, address)
    content = body("email.bounced", transport.ids[0], address.upper(), bounce={"type": "Permanent"})
    async with api(test_database, app_database, clock) as http:
        assert (
            await http.post(URL, content=content, headers=headers(content, clock))
        ).status_code == 204
    await drain(worker)
    assert (await message_row(worker_database, first)).delivery == "bounced"
    reason = await scalar(
        worker_database,
        "SELECT reason FROM notification_email_suppressions WHERE address_hash = :hash",
        hash=address_hash(address),
    )
    assert reason == "bounced"
    # The keyed hash is written beside the plain one, worked out from it under the key.
    keyed = keyed_hash(address_hash(address), HASH_KEY)
    assert keyed != address_hash(address)
    assert (
        await scalar(
            worker_database,
            "SELECT address_key FROM notification_email_suppressions WHERE address_hash = :hash",
            hash=address_hash(address),
        )
        == keyed
    )
    assert await scalar(
        worker_database,
        "SELECT recipients_keyed FROM notification_email_events WHERE email_id = :id",
        id=transport.ids[0],
    ) == [keyed]

    # The next email to that address is not sent, and says why.
    second = await send_one(app_database, worker, clock, address)
    assert len(transport.to(address)) == 1
    row = await message_row(worker_database, second)
    assert (row.status, row.failure_code, row.provider_message_id) == (
        "failed",
        "suppressed",
        None,
    )
    # Another address is unaffected.
    other = an_address()
    await send_one(app_database, worker, clock, other)
    assert len(transport.to(other)) == 1


async def test_without_a_key_only_the_plain_hash_is_kept(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    # A developer's machine may run without FINVEST_EMAIL_HASH_KEY. Production may not.
    clock, transport, address = FakeClock(), ResendRecording(), an_address()
    worker = a_worker(worker_database, clock, transport, key=None)
    await send_one(app_database, worker, clock, address)
    content = body("email.complained", transport.ids[0], address)
    async with api(test_database, app_database, clock, EmailHashSettings()) as http:
        assert (
            await http.post(URL, content=content, headers=headers(content, clock))
        ).status_code == 204
    await drain(worker)
    # Suppressed all the same, on the plain hash.
    assert (
        await scalar(
            worker_database,
            "SELECT address_key IS NULL FROM notification_email_suppressions "
            "WHERE address_hash = :hash",
            hash=address_hash(address),
        )
        is True
    )
    assert (
        await scalar(
            worker_database,
            "SELECT recipients_keyed FROM notification_email_events WHERE email_id = :id",
            id=transport.ids[0],
        )
        is None
    )


async def test_a_complaint_suppresses_and_a_passing_bounce_does_not(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, transport = FakeClock(), ResendRecording()
    worker = a_worker(worker_database, clock, transport)
    passing, complaining = an_address(), an_address()
    await send_one(app_database, worker, clock, passing)
    await send_one(app_database, worker, clock, complaining)
    soft = body("email.bounced", transport.ids[0], passing, bounce={"type": "Transient"})
    spam = body("email.complained", transport.ids[1], complaining)
    async with api(test_database, app_database, clock) as http:
        for content in (soft, spam):
            answer = await http.post(URL, content=content, headers=headers(content, clock))
            assert answer.status_code == 204
    await drain(worker)
    suppressed = {
        address: await scalar(
            worker_database,
            "SELECT reason FROM notification_email_suppressions WHERE address_hash = :hash",
            hash=address_hash(address),
        )
        for address in (passing, complaining)
    }
    assert suppressed == {passing: None, complaining: "complained"}


async def test_a_late_event_never_moves_delivery_back(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, transport, address = FakeClock(), ResendRecording(), an_address()
    worker = a_worker(worker_database, clock, transport)
    key = await send_one(app_database, worker, clock, address)
    delivered = body("email.delivered", transport.ids[0], address)
    accepted = body("email.sent", transport.ids[0], address)
    accepted_headers = headers(accepted, clock)
    async with api(test_database, app_database, clock) as http:
        for content, sent in ((delivered, headers(delivered, clock)), (accepted, accepted_headers)):
            assert (await http.post(URL, content=content, headers=sent)).status_code == 204
            await drain(worker)
    assert (await message_row(worker_database, key)).delivery == "delivered"
    outcome = await scalar(
        worker_database,
        "SELECT outcome FROM notification_email_events WHERE svix_id = :id",
        id=accepted_headers["svix-id"],
    )
    assert outcome == "unchanged"


async def test_an_event_for_an_unknown_email_waits_then_is_kept_unmatched(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, transport, address = FakeClock(), ResendRecording(), an_address()
    worker = a_worker(worker_database, clock, transport)
    content = body("email.complained", str(uuid.uuid4()), address)
    sent = headers(content, clock)
    async with api(test_database, app_database, clock) as http:
        assert (await http.post(URL, content=content, headers=sent)).status_code == 204

    async def outcome() -> object:
        return await scalar(
            worker_database,
            "SELECT outcome FROM notification_email_events WHERE svix_id = :id",
            id=sent["svix-id"],
        )

    await drain(worker)
    # Resend may report before the send's result was saved, so the event waits a while.
    assert await outcome() is None
    for _ in range(12):
        clock.advance(minutes=1)
        await drain(worker)
    assert await outcome() == "unmatched"
    # A complaint still protects the address even without a message to attach it to.
    assert (
        await scalar(
            worker_database,
            "SELECT reason FROM notification_email_suppressions WHERE address_hash = :hash",
            hash=address_hash(address),
        )
        == "complained"
    )


async def test_the_api_cannot_read_events_or_suppressions(app_database: Database) -> None:
    # Staff transactions may read the address hashes, for the console's suppression list
    # (migration 0153). Nothing else about an event, and nothing at all without a staff actor.
    with pytest.raises(Exception, match="permission denied"):
        async with app_database.transaction() as session:
            await session.execute(text("SELECT * FROM notification_email_events"))
    for table in ("notification_email_events", "notification_email_suppressions"):
        async with app_database.transaction() as session:
            hidden = await session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        assert hidden == 0
