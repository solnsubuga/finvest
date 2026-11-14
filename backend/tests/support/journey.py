"""The whole system in one process, for the end-to-end journeys in tests/journeys/.

A `World` is the API application, the worker with every handler the real worker registers, and
the scheduler's schedules, all on one database (the journeys' own) and one fake clock. Only
the outside world is replaced:

- ioTec collections by the sandbox provider, the same stand-in development and staging use,
  wrapped so a journey can read back what the "provider" confirmed;
- Resend by an email transport that records each message, and the SMS provider likewise;
- file storage by memory.

The API is called over HTTP (in process, through ASGI). Steps that have no route yet are
called through their module's service functions, as the role and actor that would call them:
a client or member of staff on the API's connection, the system on the worker's.
"""

import base64
import itertools
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app import scheduler_main, worker_main
from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.email import EmailMessage
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.infrastructure.jobs.scheduler import Schedule, instance_key
from app.infrastructure.settings import Settings
from app.infrastructure.sms import SmsMessage, SmsResult
from app.infrastructure.storage import MemoryStorage
from app.modules.identity.api import COOKIES
from app.modules.identity.domain import totp
from app.modules.identity.hashing import PasswordHasher
from app.modules.payments.domain.outcomes import Confirmed, SendResult, StatusReading
from app.modules.payments.domain.provider import CollectionRequest, ProviderNotice
from app.modules.payments.providers.sandbox import SandboxCollections
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.email import RecordingTransport

CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
_addresses = itertools.count(1)


class RecordingSms:
    name = "recording-sms"

    def __init__(self) -> None:
        self.sent: list[SmsMessage] = []

    async def send(self, message: SmsMessage) -> SmsResult:
        self.sent.append(message)
        return SmsResult(provider=self.name, provider_message_id=f"sms-{len(self.sent)}")

    def to(self, phone: str) -> list[str]:
        return [message.text for message in self.sent if message.to == phone]


@dataclass(frozen=True)
class Collected:
    """A collection the stand-in ioTec confirmed: what its wallet statement would show."""

    request_id: str
    msisdn: str
    reference: str
    minor: int
    confirmed_at: datetime


class RecordingCollections:
    """The sandbox ioTec, unchanged, with a note of every collection it confirmed.

    The API and the worker share one instance, as they would share one ioTec account. It goes
    by ioTec's name, so the money it confirms reaches `iotec.wallet`, the account the day
    close compares with ioTec's statement. (The plain sandbox books to `sandbox.wallet`, so
    pretend money never mixes with real money in staging.)
    """

    def __init__(self, clock: FakeClock, confirmed: dict[str, Collected] | None = None) -> None:
        self._sandbox = SandboxCollections(clock)
        self._clock = clock
        self.name = "iotec"
        # Shared across journeys when given, as one ioTec account serves every client.
        self.confirmed: dict[str, Collected] = {} if confirmed is None else confirmed
        self._payers: dict[str, str] = {}

    async def request_collection(self, request: CollectionRequest) -> SendResult:
        self._payers[request.request_id] = request.msisdn
        return await self._sandbox.request_collection(request)

    async def collection_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading:
        reading = await self._sandbox.collection_status(
            request_id=request_id, provider_reference=provider_reference
        )
        if isinstance(reading, Confirmed) and request_id not in self.confirmed:
            self.confirmed[request_id] = Collected(
                request_id,
                self._payers.get(request_id, ""),
                reading.provider_reference,
                reading.amount.minor,
                self._clock.now(),
            )
        return reading

    def read_notice(self, headers: Mapping[str, str], body: bytes) -> ProviderNotice:
        return self._sandbox.read_notice(headers, body)


class _Email:
    def __init__(self, transport: RecordingTransport) -> None:
        self._transport = transport

    def transport(self) -> RecordingTransport:
        return self._transport


class _Sms:
    def __init__(self, transport: RecordingSms) -> None:
        self._transport = transport

    def transport(self) -> RecordingSms:
        return self._transport


def journey_settings(test_database: TestDatabase, role: str) -> Settings:
    return Settings(
        **{
            **test_database.settings(role).model_dump(),
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
            "client_passkey_site": "finvestug.com",
            "staff_passkey_site": "admin.finvestug.com",
            # Made up for this run. A real key lives only in the API's environment.
            "authenticator_key": secrets.token_urlsafe(32),
        }
    )


@dataclass
class Person:
    """Someone signed in on their own HTTP connection."""

    actor: Actor
    http: httpx.AsyncClient
    phone: str | None = None
    email: str | None = None
    authenticator: bytes | None = None

    @property
    def id(self) -> str:
        assert self.actor.id is not None
        return self.actor.id


@dataclass
class World:
    app: object
    clock: FakeClock
    api_database: Database
    worker_database: Database
    collections: RecordingCollections
    email: RecordingTransport
    sms: RecordingSms
    storage: MemoryStorage
    worker: Worker
    schedules: dict[str, Schedule]
    _open: list[httpx.AsyncClient] = field(default_factory=list)

    @property
    def identity(self):  # type: ignore[no-untyped-def]
        return self.app.state.identity  # type: ignore[attr-defined]

    def http(self, origin: str | None = None) -> httpx.AsyncClient:
        """A new browser or phone: its own cookies, from its own address."""
        number = next(_addresses)
        headers = {
            "X-Forwarded-For": f"10.{number // 65536 % 256}.{number // 256 % 256}.{number % 256}"
        }
        if origin:
            headers["Origin"] = origin
        transport = httpx.ASGITransport(
            app=self.app,  # type: ignore[arg-type]
            raise_app_exceptions=False,
            client=("127.0.0.1", 1),
        )
        client = httpx.AsyncClient(transport=transport, base_url="https://api", headers=headers)
        self._open.append(client)
        return client

    async def close(self) -> None:
        for client in self._open:
            await client.aclose()

    # The worker and the scheduler --------------------------------------------------------------

    async def drain(self) -> int:
        """Run every job that is due now, and the ones they queue for now."""
        ran = 0
        while count := await self.worker.run_once():
            ran += count
        return ran

    async def settle(self, *, within: timedelta = timedelta(minutes=5)) -> None:
        """Let time pass in small steps, running the worker at each, as the real one would.

        Retries and status reads are queued a few seconds ahead, so a single drain would leave
        them waiting.
        """
        end = self.clock.now() + within
        while self.clock.now() < end:
            await self.drain()
            self.clock.advance(seconds=5)
        await self.drain()

    async def fire(self, schedule_name: str, fire_time: datetime) -> None:
        """What the scheduler does when `schedule_name` comes due at `fire_time`: queue one job
        with the scheduler's own key and payload. The clock moves to the fire time first."""
        schedule = self.schedules[schedule_name]
        if self.clock.now() < fire_time:
            self.clock.current = fire_time
        async with self.worker_database.transaction() as session:
            await enqueue(
                session,
                JobRequest(
                    kind=schedule.kind,
                    queue=schedule.queue,
                    idempotency_key=instance_key(schedule.name, fire_time),
                    payload={
                        "schedule": schedule.name,
                        "scheduled_for": fire_time.astimezone(UTC).isoformat(),
                        "data": dict(schedule.payload),
                    },
                    max_attempts=schedule.max_attempts,
                    max_age=schedule.max_age,
                ),
                now=self.clock.now(),
            )
        await self.drain()

    async def failed_jobs(self, since: datetime) -> list[tuple[str, str | None]]:
        """Jobs that stopped for good since `since`: kind and error code."""
        async with self.worker_database.transaction() as session:
            rows = await session.execute(
                text(
                    "SELECT kind, last_error_code FROM jobs WHERE status = 'failed' "
                    "AND updated_at >= :since ORDER BY kind"
                ),
                {"since": since},
            )
            return [(row.kind, row.last_error_code) for row in rows]

    # People ------------------------------------------------------------------------------------

    def texted_code(self, phone: str) -> str:
        """The six digits in the last text sent to `phone`."""
        texts = self.sms.to(phone)
        assert texts, f"no text was sent to {phone[:6]}…"
        words = [word.strip(".,:") for word in texts[-1].split()]
        return next(word for word in words if word.isdigit() and len(word) == 6)

    def emailed(self, address: str) -> list[EmailMessage]:
        return self.email.to(address)

    async def staff(self, role: str = "admin", name: str = "Grace Auma") -> Person:
        """A member of staff, active and signed in to the console in a browser of their own.

        Staff enrolment is proven in the integration tests; here a journey only needs a
        signed-in person with a role.
        """
        email = f"staff-{secrets.token_hex(6)}@finvestug.com"
        staff_id = await self.identity.invite_staff(
            email=email,
            name=name,
            role=role,
            created_by=None,
        )
        async with self.api_database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                    "password_changed_at = created_at WHERE id = :id"
                ),
                {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
            )
        actor = Actor("staff", staff_id)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="cookie")
        http = self.http(STAFF_ORIGIN)
        http.cookies.set(COOKIES["staff"], issued.token)
        return Person(actor, http, email=email)


def authenticator_code(world: World, secret: bytes) -> str:
    return totp.code_at(secret, totp.step_at(world.clock.now()))


def decode_secret(shown: str) -> bytes:
    return base64.b32decode(shown + "=" * (-len(shown) % 8))


def a_phone() -> str:
    """A Ugandan mobile number the sandbox approves: it never ends in 01 to 06."""
    while True:
        number = "+2567" + "".join(secrets.choice("0123456789") for _ in range(8))
        if not "01" <= number[-2:] <= "06":
            return number


@asynccontextmanager
async def running_world(
    test_database: TestDatabase,
    api_database: Database,
    worker_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    collected: dict[str, Collected] | None = None,
) -> AsyncIterator[World]:
    # Real time: the database refuses an investment that is not created now.
    clock = FakeClock(datetime.now(UTC))
    collections = RecordingCollections(clock, collected)
    email, sms, storage = RecordingTransport(), RecordingSms(), MemoryStorage()
    app = create_app(
        journey_settings(test_database, "finvest_app"),
        api_database,
        clock,
        HASHER,
        collections=collections,
        storage=storage,
        # The journeys answer lookups as the worker would, so they run with names on.
        payout_names=True,
    )
    # The real worker's handlers, built by the real worker's code. Only the providers it
    # would read from its environment are swapped for the stand-ins.
    monkeypatch.setenv("FINVEST_PAYMENTS_PROVIDER", "sandbox")
    monkeypatch.setattr(worker_main, "EmailSettings", lambda: _Email(email))
    monkeypatch.setattr(worker_main, "SmsSettings", lambda: _Sms(sms))
    monkeypatch.setattr(worker_main, "collections_provider", lambda *_: collections)
    worker = Worker(
        worker_database,
        worker_main.handlers(
            worker_database, clock, journey_settings(test_database, "finvest_worker")
        ),
        WorkerConfig(lease=timedelta(minutes=5), concurrency=4),
        clock,
    )
    schedules = {schedule.name: schedule for schedule in scheduler_main.schedules()}
    world = World(
        app,
        clock,
        api_database,
        worker_database,
        collections,
        email,
        sms,
        storage,
        worker,
        schedules,
    )
    try:
        yield world
    finally:
        await world.close()
