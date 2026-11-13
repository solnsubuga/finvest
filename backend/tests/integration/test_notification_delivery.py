"""A notification goes out by push, SMS and email as the client chose, once, from the worker."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.infrastructure.push import PushFailure, PushMessage, PushResult, PushUncertain
from app.infrastructure.sms import SmsMessage, SmsResult
from app.modules.notifications import contract, devices, jobs, preferences, service
from app.modules.notifications.delivery import DeliveryJobs
from app.modules.notifications.domain.email_events import address_hash
from app.modules.notifications.domain.events import Category, Channel
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.email import RecordingTransport
from tests.support.http import Api, running_api
from tests.support.ledger import client as new_client

pytestmark = pytest.mark.integration

PAID: dict[str, contract.Value] = {
    "amount": Money(150_000, Currency.UGX),
    "method": "MTN Mobile Money",
}


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> str:
    """Notifications raised by other tests wait on the shared queue. These tests use their own."""
    name = f"delivery_{secrets.token_hex(8)}"
    monkeypatch.setattr(service, "MESSAGE_QUEUE", name)
    return name


def a_token() -> str:
    return f"ExponentPushToken[{secrets.token_urlsafe(16)}]"


@dataclass
class Contact:
    phone: str | None
    email: str | None


@dataclass
class Book:
    """Where each test client can be reached. Identity answers this in the real worker."""

    known: dict[str, Contact] = field(default_factory=dict)

    async def __call__(self, session: AsyncSession, client_id: str) -> Contact | None:
        return self.known.get(client_id)


@dataclass
class RecordingPush:
    name: str = "recording"
    sent: list[PushMessage] = field(default_factory=list)
    failures: list[PushFailure] = field(default_factory=list)
    dead: tuple[str, ...] = ()

    async def send(self, message: PushMessage) -> PushResult:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(message)
        return PushResult(self.name, len(message.tokens) - len(self.dead), self.dead)


@dataclass
class RecordingSms:
    name: str = "recording"
    sent: list[SmsMessage] = field(default_factory=list)

    async def send(self, message: SmsMessage) -> SmsResult:
        self.sent.append(message)
        return SmsResult(self.name, f"sms-{len(self.sent)}")


@dataclass
class Rig:
    database: Database
    clock: FakeClock
    push: RecordingPush
    sms: RecordingSms
    email: RecordingTransport
    book: Book

    def worker(self, name: str = "delivery-test") -> Worker:
        config = WorkerConfig(
            queue=service.MESSAGE_QUEUE,
            worker_id=name,
            retry=RetryPolicy(base=timedelta(seconds=10), cap=timedelta(minutes=1)),
        )
        kinds = {
            **jobs.handlers(self.database, self.clock, self.email, self.sms),
            **DeliveryJobs(self.database, self.clock, self.push, self.book).handlers(),
        }
        return Worker(self.database, kinds, config, self.clock)

    async def drain(self) -> None:
        worker = self.worker()
        while await worker.run_once():
            pass


@pytest.fixture
def rig(worker_database: Database) -> Rig:
    return Rig(
        worker_database, FakeClock(), RecordingPush(), RecordingSms(), RecordingTransport(), Book()
    )


async def a_client(
    rig: Rig, app_database: Database, *, token: bool = True, phone: bool = True, email: bool = True
) -> tuple[Actor, str | None]:
    client = new_client()
    assert client.id is not None
    rig.book.known[client.id] = Contact(
        "+256772123456" if phone else None,
        f"{client.id.lower()[-6:]}@example.com" if email else None,
    )
    device = None
    if token:
        device = a_token()
        await register(app_database, client, device, rig.clock)
    return client, device


async def register(database: Database, client: Actor, token: str, clock: FakeClock) -> None:
    async with database.actor_transaction(client) as session:
        await devices.register(session, client, token, "android", clock.now())


async def tell(
    database: Database,
    client: Actor,
    clock: FakeClock,
    event_name: str = "payment_confirmed",
    variables: dict[str, contract.Value] | None = None,
) -> str:
    assert client.id is not None
    async with database.actor_transaction(client) as session:
        told = await contract.notify(
            session,
            event_name=event_name,
            client_id=client.id,
            idempotency_key=push_id(),
            variables=variables or (PAID if event_name == "payment_confirmed" else {}),
            link=contract.Link("deposit", "-Nabc0000000000000D1"),
            now=clock.now(),
        )
    assert told is not None
    return told


async def messages(database: Database, notification_id: str) -> dict[str, str]:
    async with database.transaction() as session:
        rows = (
            await session.execute(
                # Not LIKE: an ID may hold "_", which LIKE reads as any one character, so another
                # notification's messages made in the same millisecond could match.
                text(
                    "SELECT channel, status FROM notification_messages "
                    "WHERE starts_with(idempotency_key, :key)"
                ),
                {"key": f"notice:{notification_id}:"},
            )
        ).all()
    return {row.channel: row.status for row in rows}


async def jobs_of(database: Database, kind: str, key: str) -> list[str]:
    async with database.transaction() as session:
        return list(
            (
                await session.execute(
                    text("SELECT status FROM jobs WHERE kind = :kind AND idempotency_key = :key"),
                    {"kind": kind, "key": key},
                )
            ).scalars()
        )


async def test_a_payment_goes_by_push_and_sms_as_the_defaults_say(
    rig: Rig, app_database: Database
) -> None:
    client, token = await a_client(rig, app_database)
    told = await tell(app_database, client, rig.clock)
    await rig.drain()

    [alert] = rig.push.sent
    assert alert.tokens == (token,)
    assert (alert.title, alert.body) == (
        "Payment received",
        "We received your payment from MTN Mobile Money.",
    )
    assert alert.data == {"notification_id": told, "kind": "deposit", "id": "-Nabc0000000000000D1"}
    [text_message] = rig.sms.sent
    assert text_message.text == "Finvest: we received your payment from MTN Mobile Money."
    assert text_message.idempotency_key == f"notice:{told}:sms"
    # Money is push and SMS by default. Email is the client's to switch on.
    assert rig.email.sent == []
    assert await messages(rig.database, told) == {"sms": "sent"}


async def test_the_client_choice_of_channels_is_followed(rig: Rig, app_database: Database) -> None:
    client, _ = await a_client(rig, app_database)
    async with app_database.actor_transaction(client) as session:
        await preferences.save(
            session, client, {Category.MONEY: frozenset({Channel.EMAIL})}, rig.clock.now()
        )
    told = await tell(app_database, client, rig.clock)
    await rig.drain()

    assert rig.push.sent == []
    assert rig.sms.sent == []
    [mail] = rig.email.sent
    assert mail.subject == "Payment received"
    assert "We received your payment from MTN Mobile Money." in mail.text
    assert "Profile, under Notifications" in mail.text
    assert mail.idempotency_key == f"notice:{told}:email"


async def test_a_statement_is_not_emailed_twice(rig: Rig, app_database: Database) -> None:
    # The documents module emails the statement itself, with the PDF attached.
    client, token = await a_client(rig, app_database)
    async with app_database.actor_transaction(client) as session:
        await preferences.save(
            session,
            client,
            {Category.ACCOUNT: frozenset({Channel.PUSH, Channel.EMAIL})},
            rig.clock.now(),
        )
    told = await tell(
        app_database, client, rig.clock, "statement_ready", {"period": "October 2026"}
    )
    await rig.drain()
    [alert] = rig.push.sent
    assert alert.tokens == (token,)
    assert rig.email.sent == []
    assert await messages(rig.database, told) == {}


async def test_nothing_is_queued_where_the_client_cannot_be_reached(
    rig: Rig, app_database: Database
) -> None:
    client, _ = await a_client(rig, app_database, token=False, phone=False, email=False)
    told = await tell(app_database, client, rig.clock, "password_changed")
    await rig.drain()
    assert (rig.push.sent, rig.sms.sent, rig.email.sent) == ([], [], [])
    assert await messages(rig.database, told) == {}
    # The notification is still in the app, and the fan-out finished.
    assert await jobs_of(rig.database, "notifications.fan_out", f"fan-out:{told}") == ["succeeded"]


async def suppress(app_database: Database, worker_database: Database, address: str) -> None:
    """Record the address as bounced for good, as Resend's webhook would, through the real roles."""
    svix_id = f"msg_{secrets.token_hex(8)}"
    async with app_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO notification_email_events "
                "(svix_id, delivery, email_id, recipients, permanent, received_at) "
                "VALUES (:svix_id, 'bounced', 'e-1', ARRAY[:hash], true, now())"
            ),
            {"svix_id": svix_id, "hash": address_hash(address)},
        )
    async with worker_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO notification_email_suppressions "
                "(address_hash, reason, svix_id, created_at) "
                "VALUES (:hash, 'bounced', :svix_id, now())"
            ),
            {"hash": address_hash(address), "svix_id": svix_id},
        )


async def security_by_push_and_email(rig: Rig, app_database: Database) -> tuple[Actor, str]:
    client, _ = await a_client(rig, app_database)
    async with app_database.actor_transaction(client) as session:
        await preferences.save(
            session,
            client,
            {Category.SECURITY: frozenset({Channel.PUSH, Channel.EMAIL})},
            rig.clock.now(),
        )
    assert client.id is not None
    email = rig.book.known[client.id].email
    assert email is not None
    return client, email


async def test_a_security_alert_to_a_suppressed_address_goes_by_sms(
    rig: Rig, app_database: Database
) -> None:
    client, email = await security_by_push_and_email(rig, app_database)
    await suppress(app_database, rig.database, email)
    told = await tell(app_database, client, rig.clock, "password_changed")
    await rig.drain()

    # The client chose push and email, but email cannot reach them, so a text goes instead.
    assert len(rig.push.sent) == 1
    assert rig.email.sent == []
    [text_message] = rig.sms.sent
    assert text_message.idempotency_key == f"notice:{told}:sms"
    assert await messages(rig.database, told) == {"sms": "sent", "email": "failed"}


async def test_a_security_alert_follows_the_choice_while_email_works(
    rig: Rig, app_database: Database
) -> None:
    client, _ = await security_by_push_and_email(rig, app_database)
    told = await tell(app_database, client, rig.clock, "password_changed")
    await rig.drain()
    assert rig.sms.sent == []
    assert len(rig.email.sent) == 1
    assert await messages(rig.database, told) == {"email": "sent"}


async def test_other_alerts_to_a_suppressed_address_do_not_fall_back_to_sms(
    rig: Rig, app_database: Database
) -> None:
    client, _ = await a_client(rig, app_database)
    assert client.id is not None
    address = rig.book.known[client.id].email
    assert address is not None
    async with app_database.actor_transaction(client) as session:
        await preferences.save(
            session, client, {Category.MONEY: frozenset({Channel.EMAIL})}, rig.clock.now()
        )
    await suppress(app_database, rig.database, address)
    await tell(app_database, client, rig.clock)
    await rig.drain()
    # Only security alerts must reach the client outside the app. The rest wait in the app.
    assert (rig.sms.sent, rig.email.sent) == ([], [])


async def test_a_shared_phone_gets_only_the_alerts_of_whoever_signed_in_last(
    rig: Rig, app_database: Database
) -> None:
    first, token = await a_client(rig, app_database)
    second, _ = await a_client(rig, app_database, token=False)
    assert token is not None
    rig.clock.advance(minutes=1)
    await register(app_database, second, token, rig.clock)

    await tell(app_database, first, rig.clock)
    await rig.drain()
    assert rig.push.sent == []
    await tell(app_database, second, rig.clock)
    await rig.drain()
    [alert] = rig.push.sent
    assert alert.tokens == (token,)
    # Signing in again on the phone hands it back.
    rig.clock.advance(minutes=1)
    await register(app_database, first, token, rig.clock)
    await tell(app_database, first, rig.clock)
    await rig.drain()
    assert len(rig.push.sent) == 2


async def test_a_signed_out_phone_gets_nothing(rig: Rig, app_database: Database) -> None:
    client, token = await a_client(rig, app_database)
    assert token is not None
    async with app_database.actor_transaction(client) as session:
        await devices.remove(session, client, token, rig.clock.now())
    await tell(app_database, client, rig.clock)
    await rig.drain()
    assert rig.push.sent == []


async def test_a_phone_expo_no_longer_knows_is_dropped(rig: Rig, app_database: Database) -> None:
    client, token = await a_client(rig, app_database)
    assert token is not None
    other = a_token()
    rig.clock.advance(seconds=1)
    await register(app_database, client, other, rig.clock)
    rig.push.dead = (token,)
    await tell(app_database, client, rig.clock)
    await rig.drain()
    async with rig.database.transaction() as session:
        rows = (
            await session.execute(
                text("SELECT token, removed_reason FROM push_devices WHERE client_id = :id"),
                {"id": client.id},
            )
        ).all()
    assert {row.token: row.removed_reason for row in rows} == {token: "not_registered", other: None}


async def test_a_push_that_may_have_arrived_is_not_sent_again(
    rig: Rig, app_database: Database
) -> None:
    client, _ = await a_client(rig, app_database)
    rig.push.failures = [PushUncertain("Expo did not answer (ReadTimeout)")]
    told = await tell(app_database, client, rig.clock)
    await rig.drain()
    rig.clock.advance(minutes=5)
    await rig.drain()
    assert rig.push.sent == []
    # Stopped for good, never retried.
    assert await jobs_of(rig.database, "notifications.push", f"push:{told}") == ["failed"]
    # The other channels are not held back by it.
    assert len(rig.sms.sent) == 1


async def test_one_payment_told_twice_at_once_goes_out_once(
    rig: Rig, app_database: Database
) -> None:
    """Two requests raise the same notification in separate transactions at the same moment."""
    client, _ = await a_client(rig, app_database)
    assert client.id is not None
    key = push_id()

    async def raise_it() -> str | None:
        async with app_database.actor_transaction(client) as session:
            return await contract.notify(
                session,
                event_name="payment_confirmed",
                client_id=str(client.id),
                idempotency_key=key,
                variables=PAID,
                now=rig.clock.now(),
            )

    told = [found for found in await asyncio.gather(raise_it(), raise_it()) if found]
    assert len(told) == 1
    # Two workers reach for the work together.
    await asyncio.gather(rig.worker("one").run_once(), rig.worker("two").run_once())
    await rig.drain()
    assert len(rig.push.sent) == 1
    assert len(rig.sms.sent) == 1
    assert await jobs_of(rig.database, "notifications.fan_out", f"fan-out:{told[0]}") == [
        "succeeded"
    ]


async def test_a_client_sees_and_changes_only_their_own_phones(
    rig: Rig, app_database: Database
) -> None:
    owner, token = await a_client(rig, app_database)
    stranger, _ = await a_client(rig, app_database, token=False)
    async with app_database.actor_transaction(stranger) as session:
        seen = (
            await session.execute(
                text("SELECT count(*) FROM push_devices WHERE token = :t"), {"t": token}
            )
        ).scalar_one()
        assert seen == 0
        await devices.remove(session, stranger, str(token), rig.clock.now())
    async with app_database.actor_transaction(owner) as session:
        live = (
            await session.execute(
                text("SELECT removed_at FROM push_devices WHERE token = :t"), {"t": token}
            )
        ).scalar_one()
    assert live is None
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(stranger) as session:
            await session.execute(
                text(
                    "INSERT INTO push_devices (id, client_id, token, platform, registered_at) "
                    "VALUES (:id, :owner, :token, 'ios', now())"
                ),
                {"id": push_id(), "owner": owner.id, "token": a_token()},
            )


async def test_the_worker_cannot_add_a_phone(rig: Rig) -> None:
    with pytest.raises(DBAPIError):
        async with rig.database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO push_devices (id, client_id, token, platform, registered_at) "
                    "VALUES (:id, :client, :token, 'ios', now())"
                ),
                {"id": push_id(), "client": push_id(), "token": a_token()},
            )


async def test_a_client_has_at_most_ten_phones(rig: Rig, app_database: Database) -> None:
    client, _ = await a_client(rig, app_database, token=False)
    for _ in range(devices.MAX_DEVICES):
        await register(app_database, client, a_token(), rig.clock)
    with pytest.raises(ApplicationError) as refused:
        await register(app_database, client, a_token(), rig.clock)
    assert refused.value.code == "conflict"


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        yield running


async def test_the_app_registers_and_gives_up_its_phone(api: Api) -> None:
    client = await api.client()
    token = a_token()
    bad = await api.http.put(
        "/client/push-devices", json={"token": "not-a-token", "platform": "ios"}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["fields"] == [{"field_path": ["token"], "code": "invalid"}]
    added = await api.http.put("/client/push-devices", json={"token": token, "platform": "ios"})
    assert added.status_code == 204
    again = await api.http.put("/client/push-devices", json={"token": token, "platform": "ios"})
    assert again.status_code == 204
    removed = await api.http.post("/client/push-devices/remove", json={"token": token})
    assert removed.status_code == 204
    async with api.database.actor_transaction(client) as session:
        rows = (
            (
                await session.execute(
                    text("SELECT removed_reason FROM push_devices WHERE token = :t"), {"t": token}
                )
            )
            .scalars()
            .all()
        )
    assert rows == ["signed_out"]
    await api.staff()
    staff = await api.http.put("/client/push-devices", json={"token": a_token(), "platform": "ios"})
    assert staff.status_code in (401, 403)
