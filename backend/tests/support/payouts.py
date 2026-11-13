"""Payout test helpers: a client ready to be paid (money handed over, an approved destination
past its wait, a funded pot for the provider to pay from) and a scripted provider."""

import asyncio
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest

from app.infrastructure.database import Database
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.modules.kyc import service as kyc
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import EntryDraft, credit, debit
from app.modules.payments.domain.outcomes import (
    SendResult,
    StatusReading,
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.modules.payouts import destinations, jobs, payouts
from app.modules.payouts.domain import posting
from app.modules.payouts.domain.destinations import (
    BankAccount,
    DestinationKind,
    MobileMoneyNumber,
    ProofKind,
)
from app.modules.payouts.domain.payouts import PayoutEvent, PayoutStatus
from app.modules.payouts.domain.provider import DisbursementRequest
from app.modules.payouts.domain.sending import AttemptOutcome
from app.modules.payouts.payouts import PayoutRefused, PayoutView
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.kyc import VERIFIED_NAME, verify
from tests.support.ledger import key, staff, ugx

# The events that take a payout out of the queue. The database allows them only through a
# send attempt, so a test that reports one for the provider records the attempt first.
_SEND_OUTCOMES = {
    PayoutEvent.SEND_ACCEPTED: AttemptOutcome.ACCEPTED,
    PayoutEvent.SEND_CONFIRMED: AttemptOutcome.ACCEPTED,
    PayoutEvent.SEND_REJECTED: AttemptOutcome.REJECTED,
    PayoutEvent.SEND_UNCERTAIN: AttemptOutcome.UNCERTAIN,
}


async def approve_by_two(
    database: Database,
    destination_id: str,
    clock: Clock,
    *,
    proof_name: str = VERIFIED_NAME,
    statement_date: date | None = None,
) -> destinations.DestinationView:
    """One member of staff checks the destination's proof and a different one approves it.

    The client is taken through an identity check first if they have not passed one, because
    the check compares the proof with the verified ID name. Pass a clock at or near the real
    time: the database stamps both steps against its own.
    """
    checker, approver = staff(), staff()
    async with database.actor_transaction(checker) as session:
        client_id = (await destinations.get_destination(session, destination_id)).client_id
        name = await kyc.verified_name(session, client_id)
    if name is None:
        await verify(database, Actor("client", client_id), clock)
    async with database.actor_transaction(checker) as session:
        await destinations.check(
            session,
            checker,
            destination_id,
            proof_name=proof_name,
            statement_date=statement_date,
            reason="Checked",
            clock=clock,
        )
    async with database.actor_transaction(approver) as session:
        return await destinations.approve(
            session, approver, destination_id, reason="Approved", clock=clock
        )


class World:
    """One client with money handed over for paying out, an approved destination past its
    wait, and a funded pot for the provider to pay from."""

    def __init__(self, app: Database, worker: Database, clock: FakeClock) -> None:
        self.app, self.worker, self.clock = app, worker, clock
        self.client = Actor("client", push_id())
        self.holding = ""
        self.handover = f"investment-{push_id()}"
        self.pot = ""
        self.destination_id = ""
        self.pot_before = 0

    @property
    def client_id(self) -> str:
        assert self.client.id is not None
        return self.client.id

    async def build(self, funds: int, *, mobile: bool = True, ending: str | None = None) -> "World":
        """`ending` gives the last two digits of the mobile money number, which a scripted
        provider answers by."""
        digits = f"{secrets.randbelow(10**5):05d}{ending}" if ending else None
        account: MobileMoneyNumber | BankAccount = (
            MobileMoneyNumber(f"25677{digits or f'{secrets.randbelow(10**7):07d}'}")
            if mobile
            else BankAccount("stanbic", str(secrets.randbelow(10**12) + 10**12))
        )
        proof = ProofKind.NETWORK_REGISTERED_NAME if mobile else ProofKind.BANK_LETTER
        async with self.worker.actor_transaction(SYSTEM) as session:
            pot = await ledger.open_account(
                session,
                posting.paid_from_account(
                    account_kind(mobile), "mtn" if mobile else None, Currency.UGX
                ),
                self.clock,
            )
            holding = await ledger.open_account(
                session,
                posting.funding_account(self.client_id, self.handover, Currency.UGX),
                self.clock,
            )
            self.pot, self.holding = pot.id, holding.id
            self.pot_before = (await ledger.get_balance(session, pot.id)).minor
            if funds:
                await ledger.post_entry(
                    session,
                    EntryDraft(
                        entry_type="test.deposit",
                        effective_date=business_date(self.clock.now()),
                        lines=(debit(pot.id, ugx(funds)), credit(holding.id, ugx(funds))),
                        idempotency_key=key(),
                    ),
                    self.clock,
                )
        async with self.app.actor_transaction(self.client) as session:
            view = await destinations.add_destination(
                session, self.client, account, "Sarah Nakato", self.clock
            )
            await destinations.confirm_code(session, self.client, view.id, self.clock)
            await destinations.submit_proof(
                session, self.client, view.id, proof, "doc-1", self.clock
            )
        # An approval is stamped against the database clock, whatever time the test is at.
        approved_at = FakeClock(datetime.now(UTC))
        await approve_by_two(self.app, view.id, approved_at)
        self.destination_id = view.id
        self.clock.current = approved_at.now() + timedelta(hours=24)
        return self

    async def request(self, amount: int, **options: object) -> PayoutView:
        async with self.app.actor_transaction(self.client) as session:
            return await payouts.create_payout(
                session,
                self.client,
                destination_id=self.destination_id,
                amount=ugx(amount),
                client_id=self.client_id,
                source_reference=self.handover,
                request_key=key(),
                clock=self.clock,
                **options,  # type: ignore[arg-type]
            )

    async def refused(self, amount: int, **options: object) -> str:
        with pytest.raises(PayoutRefused) as refusal:
            await self.request(amount, **options)
        return refusal.value.code

    async def worker_event(self, payout_id: str, event: PayoutEvent, **options: str) -> PayoutView:
        async with self.worker.actor_transaction(SYSTEM) as session:
            outcome = _SEND_OUTCOMES.get(event)
            current = await payouts.get_payout(session, payout_id)
            if outcome is not None and current.status is PayoutStatus.QUEUED:
                now = self.clock.now()
                attempt = await jobs.start_attempt(session, payout_id, "test", now)
                await jobs.finish_attempt(session, payout_id, attempt, outcome, now)
            return await payouts.apply_event(
                session, SYSTEM, payout_id, event, self.clock, **options
            )

    async def balance(self, account_id: str) -> int:
        async with self.worker.actor_transaction(SYSTEM) as session:
            return (await ledger.get_balance(session, account_id)).minor

    async def pot_change(self) -> int:
        """How far the pot has moved since this world was built, its own deposit included.
        The pot is shared by every test, as the real wallet is shared by every client."""
        return await self.balance(self.pot) - self.pot_before

    async def pending(self) -> int:
        async with self.worker.actor_transaction(SYSTEM) as session:
            account = await ledger.open_account(
                session, posting.pending_account(self.client_id, Currency.UGX), self.clock
            )
            return (await ledger.get_balance(session, account.id)).minor

    async def owed(self, payout: PayoutView) -> int:
        """Everything Finvest owes this client across the three places it can sit."""
        return (
            await self.balance(self.holding)
            + await self.balance(payout.in_flight_account_id)
            + await self.pending()
        )

    async def no_drift(self) -> None:
        async with self.worker.actor_transaction(SYSTEM) as session:
            assert await ledger.find_balance_drift(session) == []


def account_kind(mobile: bool) -> DestinationKind:
    return DestinationKind.MOBILE_MONEY if mobile else DestinationKind.BANK


class FakeDisbursements:
    """A scripted disbursements provider. It can give every answer a real one can: accepted,
    refused, deferred, a timeout whose outcome is unknown, and a crash mid-call. Answers are
    scripted per payout, so tests sharing a database never read each other's script, and a
    payout with no script is answered as a timeout."""

    name = "fake"

    def __init__(self) -> None:
        self._sends: dict[str, list[SendResult | Exception]] = {}
        self._readings: dict[str, list[StatusReading]] = {}
        self.requests: list[DisbursementRequest] = []
        self.reads: list[str] = []

    def on_send(self, payout_id: str, *results: SendResult | Exception) -> None:
        """Answers to successive sends of this payout. The last one repeats."""
        self._sends[payout_id] = list(results)

    def on_status(self, payout_id: str, *readings: StatusReading) -> None:
        """Answers to successive status reads of this payout. The last one repeats."""
        self._readings[payout_id] = list(readings)

    def sends_for(self, payout_id: str) -> int:
        return sum(1 for request in self.requests if request.request_id == payout_id)

    async def send_disbursement(self, request: DisbursementRequest) -> SendResult:
        self.requests.append(request)
        await asyncio.sleep(0)  # let a racing send run
        script = self._sends.get(request.request_id)
        if not script:
            return Uncertain(UncertainReason.TIMED_OUT)
        result = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(result, Exception):
            raise result
        return result

    async def disbursement_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading:
        self.reads.append(request_id)
        script = self._readings.get(request_id)
        if not script:
            return Unavailable()
        return script.pop(0) if len(script) > 1 else script[0]


def payout_worker(database: Database, provider: FakeDisbursements, clock: Clock) -> Worker:
    return Worker(
        database,
        jobs.PayoutJobs(database, provider, clock).handlers(),
        WorkerConfig(
            lease=timedelta(seconds=30),
            retry=RetryPolicy(base=timedelta(seconds=5), cap=timedelta(seconds=5)),
        ),
        clock,
    )
