"""Mobile money deposits against real PostgreSQL, the real job queue and the real ledger.

The provider is a scripted fake that can accept, refuse, defer, time out and crash. Time is a
fake clock. Races use separate connection pools.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, enqueue
from app.infrastructure.jobs import queue as job_queue
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.payments import jobs as deposit_jobs
from app.modules.payments import service
from app.modules.payments.domain.deposits import (
    APPROVAL_WINDOW,
    SENDING_GRACE,
    TRANSITIONS,
    UNCERTAIN_READ_WINDOW,
    UNSENT_EXPIRY,
    DepositStatus,
    Move,
    Network,
    Purpose,
)
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    SendResult,
    StatusReading,
    Uncertain,
    UncertainReason,
)
from app.modules.payments.domain.provider import CollectionRequest
from app.modules.payments.jobs import DepositJobs
from app.modules.payments.providers.sandbox import (
    PROMPT_TIME,
    SIGNATURE_HEADER,
    SandboxCollections,
)
from app.modules.payments.service import Deposit, DepositRequest
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import staff as new_staff
from tests.support.payments import (
    LEASE,
    PROVIDER,
    FakeCollections,
    client,
    deposit_worker,
    drain,
    phone,
    reload,
    start_deposit,
    ugx,
)

pytestmark = pytest.mark.integration

S = DepositStatus
POLL = timedelta(seconds=3)
RETRY = timedelta(seconds=5)


@pytest.fixture
def provider() -> FakeCollections:
    return FakeCollections()


@pytest.fixture
def worker(worker_database: Database, provider: FakeCollections, fake_clock: FakeClock) -> Worker:
    return deposit_worker(worker_database, provider, fake_clock)


@pytest.fixture
async def pools(test_database: TestDatabase) -> AsyncIterator[dict[str, list[Database]]]:
    """Two more pools per role, for races between independent transactions."""
    made = {
        role: [Database(test_database.settings(role)) for _ in range(2)]
        for role in ("finvest_app", "finvest_worker")
    }
    yield made
    for databases in made.values():
        for database in databases:
            await database.close()


def reference() -> str:
    return f"ref-{push_id()}"


async def awaiting_start(database: Database, deposit: Deposit) -> int:
    """What the ledger says this client has paid in for this investment."""
    async with database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            AccountSpec(
                "client.awaiting_start",
                AccountKind.LIABILITY,
                Currency.UGX,
                "Paid in, awaiting start",
                owner_client_id=deposit.client_id,
                reference=deposit.purpose_id,
            ),
            FakeClock(),
        )
        return (await ledger.get_balance(session, account.id)).minor


async def count(database: Database, sql: str, **parameters: object) -> int:
    async with database.actor_transaction(SYSTEM) as session:
        return int(await session.scalar(text(sql), parameters) or 0)


async def entries(database: Database, deposit: Deposit) -> int:
    return await count(
        database,
        "SELECT count(*) FROM ledger_journal_entries "
        "WHERE source_type = 'payment.deposit' AND source_id = :id",
        id=deposit.id,
    )


async def history(database: Database, deposit: Deposit) -> list[tuple[str, str | None]]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT to_status, code FROM payment_deposit_events "
                "WHERE deposit_id = :id ORDER BY id"
            ),
            {"id": deposit.id},
        )
        return [(row.to_status, row.code) for row in rows]


async def sweep(worker: Worker, database: Database, clock: FakeClock) -> None:
    async with database.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(kind=service.SWEEP_KIND, idempotency_key=f"sweep-{push_id()}", payload={}),
            now=clock.now(),
        )
    await drain(worker)


def sweep_only(database: Database, provider: FakeCollections, clock: FakeClock) -> Worker:
    jobs = DepositJobs(database, provider, clock)
    return Worker(database, {service.SWEEP_KIND: jobs.sweep}, clock=clock)


async def awaiting_deposit(
    app_database: Database, provider: FakeCollections, worker: Worker, clock: FakeClock
) -> tuple[Deposit, str, str]:
    """A deposit whose payer has the prompt. Returns it, the payer number and the reference."""
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Accepted(ref))
    provider.on_status(msisdn, Pending(ref))
    deposit = await start_deposit(app_database, clock, msisdn=msisdn)
    await drain(worker)
    return deposit, msisdn, ref


# The whole path


async def test_a_deposit_is_sent_approved_confirmed_and_posted_once(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Accepted(ref))
    provider.on_status(msisdn, Pending(ref), Confirmed(ref, ugx(50_000)))

    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    assert deposit.status is S.CREATED
    # Nothing is sent by the request itself: the provider is called only by the worker.
    assert provider.requests == []

    await drain(worker)
    waiting = await reload(worker_database, deposit)
    assert (waiting.status, waiting.provider_reference) == (S.AWAITING_APPROVAL, ref)
    sent = provider.requests[-1]
    assert (sent.request_id, sent.msisdn, sent.amount, sent.network) == (
        deposit.id,
        msisdn,
        ugx(50_000),
        Network.MTN,
    )
    assert await awaiting_start(worker_database, deposit) == 0

    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)  # the payer has not answered yet
    assert (await reload(worker_database, deposit)).status is S.AWAITING_APPROVAL

    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)
    confirmed = await reload(worker_database, deposit)
    assert confirmed.status is S.CONFIRMED
    assert confirmed.settled_at == fake_clock.now()
    assert confirmed.ledger_entry_id is not None
    assert await awaiting_start(worker_database, deposit) == 50_000
    assert await entries(worker_database, deposit) == 1
    assert provider.sends_for(deposit) == 1
    assert await history(worker_database, deposit) == [
        ("created", None),
        ("sending", None),
        ("awaiting_approval", None),
        ("confirmed", None),
    ]
    assert (
        await count(
            worker_database,
            "SELECT count(*) FROM outbox_events WHERE topic = :topic AND idempotency_key = :id",
            topic=service.CONFIRMED_TOPIC,
            id=deposit.id,
        )
        == 1
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        entry = await ledger.get_entry(session, confirmed.ledger_entry_id)
        assert await ledger.find_balance_drift(session) == []
    assert entry.entry_type == "payment.deposit_confirmed"
    assert sorted(line.amount.minor for line in entry.lines) == [-50_000, 50_000]
    # The client's request and the confirmation are on the audit trail, with no amount or number.
    assert await audit_trail(app_database, deposit) == [
        ("payments.deposit_requested", "client", "succeeded", {"method": "mtn"}),
        (
            "payments.deposit_confirmed",
            "system",
            "succeeded",
            {"method": "mtn", "cause": "status_read"},
        ),
    ]
    # The client is told once, in the same transaction as the confirmation.
    assert await notices(worker_database, deposit) == [
        ("payment_confirmed", "We received your payment from MTN Mobile Money.", "deposit")
    ]


async def notices(database: Database, deposit: Deposit) -> list[tuple[str, str, str]]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT event, body, link_kind FROM notifications "
                "WHERE client_id = :client AND link_id = :id ORDER BY created_at, id"
            ),
            {"client": deposit.client_id, "id": deposit.id},
        )
        return [(row.event, row.body, row.link_kind) for row in rows]


async def test_the_providers_charge_is_finvests_cost_and_the_client_is_credited_in_full(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    deposit, msisdn, ref = await awaiting_deposit(app_database, provider, worker, fake_clock)
    # ioTec's 24.5 reaches us already rounded up by the adapter.
    confirmation = Confirmed(ref, ugx(50_000), ugx(25))
    provider.on_status(msisdn, confirmation)
    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)
    confirmed = await reload(worker_database, deposit)
    assert confirmed.status is S.CONFIRMED
    assert await awaiting_start(worker_database, deposit) == 50_000
    # A second report of the same confirmation books nothing more.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await service.settle_deposit(
            session, deposit.id, confirmation, fake_clock, cause="reconciliation"
        )
    assert await entries(worker_database, deposit) == 2
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT e.entry_type, a.code, l.amount_minor FROM ledger_journal_entries e "
                    "JOIN ledger_lines l ON l.entry_id = e.id "
                    "JOIN ledger_accounts a ON a.id = l.account_id "
                    "WHERE e.source_type = 'payment.deposit' AND e.source_id = :id "
                    "ORDER BY e.entry_type, l.amount_minor"
                ),
                {"id": deposit.id},
            )
        ).all()
        assert await ledger.find_balance_drift(session) == []
    wallet = f"{deposit.provider}.wallet"
    assert [tuple(row) for row in rows] == [
        ("payment.deposit_confirmed", "client.awaiting_start", -50_000),
        ("payment.deposit_confirmed", wallet, 50_000),
        ("payment.provider_charge", wallet, -25),
        ("payment.provider_charge", f"fees.provider.{deposit.provider}", 25),
    ]


async def test_a_refused_request_fails_and_posts_nothing(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn = phone()
    provider.on_send(msisdn, Rejected(RejectionCode.ACCOUNT_NOT_FOUND))
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    failed = await reload(worker_database, deposit)
    assert (failed.status, failed.code) == (S.FAILED, "account_not_found")
    assert await entries(worker_database, deposit) == 0
    assert await awaiting_start(worker_database, deposit) == 0
    assert (await audit_trail(app_database, deposit))[-1] == (
        "payments.deposit_failed",
        "system",
        "failed",
        {"method": "mtn", "cause": "send", "failure": "account_not_found"},
    )
    [(event, body, _)] = await notices(worker_database, deposit)
    assert event == "payment_failed" and "No money was taken" in body


async def audit_trail(
    app_database: Database, deposit: Deposit
) -> list[tuple[str, str, str, dict[str, object]]]:
    async with app_database.actor_transaction(new_staff()) as session:
        rows = await session.execute(
            text(
                "SELECT action, actor_kind, outcome, details FROM audit_events "
                "WHERE resource_type = 'payment_deposit' AND resource_id = :id "
                "ORDER BY occurred_at, id"
            ),
            {"id": deposit.id},
        )
        return [
            (
                row.action,
                row.actor_kind,
                row.outcome,
                row.details if isinstance(row.details, dict) else json.loads(row.details),
            )
            for row in rows
        ]


async def test_a_payer_who_declines_fails_the_deposit(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    deposit, msisdn, ref = await awaiting_deposit(app_database, provider, worker, fake_clock)
    provider.on_status(msisdn, Rejected(RejectionCode.DECLINED, ref))
    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)
    failed = await reload(worker_database, deposit)
    assert (failed.status, failed.code) == (S.FAILED, "declined")
    assert await entries(worker_database, deposit) == 0

    # A failed deposit is final, whatever anyone reports later.
    async with worker_database.actor_transaction(SYSTEM) as session:
        late = await service.settle_deposit(
            session, deposit.id, Confirmed(ref, ugx(50_000)), fake_clock, cause="reconciliation"
        )
    assert late.status is S.FAILED
    assert await entries(worker_database, deposit) == 0
    # Told once that it failed, and never that it was paid.
    assert [event for event, _, _ in await notices(worker_database, deposit)] == ["payment_failed"]


# Deferred: it certainly did not happen, so sending again is safe


async def test_a_deferred_request_is_sent_again_later(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Deferred(), Accepted(ref))
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.CREATED
    fake_clock.advance(seconds=RETRY.total_seconds())
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.AWAITING_APPROVAL
    assert provider.sends_for(deposit) == 2
    assert [status for status, _ in await history(worker_database, deposit)] == [
        "created",
        "sending",
        "created",
        "sending",
        "awaiting_approval",
    ]


async def test_a_request_deferred_every_time_fails_with_no_money_moved(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn = phone()
    provider.on_send(msisdn, Deferred())
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    for _ in range(service.SEND_ATTEMPTS):
        await drain(worker)
        fake_clock.advance(seconds=RETRY.total_seconds())
    failed = await reload(worker_database, deposit)
    assert (failed.status, failed.code) == (S.FAILED, "provider_unavailable")
    assert provider.sends_for(deposit) == service.SEND_ATTEMPTS


# Uncertain: it may have happened, so it is never sent again


async def test_a_request_that_times_out_is_never_sent_again(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Uncertain(UncertainReason.TIMED_OUT))
    provider.on_status(msisdn, NotFound())
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    uncertain = await reload(worker_database, deposit)
    assert (uncertain.status, uncertain.code) == (S.UNCERTAIN, "send_uncertain")

    # Time passes, status reads and sweeps run. The provider is asked what happened, and the
    # request is not repeated.
    for _ in range(4):
        fake_clock.advance(minutes=6)
        await sweep(worker, worker_database, fake_clock)
    assert provider.sends_for(deposit) == 1
    assert provider.reads_for(deposit) >= 1
    assert (await reload(worker_database, deposit)).status is S.UNCERTAIN
    assert await entries(worker_database, deposit) == 0

    # Reconciliation finds the payment in the provider's record and settles it.
    async with worker_database.actor_transaction(SYSTEM) as session:
        settled = await service.settle_deposit(
            session, deposit.id, Confirmed(ref, ugx(50_000)), fake_clock, cause="reconciliation"
        )
    assert (settled.status, settled.provider_reference) == (S.CONFIRMED, ref)
    assert await awaiting_start(worker_database, deposit) == 50_000
    assert (await history(worker_database, deposit))[-2:] == [
        ("uncertain", "send_uncertain"),
        ("confirmed", None),
    ]


async def test_an_uncertain_deposit_is_read_again_until_a_late_answer_settles_it(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Uncertain(UncertainReason.TIMED_OUT))
    provider.on_status(msisdn, NotFound())
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    fake_clock.advance(seconds=5)
    await drain(worker)
    assert provider.reads_for(deposit) == 1

    # The first read found nothing. The sweep keeps asking, about once a minute at first.
    for _ in range(5):
        fake_clock.advance(minutes=2)
        await sweep(worker, worker_database, fake_clock)
    early = provider.reads_for(deposit)
    assert early >= 3
    assert (await reload(worker_database, deposit)).status is S.UNCERTAIN

    # Forty minutes on, the provider's record catches up. The next read settles it.
    provider.on_status(msisdn, Confirmed(ref, ugx(50_000)))
    for _ in range(4):
        fake_clock.advance(minutes=10)
        await sweep(worker, worker_database, fake_clock)
    settled = await reload(worker_database, deposit)
    assert (settled.status, settled.provider_reference) == (S.CONFIRMED, ref)
    assert await entries(worker_database, deposit) == 1
    assert provider.sends_for(deposit) == 1

    # Settled deposits are left alone.
    reads = provider.reads_for(deposit)
    fake_clock.advance(minutes=30)
    await sweep(worker, worker_database, fake_clock)
    assert provider.reads_for(deposit) == reads


async def test_a_deposit_uncertain_for_the_second_time_is_still_read_and_settled(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Uncertain(UncertainReason.TIMED_OUT))
    provider.on_status(msisdn, NotFound())
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    # First period: two numbered reads run and find nothing.
    for _ in range(2):
        fake_clock.advance(minutes=3)
        await sweep(worker, worker_database, fake_clock)
    assert (await reload(worker_database, deposit)).status is S.UNCERTAIN

    # The provider has it after all, the prompt goes unanswered, and it is uncertain again.
    provider.on_status(msisdn, Pending(ref))
    for _ in range(8):
        fake_clock.advance(minutes=3)
        await sweep(worker, worker_database, fake_clock)
    again = await reload(worker_database, deposit)
    assert (again.status, again.code) == (S.UNCERTAIN, "approval_overdue")
    assert [status for status, _ in await history(worker_database, deposit)].count("uncertain") == 2

    # Second period: its reads start from number one again and must still take effect.
    provider.on_status(msisdn, Confirmed(ref, ugx(50_000)))
    for _ in range(2):
        fake_clock.advance(minutes=10)
        await sweep(worker, worker_database, fake_clock)
    settled = await reload(worker_database, deposit)
    assert settled.status is S.CONFIRMED
    assert await entries(worker_database, deposit) == 1


async def test_sweeps_do_not_pile_up_reads_of_an_uncertain_deposit(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn = phone()
    provider.on_send(msisdn, Uncertain(UncertainReason.TIMED_OUT))
    provider.on_status(msisdn, NotFound())
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    fake_clock.advance(minutes=30)
    # Ten sweeps in the same minute queue one read, due in ten minutes, and nothing runs yet.
    before = provider.reads_for(deposit)
    for _ in range(10):
        await sweep(worker, worker_database, fake_clock)
    assert provider.reads_for(deposit) == before
    assert (
        await count(
            worker_database,
            "SELECT count(*) FROM jobs WHERE kind = :kind AND starts_with(idempotency_key, :key) "
            "AND status = 'pending'",
            kind=service.VERIFY_KIND,
            key=f"{deposit.id}:uncertain:",
        )
        == 1
    )
    fake_clock.advance(minutes=10)
    await drain(worker)
    assert provider.reads_for(deposit) == before + 1


async def test_a_sweep_reaches_every_uncertain_deposit_however_many_there_are(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pages of two, so five deposits take three pages. Older ones must not wait behind newer.
    monkeypatch.setattr(deposit_jobs, "SWEEP_BATCH", 2)
    deposits = []
    for _ in range(5):
        msisdn = phone()
        provider.on_send(msisdn, Uncertain(UncertainReason.TIMED_OUT))
        provider.on_status(msisdn, NotFound())
        deposits.append(await start_deposit(app_database, fake_clock, msisdn=msisdn))
        await drain(worker)
        fake_clock.advance(minutes=1)
    fake_clock.advance(minutes=30)
    await sweep(worker, worker_database, fake_clock)
    for deposit in deposits:
        assert (
            await count(
                worker_database,
                "SELECT count(*) FROM jobs WHERE kind = :kind "
                "AND starts_with(idempotency_key, :key) AND status = 'pending'",
                kind=service.VERIFY_KIND,
                key=f"{deposit.id}:uncertain:",
            )
            == 1
        )


async def test_status_reads_of_an_uncertain_deposit_stop_after_a_day(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn = phone()
    provider.on_send(msisdn, Uncertain(UncertainReason.TIMED_OUT))
    provider.on_status(msisdn, NotFound())
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    fake_clock.advance(seconds=UNCERTAIN_READ_WINDOW.total_seconds() + 3600)
    await drain(worker)  # anything queued earlier is past its age by now
    reads = provider.reads_for(deposit)
    for _ in range(3):
        await sweep(worker, worker_database, fake_clock)
        fake_clock.advance(hours=2)
        await drain(worker)
    assert provider.reads_for(deposit) == reads
    # Still uncertain, and still there for reconciliation to settle.
    assert (await reload(worker_database, deposit)).status is S.UNCERTAIN


async def test_a_different_confirmed_amount_is_not_read_again(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    deposit, msisdn, ref = await awaiting_deposit(app_database, provider, worker, fake_clock)
    provider.on_status(msisdn, Confirmed(ref, ugx(49_000)))
    fake_clock.advance(seconds=30)
    await drain(worker)
    held = await reload(worker_database, deposit)
    assert (held.status, held.code) == (S.UNCERTAIN, "amount_mismatch")
    reads = provider.reads_for(deposit)
    for _ in range(3):
        fake_clock.advance(minutes=5)
        await sweep(worker, worker_database, fake_clock)
    assert provider.reads_for(deposit) == reads


async def test_an_uncertain_deposit_the_provider_did_take_is_picked_up_by_a_status_read(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Uncertain(UncertainReason.CONNECTION_LOST))
    provider.on_status(msisdn, Pending(ref), Confirmed(ref, ugx(50_000)))
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.AWAITING_APPROVAL
    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.CONFIRMED
    assert provider.sends_for(deposit) == 1
    assert await entries(worker_database, deposit) == 1


@pytest.mark.parametrize(
    ("reading_kind", "expected"),
    [("pending", S.AWAITING_APPROVAL), ("not_found", S.UNCERTAIN)],
)
async def test_an_adapter_that_raises_is_treated_as_uncertain_and_not_sent_again(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
    reading_kind: str,
    expected: DepositStatus,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, RuntimeError("0772123456 lost"), Accepted(ref))
    provider.on_status(msisdn, Pending(ref) if reading_kind == "pending" else NotFound())
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    await drain(worker)
    unclear = await reload(worker_database, deposit)
    assert (unclear.status, unclear.code) == (S.UNCERTAIN, "send_uncertain")

    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is expected
    assert provider.sends_for(deposit) == 1
    assert provider.reads_for(deposit) == 1


async def test_a_worker_that_dies_mid_call_does_not_send_twice(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn, ref = phone(), reference()
    provider.on_send(msisdn, Accepted(ref))
    provider.on_status(msisdn, Confirmed(ref, ugx(50_000)))
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)

    # A worker claims the send job, marks the deposit, calls the provider and dies before it
    # saves the answer. Its lease is never released.
    claimed = await job_queue.claim(
        worker_database,
        queue="default",
        kinds=[service.SEND_KIND],
        external_kinds=[service.SEND_KIND],
        worker="worker-that-dies",
        now=fake_clock.now(),
        lease=LEASE,
        limit=100,
    )
    job = next(job for job in claimed if job.payload.get("deposit_id") == deposit.id)
    async with worker_database.actor_transaction(SYSTEM) as session:
        locked = await service.find_deposit(session, deposit.id, lock=True)
        assert locked is not None
        await service.apply_move(session, locked, Move(S.SENDING), fake_clock, cause="send")
    assert deposit.network is not None and deposit.payer_msisdn is not None
    await provider.request_collection(
        CollectionRequest(deposit.id, deposit.network, deposit.payer_msisdn, deposit.amount)
    )

    # The lease expires. The queue does not run a job with an external effect again.
    fake_clock.advance(seconds=LEASE.total_seconds() + 1)
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.SENDING
    assert (
        await count(
            worker_database,
            "SELECT count(*) FROM jobs WHERE id = :id AND status = 'failed' "
            "AND last_error_code = 'uncertain_outcome'",
            id=job.id,
        )
        == 1
    )

    # The sweep finds the mark, calls it uncertain and queues a read. The read settles it.
    fake_clock.advance(seconds=SENDING_GRACE.total_seconds())
    await sweep(sweep_only(worker_database, provider, fake_clock), worker_database, fake_clock)
    marked = await reload(worker_database, deposit)
    assert (marked.status, marked.code) == (S.UNCERTAIN, "send_uncertain")
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.CONFIRMED
    assert provider.sends_for(deposit) == 1
    assert await entries(worker_database, deposit) == 1
    assert await awaiting_start(worker_database, deposit) == 50_000


async def test_a_confirmation_for_another_amount_posts_nothing_and_waits_for_staff(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    deposit, msisdn, ref = await awaiting_deposit(app_database, provider, worker, fake_clock)
    provider.on_status(msisdn, Confirmed(ref, ugx(49_000)))
    fake_clock.advance(seconds=POLL.total_seconds())
    await drain(worker)
    held = await reload(worker_database, deposit)
    assert (held.status, held.code) == (S.UNCERTAIN, "amount_mismatch")
    assert await entries(worker_database, deposit) == 0
    assert await awaiting_start(worker_database, deposit) == 0


async def test_a_prompt_nobody_answers_goes_to_reconciliation_after_the_window(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    deposit, _, _ = await awaiting_deposit(app_database, provider, worker, fake_clock)
    fake_clock.advance(seconds=APPROVAL_WINDOW.total_seconds())
    await sweep(worker, worker_database, fake_clock)
    overdue = await reload(worker_database, deposit)
    assert (overdue.status, overdue.code) == (S.UNCERTAIN, "approval_overdue")


# Sweeps pick up deposits whose own job was lost


async def test_a_deposit_that_was_never_sent_fails_and_is_not_sent_late(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    msisdn = phone()
    provider.on_send(msisdn, Accepted(reference()))
    deposit = await start_deposit(app_database, fake_clock, msisdn=msisdn)
    # No worker ran for ten minutes. The payer has long stopped waiting.
    fake_clock.advance(seconds=UNSENT_EXPIRY.total_seconds())
    await sweep(sweep_only(worker_database, provider, fake_clock), worker_database, fake_clock)
    failed = await reload(worker_database, deposit)
    assert (failed.status, failed.code) == (S.FAILED, "provider_unavailable")
    await drain(worker)  # the old send job is past its age and must not prompt the payer now
    assert provider.sends_for(deposit) == 0


async def test_a_quiet_prompt_is_read_again_and_confirmed_once(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    deposit, msisdn, ref = await awaiting_deposit(app_database, provider, worker, fake_clock)
    provider.on_status(msisdn, Confirmed(ref, ugx(50_000)))
    fake_clock.advance(seconds=SENDING_GRACE.total_seconds())
    # The sweep queues a read while the ordinary poll is also due. Both run; one posts.
    await sweep(sweep_only(worker_database, provider, fake_clock), worker_database, fake_clock)
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.CONFIRMED
    assert provider.reads_for(deposit) >= 2
    assert await entries(worker_database, deposit) == 1
    assert await awaiting_start(worker_database, deposit) == 50_000


# Races


async def test_two_confirmations_at_once_post_one_entry(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
    pools: dict[str, list[Database]],
    concurrency_barrier: AsyncBarrier,
) -> None:
    deposit, _, ref = await awaiting_deposit(app_database, provider, worker, fake_clock)

    async def settle(database: Database) -> DepositStatus:
        async with database.actor_transaction(SYSTEM) as session:
            await session.execute(text("SELECT 1"))
            await concurrency_barrier.wait()
            settled = await service.settle_deposit(
                session, deposit.id, Confirmed(ref, ugx(50_000)), fake_clock, cause="status_read"
            )
            return settled.status

    results = await asyncio.gather(*(settle(database) for database in pools["finvest_worker"]))
    assert results == [S.CONFIRMED, S.CONFIRMED]
    assert await entries(worker_database, deposit) == 1
    assert await awaiting_start(worker_database, deposit) == 50_000
    assert [status for status, _ in await history(worker_database, deposit)].count("confirmed") == 1


async def test_a_confirmation_racing_a_rejection_settles_one_way_only(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
    pools: dict[str, list[Database]],
    concurrency_barrier: AsyncBarrier,
) -> None:
    deposit, _, ref = await awaiting_deposit(app_database, provider, worker, fake_clock)
    readings: list[Confirmed | Rejected] = [
        Confirmed(ref, ugx(50_000)),
        Rejected(RejectionCode.DECLINED, ref),
    ]

    async def settle(database: Database, reading: Confirmed | Rejected) -> DepositStatus:
        async with database.actor_transaction(SYSTEM) as session:
            await session.execute(text("SELECT 1"))
            await concurrency_barrier.wait()
            settled = await service.settle_deposit(
                session, deposit.id, reading, fake_clock, cause="status_read"
            )
            return settled.status

    first, second = await asyncio.gather(
        *(
            settle(database, reading)
            for database, reading in zip(pools["finvest_worker"], readings, strict=True)
        )
    )
    assert first is second
    final = await reload(worker_database, deposit)
    assert final.status is first
    assert await entries(worker_database, deposit) == (1 if first is S.CONFIRMED else 0)
    assert await awaiting_start(worker_database, deposit) == (50_000 if first is S.CONFIRMED else 0)


async def test_a_double_tap_makes_one_deposit_and_one_prompt(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
    pools: dict[str, list[Database]],
) -> None:
    actor, msisdn, key, purpose = client(), phone(), f"key-{push_id()}", push_id()
    provider.on_send(msisdn, Accepted(reference()))
    first, second = await asyncio.gather(
        *(
            start_deposit(
                database, fake_clock, actor=actor, msisdn=msisdn, key=key, purpose_id=purpose
            )
            for database in pools["finvest_app"]
        )
    )
    assert first.id == second.id
    again = await start_deposit(
        app_database, fake_clock, actor=actor, msisdn=msisdn, key=key, purpose_id=purpose
    )
    assert again.id == first.id
    await drain(worker)
    assert provider.sends_for(first) == 1
    assert (
        await count(
            worker_database,
            "SELECT count(*) FROM payment_deposits WHERE client_id = :id",
            id=actor.id,
        )
        == 1
    )


async def test_an_investment_with_an_open_deposit_refuses_a_second_one(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    actor, msisdn, purpose = client(), phone(), push_id()
    provider.on_send(msisdn, Accepted(reference()))
    provider.on_status(msisdn, Pending(reference()))
    first = await start_deposit(
        app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
    )
    # The answer was lost, the client reopened the pay screen, and the app made a new key.
    with pytest.raises(service.DepositExists) as refused:
        await start_deposit(
            app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
        )
    assert refused.value.code == "conflict"
    assert (refused.value.deposit_id, refused.value.status) == (first.id, S.CREATED)

    await drain(worker)
    with pytest.raises(service.DepositExists) as waiting:
        await start_deposit(
            app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
        )
    assert (waiting.value.deposit_id, waiting.value.status) == (first.id, S.AWAITING_APPROVAL)
    # One deposit, one prompt.
    assert len([r for r in provider.requests if r.msisdn == msisdn]) == 1


@pytest.mark.parametrize(
    ("send", "reading", "status"),
    [
        (Uncertain(UncertainReason.TIMED_OUT), NotFound(), S.UNCERTAIN),
        (Accepted("ref-paid"), None, S.CONFIRMED),
    ],
)
async def test_an_uncertain_or_paid_investment_refuses_another_deposit(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
    send: SendResult,
    reading: StatusReading | None,
    status: DepositStatus,
) -> None:
    actor, msisdn, purpose, ref = client(), phone(), push_id(), reference()
    provider.on_send(msisdn, Accepted(ref) if isinstance(send, Accepted) else send)
    provider.on_status(msisdn, reading or Confirmed(ref, ugx(50_000)))
    first = await start_deposit(
        app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
    )
    await drain(worker)
    fake_clock.advance(seconds=30)
    await drain(worker)
    assert (await reload(worker_database, first)).status is status
    with pytest.raises(service.DepositExists) as refused:
        await start_deposit(
            app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
        )
    assert (refused.value.deposit_id, refused.value.status) == (first.id, status)


async def test_a_failed_deposit_frees_the_investment_for_another_try(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    actor, msisdn, purpose = client(), phone(), push_id()
    provider.on_send(msisdn, Rejected(RejectionCode.INSUFFICIENT_FUNDS), Accepted(reference()))
    first = await start_deposit(
        app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
    )
    await drain(worker)
    assert (await reload(worker_database, first)).status is S.FAILED
    second = await start_deposit(
        app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
    )
    assert second.id != first.id
    await drain(worker)
    assert (await reload(worker_database, second)).status is S.AWAITING_APPROVAL


async def test_other_modules_can_ask_whether_money_may_still_arrive(
    app_database: Database,
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
) -> None:
    async def live(purpose_id: str, purpose: Purpose = Purpose.INVESTMENT) -> bool:
        async with worker_database.actor_transaction(SYSTEM) as session:
            return await service.has_live_deposit(session, purpose=purpose, purpose_id=purpose_id)

    actor, msisdn, purpose = client(), phone(), push_id()
    assert not await live(purpose)

    # Uncertain: the money may still arrive, so the investment must not be cancelled.
    provider.on_send(msisdn, Uncertain(UncertainReason.TIMED_OUT))
    provider.on_status(msisdn, NotFound())
    deposit = await start_deposit(
        app_database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose
    )
    assert await live(purpose)
    assert not await live(purpose, Purpose.GOAL)
    await drain(worker)
    assert (await reload(worker_database, deposit)).status is S.UNCERTAIN
    assert await live(purpose)

    # Reconciliation finds it was never taken. Nothing is on its way any more.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await service.settle_deposit(
            session,
            deposit.id,
            Rejected(RejectionCode.DECLINED),
            fake_clock,
            cause="reconciliation",
        )
    assert not await live(purpose)


async def test_two_new_keys_at_once_make_one_deposit_for_an_investment(
    worker_database: Database,
    provider: FakeCollections,
    worker: Worker,
    fake_clock: FakeClock,
    pools: dict[str, list[Database]],
) -> None:
    actor, msisdn, purpose = client(), phone(), push_id()
    provider.on_send(msisdn, Accepted(reference()))
    # Two devices, two keys, two transactions that cannot see each other's insert.
    results = await asyncio.gather(
        *(
            start_deposit(database, fake_clock, actor=actor, msisdn=msisdn, purpose_id=purpose)
            for database in pools["finvest_app"]
        ),
        return_exceptions=True,
    )
    made = [result for result in results if isinstance(result, Deposit)]
    refused = [result for result in results if isinstance(result, service.DepositExists)]
    assert (len(made), len(refused)) == (1, len(results) - 1)
    # The loser is told which deposit won, so the app can take the client to it.
    assert {refusal.deposit_id for refusal in refused} == {made[0].id}
    await drain(worker)
    assert len([r for r in provider.requests if r.msisdn == msisdn]) == 1
    assert (
        await count(
            worker_database,
            "SELECT count(*) FROM payment_deposits WHERE purpose_id = :id",
            id=purpose,
        )
        == 1
    )


async def test_the_database_itself_refuses_a_second_live_deposit_for_an_investment(
    app_database: Database, fake_clock: FakeClock
) -> None:
    actor = client()
    first = await start_deposit(app_database, fake_clock, actor=actor)
    with pytest.raises(DBAPIError, match="uq_payment_deposits_live_investment"):
        async with app_database.actor_transaction(actor) as session:
            await session.execute(
                text(
                    "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, "
                    "method, provider, currency, amount_minor, payer_msisdn, created_at, "
                    "updated_at) SELECT :id, client_id, purpose_type, purpose_id, method, "
                    "provider, currency, amount_minor, payer_msisdn, created_at, updated_at "
                    "FROM payment_deposits WHERE id = :first"
                ),
                {"id": push_id(), "first": first.id},
            )


# Requests


async def test_a_key_reused_for_a_different_payment_is_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    actor, key = client(), f"key-{push_id()}"
    await start_deposit(app_database, fake_clock, actor=actor, key=key, amount=50_000)
    with pytest.raises(ApplicationError) as refused:
        await start_deposit(app_database, fake_clock, actor=actor, key=key, amount=60_000)
    assert refused.value.code == "idempotency_key_reused"


@pytest.mark.parametrize(
    ("changes", "field", "code"),
    [
        ({"phone": "12345"}, "phone", "invalid_phone"),
        ({"amount": ugx(0)}, "amount", "invalid_amount"),
        ({"amount": ugx(-1)}, "amount", "invalid_amount"),
        ({"purpose_id": "not-an-id"}, "purpose_id", "invalid_id"),
    ],
)
async def test_a_bad_request_saves_nothing_and_does_not_echo_what_was_sent(
    app_database: Database,
    worker_database: Database,
    fake_clock: FakeClock,
    changes: dict[str, object],
    field: str,
    code: str,
) -> None:
    actor = client()
    values: dict[str, object] = {
        "purpose": Purpose.INVESTMENT,
        "purpose_id": push_id(),
        "network": Network.AIRTEL,
        "amount": ugx(10_000),
        "phone": phone(),
    }
    values.update(changes)
    with pytest.raises(ApplicationError) as refused:
        async with app_database.actor_transaction(actor) as session:
            await service.request_deposit(
                session,
                actor,
                DepositRequest(**values),  # type: ignore[arg-type]
                idempotency_key=f"key-{push_id()}",
                account_phone=str(values["phone"]) if "phone" not in changes else phone(),
                provider=PROVIDER,
                clock=fake_clock,
            )
    assert refused.value.code == "validation_failed"
    problem = refused.value.fields[0]
    assert (problem.field_path, problem.code) == ([field], code)
    assert (
        await count(
            worker_database,
            "SELECT count(*) FROM payment_deposits WHERE client_id = :id",
            id=actor.id,
        )
        == 0
    )


async def test_a_payment_from_a_number_that_is_not_the_accounts_is_refused(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    actor, other = client(), phone()
    with pytest.raises(ApplicationError) as refused:
        await start_deposit(
            app_database, fake_clock, actor=actor, msisdn=other, account_phone="+256772000111"
        )
    assert refused.value.code == "validation_failed"
    assert [(f.field_path, f.code) for f in refused.value.fields] == [
        (["phone"], "not_account_phone")
    ]
    assert (
        await count(
            worker_database,
            "SELECT count(*) FROM payment_deposits WHERE client_id = :id",
            id=actor.id,
        )
        == 0
    )


async def test_the_accounts_number_may_be_written_in_any_usual_form(
    app_database: Database, fake_clock: FakeClock
) -> None:
    deposit = await start_deposit(
        app_database, fake_clock, msisdn="0772000222", account_phone="+256772000222"
    )
    assert deposit.payer_msisdn == "+256772000222"


async def test_only_a_client_starts_a_deposit(
    app_database: Database, fake_clock: FakeClock
) -> None:
    staff = Actor("staff", push_id())
    with pytest.raises(ApplicationError) as refused:
        await start_deposit(app_database, fake_clock, actor=staff)
    assert refused.value.code == "forbidden"


# What the database itself refuses


async def test_a_client_sees_only_their_own_deposits(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, other, staff = client(), client(), Actor("staff", push_id())
    deposit = await start_deposit(app_database, fake_clock, actor=owner)
    async with app_database.actor_transaction(owner) as session:
        assert (await service.get_deposit(session, deposit.id)).id == deposit.id
        assert [row.id for row in await service.list_deposits(session)] == [deposit.id]
    async with app_database.actor_transaction(other) as session:
        with pytest.raises(ApplicationError) as hidden:
            await service.get_deposit(session, deposit.id)
        assert hidden.value.code == "not_found"
        assert await service.list_deposits(session) == []
    async with app_database.transaction() as session:
        assert await service.find_deposit(session, deposit.id) is None
    async with app_database.actor_transaction(staff) as session:
        listed = await service.list_deposits(session, status=S.CREATED, limit=200)
        assert deposit.id in [row.id for row in listed]


RAW_INSERT = text(
    "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, provider, "
    "currency, amount_minor, payer_msisdn, status, created_at, updated_at) VALUES (:id, "
    ":client_id, 'investment', :purpose_id, 'mtn', 'fake', 'UGX', 1000, '+256772000000', "
    ":status, now(), now())"
)


@pytest.mark.parametrize("forged", ["someone_else", "already_sending"])
async def test_the_api_cannot_forge_a_deposit(app_database: Database, forged: str) -> None:
    actor = client()
    values = {
        "id": push_id(),
        "client_id": push_id() if forged == "someone_else" else actor.id,
        "purpose_id": push_id(),
        "status": "sending" if forged == "already_sending" else "created",
    }
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(actor) as session:
            await session.execute(RAW_INSERT, values)


async def test_the_api_cannot_change_a_deposit_and_nobody_can_change_its_terms(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
) -> None:
    owner = client()
    deposit = await start_deposit(app_database, fake_clock, actor=owner)
    update = text("UPDATE payment_deposits SET status = 'failed' WHERE id = :id")
    for actor in (owner, Actor("staff", push_id())):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(update, {"id": deposit.id})
    for column, value in [("amount_minor", "1"), ("client_id", f"'{push_id()}'")]:
        change = text(
            f"UPDATE payment_deposits SET {column} = {value} WHERE id = :id"  # noqa: S608
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(change, {"id": deposit.id})
        with pytest.raises(DBAPIError, match="terms cannot change"):
            async with migrator_database.transaction() as session:
                await session.execute(change, {"id": deposit.id})
    for statement in (
        "DELETE FROM payment_deposits WHERE id = :id",
        "DELETE FROM payment_deposit_events WHERE deposit_id = :id",
        "UPDATE payment_deposit_events SET to_status = 'failed' WHERE deposit_id = :id",
    ):
        with pytest.raises(DBAPIError, match="does not allow this"):
            async with migrator_database.transaction() as session:
                await session.execute(text(statement), {"id": deposit.id})
    for table in ("payment_deposits", "payment_deposit_events", "payment_provider_notices"):
        with pytest.raises(DBAPIError, match="does not allow this"):
            async with migrator_database.transaction() as session:
                await session.execute(text(f"TRUNCATE {table} CASCADE"))


class Rollback(Exception):
    pass


async def test_the_database_allows_exactly_the_moves_the_rules_allow(
    migrator_database: Database,
) -> None:
    """Every pair of states is tried against the trigger and compared with TRANSITIONS."""
    shape = {
        # status: (code, has entry, settled)
        "created": (None, False, False),
        "sending": (None, False, False),
        "awaiting_approval": (None, False, False),
        "confirmed": (None, True, True),
        "failed": ("declined", False, True),
        "uncertain": ("send_uncertain", False, False),
        "in_review": (None, False, False),
    }
    allowed: dict[DepositStatus, set[DepositStatus]] = {status: set() for status in S}
    insert = text(
        "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, "
        "provider, currency, amount_minor, payer_msisdn, status, code, ledger_entry_id, "
        "created_at, updated_at, settled_at) VALUES (:id, :client, 'goal', :purpose, :method, "
        ":provider, 'UGX', 1000, :msisdn, :status, :code, :entry, now(), now(), :settled)"
    )
    # A bank transfer is settled only as its staff review says, so one in review gets the
    # review its move needs. Every other state is a mobile money deposit.
    review = text(
        "INSERT INTO payment_bank_reviews (deposit_id, outcome, reason, statement_line_id, "
        "account_reference, reviewed_by, reviewed_at) VALUES (:id, :outcome, :reason, :line, "
        ":account, :by, now())"
    )
    move = text(
        "UPDATE payment_deposits SET status = :status, code = :code, ledger_entry_id = :entry, "
        "settled_at = :settled WHERE id = :id"
    )

    with pytest.raises(Rollback):
        async with migrator_database.transaction() as session:
            now = (await session.execute(text("SELECT now()"))).scalar_one()
            for old in S:
                for new in S:
                    if old is new:
                        continue
                    code, has_entry, settled = shape[old.value]
                    deposit_id = push_id()
                    bank = old is S.IN_REVIEW
                    await session.execute(
                        insert,
                        {
                            "id": deposit_id,
                            "client": push_id(),
                            "purpose": push_id(),
                            "method": "bank_transfer" if bank else "airtel",
                            "provider": "bank" if bank else "fake",
                            "msisdn": None if bank else "+256752000000",
                            "status": old.value,
                            "code": code,
                            "entry": push_id() if has_entry else None,
                            "settled": now if settled else None,
                        },
                    )
                    matched = new is S.CONFIRMED
                    if bank:
                        await session.execute(
                            review,
                            {
                                "id": deposit_id,
                                "outcome": "matched" if matched else "not_matched",
                                "reason": None if matched else "other",
                                "line": push_id() if matched else None,
                                "account": "trust" if matched else None,
                                "by": push_id(),
                            },
                        )
                        if matched:
                            await session.execute(
                                text(
                                    "INSERT INTO payment_bank_approvals (deposit_id, "
                                    "approved_by, approved_at) VALUES (:id, :by, now())"
                                ),
                                {"id": deposit_id, "by": push_id()},
                            )
                    code, has_entry, settled = shape[new.value]
                    if bank and new is S.FAILED:
                        code = "other"
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                move,
                                {
                                    "id": deposit_id,
                                    "status": new.value,
                                    "code": code,
                                    "entry": push_id() if has_entry else None,
                                    "settled": now if settled else None,
                                },
                            )
                        allowed[old].add(new)
                    except DBAPIError as refused:
                        assert "deposit" in str(refused.orig)
            # Nothing here is kept.
            raise Rollback
    assert allowed == {status: set(moves) for status, moves in TRANSITIONS.items()}


async def test_a_deposit_cannot_be_confirmed_without_its_ledger_entry(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    deposit = await start_deposit(app_database, fake_clock)
    with pytest.raises(DBAPIError, match="ck_payment_deposits_confirmed_has_entry"):
        async with migrator_database.transaction() as session:
            for status in ("sending", "awaiting_approval"):
                await session.execute(
                    text("UPDATE payment_deposits SET status = :status WHERE id = :id"),
                    {"id": deposit.id, "status": status},
                )
            await session.execute(
                text(
                    "UPDATE payment_deposits SET status = 'confirmed', settled_at = now() "
                    "WHERE id = :id"
                ),
                {"id": deposit.id},
            )


# Callbacks are hints


SECRET = "test-notice-secret"


async def test_a_callback_causes_a_status_read_and_proves_nothing_itself(
    app_database: Database,
    worker_database: Database,
    test_database: TestDatabase,
    fake_clock: FakeClock,
) -> None:
    sandbox = SandboxCollections(fake_clock, notice_secret=SECRET)
    worker = Worker(
        worker_database,
        DepositJobs(worker_database, sandbox, fake_clock).handlers(),
        clock=fake_clock,
    )
    app = create_app(
        test_database.settings("finvest_app"), app_database, fake_clock, collections=sandbox
    )
    deposit = await start_deposit(
        app_database, fake_clock, msisdn="+256772000056", provider=sandbox.name
    )
    await drain(worker)
    waiting = await reload(worker_database, deposit)
    assert waiting.status is S.AWAITING_APPROVAL

    def callback(notice_id: str) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(
            {"id": notice_id, "request_id": deposit.id, "reference": waiting.provider_reference}
        ).encode()
        return body, {SIGNATURE_HEADER: sandbox.sign(body)}

    async def notices() -> int:
        return await count(
            worker_database,
            "SELECT count(*) FROM payment_provider_notices WHERE request_id = :id",
            id=deposit.id,
        )

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as api:
        url = "/provider-callbacks/collections/sandbox"
        body, headers = callback("notice-1")

        forged = await api.post(url, content=body, headers={SIGNATURE_HEADER: "0" * 64})
        assert forged.status_code == 403
        assert (await api.post(url, content=body)).status_code == 403
        assert (
            await api.post("/provider-callbacks/collections/other", content=body)
        ).status_code == 404
        huge = await api.post(url, content=b"x" * 20_000, headers=headers)
        assert huge.status_code == 413
        assert await notices() == 0

        # The callback says "paid" while the provider's own record still says "prompting".
        assert (await api.post(url, content=body, headers=headers)).status_code == 204
        assert (await api.post(url, content=body, headers=headers)).status_code == 204
        assert await notices() == 1
        assert (
            await count(
                worker_database,
                "SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :key",
                kind=service.VERIFY_KIND,
                key="notice:sandbox:notice-1",
            )
            == 1
        )
        await drain(worker)
        assert (await reload(worker_database, deposit)).status is S.AWAITING_APPROVAL
        assert await entries(worker_database, deposit) == 0

        # Once the provider's record says the money arrived, the next read posts it.
        fake_clock.advance(seconds=PROMPT_TIME.total_seconds())
        body, headers = callback("notice-2")
        assert (await api.post(url, content=body, headers=headers)).status_code == 204
        await drain(worker)
        assert (await reload(worker_database, deposit)).status is S.CONFIRMED
        assert await entries(worker_database, deposit) == 1

        # A callback about something we never asked for is recorded and changes nothing.
        stray = json.dumps({"id": "notice-3", "reference": "sbx:unknown"}).encode()
        answered = await api.post(
            url, content=stray, headers={SIGNATURE_HEADER: sandbox.sign(stray)}
        )
        assert answered.status_code == 204
        await drain(worker)

        unusable = json.dumps({"id": "has space", "request_id": deposit.id}).encode()
        answered = await api.post(
            url, content=unusable, headers={SIGNATURE_HEADER: sandbox.sign(unusable)}
        )
        assert answered.status_code == 422


async def test_callbacks_are_not_served_when_payments_are_disabled(
    app_database: Database, test_database: TestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FINVEST_PAYMENTS_PROVIDER", raising=False)
    app = create_app(test_database.settings("finvest_app"), app_database)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as api:
        answered = await api.post("/provider-callbacks/collections/sandbox", content=b"{}")
    assert answered.status_code == 404


async def test_the_callback_address_cannot_read_what_it_records(app_database: Database) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.transaction() as session:
            await session.execute(text("SELECT count(*) FROM payment_provider_notices"))
