"""A scripted collections provider and helpers for deposit tests.

The fake can give every answer a real provider can: success, rejection, a deferral, a timeout
whose outcome is unknown, and a crash mid-call. Answers are scripted per payer number, and each
test uses a fresh number, so tests sharing a database never read each other's script.
"""

import secrets
from collections.abc import Mapping
from datetime import timedelta

from app.infrastructure.database import Database
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.modules.payments import service
from app.modules.payments.domain.deposits import Network, Purpose
from app.modules.payments.domain.outcomes import (
    SendResult,
    StatusReading,
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.modules.payments.domain.provider import (
    CollectionRequest,
    InvalidNotice,
    ProviderNotice,
)
from app.modules.payments.jobs import DepositJobs
from app.modules.payments.service import Deposit, DepositRequest
from app.shared.actors import SYSTEM, Actor
from app.shared.clock import Clock
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

PROVIDER = "fake"
LEASE = timedelta(seconds=30)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def phone() -> str:
    return "+2567" + "".join(secrets.choice("0123456789") for _ in range(8))


def client() -> Actor:
    return Actor("client", push_id())


class FakeCollections:
    name = PROVIDER

    def __init__(self) -> None:
        self._sends: dict[str, list[SendResult | Exception]] = {}
        self._readings: dict[str, list[StatusReading]] = {}
        self._payer: dict[str, str] = {}
        self.requests: list[CollectionRequest] = []
        self.reads: list[str] = []

    def on_send(self, msisdn: str, *results: SendResult | Exception) -> None:
        """Answers to successive requests for this payer. The last one repeats."""
        self._sends[msisdn] = list(results)

    def on_status(self, msisdn: str, *readings: StatusReading) -> None:
        """Answers to successive status reads for this payer. The last one repeats."""
        self._readings[msisdn] = list(readings)

    def sends_for(self, deposit: Deposit) -> int:
        return sum(1 for request in self.requests if request.request_id == deposit.id)

    def reads_for(self, deposit: Deposit) -> int:
        return self.reads.count(deposit.id)

    async def request_collection(self, request: CollectionRequest) -> SendResult:
        self.requests.append(request)
        self._payer[request.request_id] = request.msisdn
        script = self._sends.get(request.msisdn)
        if not script:
            return Uncertain(UncertainReason.TIMED_OUT)
        result = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(result, Exception):
            raise result
        return result

    async def collection_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading:
        self.reads.append(request_id)
        script = self._readings.get(self._payer.get(request_id, ""))
        if not script:
            return Unavailable()
        return script.pop(0) if len(script) > 1 else script[0]

    def read_notice(self, headers: Mapping[str, str], body: bytes) -> ProviderNotice:
        raise InvalidNotice("the fake sends no callbacks")


def deposit_worker(database: Database, provider: FakeCollections, clock: Clock) -> Worker:
    return Worker(
        database,
        DepositJobs(database, provider, clock).handlers(),
        WorkerConfig(
            lease=LEASE, retry=RetryPolicy(base=timedelta(seconds=5), cap=timedelta(seconds=5))
        ),
        clock,
    )


async def drain(worker: Worker) -> None:
    """Run every job that is due now, including ones those jobs queue for now."""
    while await worker.run_once():
        pass


async def start_deposit(
    database: Database,
    clock: Clock,
    *,
    actor: Actor | None = None,
    msisdn: str | None = None,
    amount: int = 50_000,
    key: str | None = None,
    purpose_id: str | None = None,
    provider: str = PROVIDER,
) -> Deposit:
    actor = actor or client()
    async with database.actor_transaction(actor) as session:
        return await service.request_deposit(
            session,
            actor,
            DepositRequest(
                purpose=Purpose.INVESTMENT,
                purpose_id=purpose_id or push_id(),
                network=Network.MTN,
                amount=ugx(amount),
                phone=msisdn or phone(),
            ),
            idempotency_key=key or f"key-{push_id()}",
            provider=provider,
            clock=clock,
        )


async def reload(database: Database, deposit: Deposit) -> Deposit:
    """The deposit as the worker sees it."""
    async with database.actor_transaction(SYSTEM) as session:
        return await service.get_deposit(session, deposit.id)
