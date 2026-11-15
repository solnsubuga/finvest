"""Buying a bond against real PostgreSQL: a quote held for a day, one payment, one ledger entry.

Issues are never removed and tests share one database, so every test makes its own issue with
its own code and ISIN, and pauses it again before it ends.
"""

import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.bonds import catalogue, holdings
from app.modules.bonds.domain.holdings import CANCEL_GRACE, PAYMENT_WINDOW, HoldingStatus
from app.modules.bonds.domain.issues import BondStatus, quote
from app.modules.bonds.events import START_KIND
from app.modules.bonds.jobs import BondJobs
from app.modules.identity.hashing import PasswordHasher
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payments.domain.outcomes import Accepted, Confirmed
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import approved_client
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import key
from tests.support.ledger import staff as new_staff
from tests.support.payments import FakeCollections, deposit_worker, drain

pytestmark = pytest.mark.integration

PRICE = 985_000
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def now() -> FakeClock:
    """The database dates ledger entries by the business day, so tests run at the real time."""
    return FakeClock(datetime.now(UTC))


async def approve(database: Database, change_id: str, clock: Clock) -> None:
    checker = new_staff()
    async with database.actor_transaction(checker) as session:
        await catalogue.approve(session, checker, change_id, clock)


@asynccontextmanager
async def live_issue(database: Database, clock: Clock, **overrides: object) -> AsyncIterator[str]:
    """A new issue, open to clients at PRICE, paused again afterwards."""
    tag = uuid.uuid4().hex[:8].upper()
    code, isin = f"BOND-{tag}", f"UGT{tag}0"
    values: dict[str, object] = {
        "code": code,
        "isin": isin,
        "name": "10 Year Treasury Bond 2037",
        "currency": Currency.UGX,
        "coupon_bps": 1600,
        "tax_bps": 1000,
        "issue_date": date(2025, 5, 14),
        "maturity_date": date(2037, 5, 14),
        "minimum_face_minor": 100_000,
        "face_step_minor": 100_000,
        "price": PRICE,
    }
    values.update(overrides)
    maker = new_staff()
    async with database.actor_transaction(maker) as session:
        change = await catalogue.propose_issue(
            session,
            maker,
            catalogue.NewIssue(**values),  # type: ignore[arg-type]
            "From the auction",
            clock,
        )
    await approve(database, change.id, clock)
    await set_status(database, code, BondStatus.LIVE, clock)
    try:
        yield code
    finally:
        await set_status(database, code, BondStatus.PAUSED, clock)


async def set_status(database: Database, code: str, to: BondStatus, clock: Clock) -> None:
    maker = new_staff()
    async with database.actor_transaction(maker) as session:
        view = await catalogue.issue_view(session, code)
        if view.issue.status is to:
            return
        change = await catalogue.propose_status(session, maker, code, to, "Test", clock)
    await approve(database, change.id, clock)


async def buy(
    database: Database, client: Actor, code: str, clock: Clock, face: int = 1_000_000, **kw: str
) -> holdings.Holding:
    async with database.actor_transaction(client) as session:
        return await holdings.request_purchase(
            session,
            client,
            idempotency_key=kw.get("idempotency_key", key()),
            code=code,
            face=ugx(face),
            clock=clock,
        )


def awaiting_spec(holding: holdings.Holding) -> AccountSpec:
    return AccountSpec(
        holdings.AWAITING_START,
        AccountKind.LIABILITY,
        holding.face.currency,
        "Paid in, awaiting start",
        owner_client_id=holding.client_id,
        reference=holding.id,
    )


async def pay(worker: Database, holding: holdings.Holding, amount: Money, clock: Clock) -> None:
    """What the payments module does when a deposit is confirmed."""
    async with worker.actor_transaction(SYSTEM) as session:
        cash = await ledger.open_account(
            session,
            AccountSpec("test.collections", AccountKind.ASSET, amount.currency, "Test collections"),
            clock,
        )
        awaiting = await ledger.open_account(session, awaiting_spec(holding), clock)
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.deposit_confirmed",
                effective_date=business_date(clock.now()),
                lines=(debit(cash.id, amount), credit(awaiting.id, amount)),
                idempotency_key=key(),
            ),
            clock,
        )


async def balance(worker: Database, spec: AccountSpec, clock: Clock) -> int:
    async with worker.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(session, spec, clock)
        return (await ledger.get_balance(session, account.id)).minor


async def run_start_job(
    worker: Database, data: dict[str, object], clock: Clock
) -> tuple[str, str | None]:
    """Queue the consumer job as the outbox would, run it, and return how it ended."""
    queue = "bond" + "".join(c for c in push_id().lower() if c.isalnum())
    async with worker.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=START_KIND,
                idempotency_key=f"payments.deposit_confirmed:{push_id()}",
                payload={
                    "event_id": push_id(),
                    "topic": "payments.deposit_confirmed",
                    "data": data,
                },
                queue=queue,
            ),
            now=clock.now(),
        )
    runner = Worker(worker, BondJobs(worker, clock).handlers(), WorkerConfig(queue=queue), clock)
    assert await runner.run_once() == 1
    async with worker.actor_transaction(SYSTEM) as session:
        row = (
            await session.execute(
                text("SELECT status, last_error_code FROM jobs WHERE id = :id"), {"id": job_id}
            )
        ).one()
    return row.status, row.last_error_code


def confirmed(holding: holdings.Holding, clock: Clock) -> dict[str, object]:
    return {
        "purpose_type": "bond",
        "purpose_id": holding.id,
        "confirmed_on": business_date(clock.now()).isoformat(),
    }


async def test_a_client_buys_at_todays_price_and_the_payment_starts_the_holding(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    today = business_date(clock.now())
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock) as code:
        holding = await buy(app_database, client, code, clock)
        expected = quote(holding.issue, ugx(1_000_000), PRICE, today)
        assert (holding.status, holding.price, holding.quoted_on) == (
            HoldingStatus.UNPAID,
            PRICE,
            today,
        )
        assert (holding.accrued, holding.cost) == (expected.accrued, expected.cost)
        assert holding.pay_by == clock.now() + PAYMENT_WINDOW

        await pay(worker_database, holding, holding.cost, clock)
        assert await run_start_job(worker_database, confirmed(holding, clock), clock) == (
            "succeeded",
            None,
        )
        async with worker_database.actor_transaction(SYSTEM) as session:
            started = await holdings.get_holding(session, holding.id)
        assert (started.status, started.start_date) == (HoldingStatus.ACTIVE, today)
        assert await balance(worker_database, awaiting_spec(holding), clock) == 0
        held = holdings.holding_account(holding)
        assert await balance(worker_database, held, clock) == holding.cost.minor

        # The same event again changes nothing.
        assert await run_start_job(worker_database, confirmed(holding, clock), clock) == (
            "succeeded",
            None,
        )
        assert await balance(worker_database, held, clock) == holding.cost.minor
        async with worker_database.actor_transaction(SYSTEM) as session:
            entries = await session.execute(
                text(
                    "SELECT count(*) FROM ledger_journal_entries "
                    "WHERE source_type = 'bond_holding' AND source_id = :id"
                ),
                {"id": holding.id},
            )
        assert entries.scalar_one() == 1

        # The client sees it after tax: 72,000 a coupon, never the tax.
        async with app_database.actor_transaction(client) as session:
            mine = await holdings.client_holdings(session, str(client.id), clock)
        assert [(h.id, h.status, h.coupon_net) for h in mine] == [
            (holding.id, HoldingStatus.ACTIVE, ugx(72_000))
        ]


async def test_a_part_payment_does_not_start_a_holding(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock) as code:
        holding = await buy(app_database, client, code, clock)
        await pay(worker_database, holding, holding.cost - ugx(1), clock)
        status, error = await run_start_job(worker_database, confirmed(holding, clock), clock)
        assert (status, error) == ("failed", "bond_holding_conflict")
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert (await holdings.get_holding(session, holding.id)).status is HoldingStatus.UNPAID


async def test_a_retry_with_the_same_key_is_the_same_holding(app_database: Database) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock) as code:
        same = key()
        first = await buy(app_database, client, code, clock, idempotency_key=same)
        again = await buy(app_database, client, code, clock, idempotency_key=same)
        assert again.id == first.id
        with pytest.raises(ApplicationError) as changed:
            await buy(app_database, client, code, clock, face=2_000_000, idempotency_key=same)
        assert changed.value.code == "idempotency_key_reused"


async def test_a_purchase_is_refused_as_the_quote_is(app_database: Database) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock) as code:
        for face, problem in [(50_000, "below_minimum"), (150_000, "not_a_step")]:
            with pytest.raises(ApplicationError) as error:
                await buy(app_database, client, code, clock, face=face)
            assert [(p.field_path[0], p.code) for p in error.value.fields] == [
                ("face_minor", problem)
            ]
        await set_status(app_database, code, BondStatus.PAUSED, clock)
        with pytest.raises(ApplicationError) as paused:
            await buy(app_database, client, code, clock)
        assert [(p.field_path[0], p.code) for p in paused.value.fields] == [
            ("code", "product_not_open")
        ]


async def test_nobody_buys_before_their_identity_check_is_approved(
    app_database: Database,
) -> None:
    clock = now()
    stranger = Actor("client", push_id())
    async with live_issue(app_database, clock) as code:
        with pytest.raises(ApplicationError) as error:
            await buy(app_database, stranger, code, clock)
    assert error.value.code == "forbidden"


async def test_the_database_holds_the_price_and_who_may_move_a_holding(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock) as code:
        holding = await buy(app_database, client, code, clock)
        async with app_database.actor_transaction(client) as session:
            price_id = (
                await session.execute(
                    text("SELECT price_id FROM bond_holdings WHERE id = :id"), {"id": holding.id}
                )
            ).scalar_one()
        insert = text(
            "INSERT INTO bond_holdings (id, client_id, issue_id, currency, face_minor, price, "
            "price_id, accrued_minor, cost_minor, quoted_on, created_at, pay_by, updated_at) "
            "VALUES (:id, :client_id, :issue_id, 'UGX', 1000000, :price, :price_id, 0, 900000, "
            "CURRENT_DATE, now(), now() + interval '1 day', now())"
        )
        values = {
            "client_id": client.id,
            "issue_id": holding.issue_id,
            "price_id": price_id,
        }
        # Not at the price in force.
        with pytest.raises(DBAPIError, match="at the price in force"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(insert, {**values, "id": push_id(), "price": 900_000})
        # Not for somebody else.
        with pytest.raises(DBAPIError, match="bought by the client signed in"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    insert, {**values, "id": push_id(), "price": PRICE, "client_id": push_id()}
                )
        # A client cannot move it on, and nobody changes what was bought.
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text("UPDATE bond_holdings SET status = 'active' WHERE id = :id"),
                    {"id": holding.id},
                )
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("UPDATE bond_holdings SET cost_minor = 1 WHERE id = :id"),
                    {"id": holding.id},
                )
        with pytest.raises(DBAPIError, match="only started or cancelled"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("UPDATE bond_holdings SET updated_at = now() WHERE id = :id"),
                    {"id": holding.id},
                )
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("DELETE FROM bond_holdings WHERE id = :id"), {"id": holding.id}
                )


async def test_an_unpaid_holding_is_cancelled_after_its_window_unless_money_came(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock) as code:
        unpaid = await buy(app_database, client, code, clock)
        paid = await buy(app_database, client, code, clock)
        await pay(worker_database, paid, paid.cost, clock)
        later = FakeClock(clock.now() + PAYMENT_WINDOW + CANCEL_GRACE)
        async with worker_database.actor_transaction(SYSTEM) as session:
            await holdings.cancel_unpaid(session, later)
            assert (await holdings.get_holding(session, unpaid.id)).status is (
                HoldingStatus.CANCELLED
            )
            # Money arrived for this one, so it is left for the start or for staff.
            assert (await holdings.get_holding(session, paid.id)).status is HoldingStatus.UNPAID
        async with app_database.actor_transaction(client) as session:
            with pytest.raises(ApplicationError) as closed:
                await holdings.payable_holding(
                    session, client_id=str(client.id), holding_id=unpaid.id, clock=later
                )
        assert closed.value.code == "conflict"


# Over HTTP --------------------------------------------------------------------------------------


@pytest.fixture
async def http_api(
    test_database: TestDatabase, app_database: Database
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeCollections, FakeClock]]:
    settings = test_database.settings("finvest_app")
    clock = now()
    collections = FakeCollections()
    app = create_app(settings, app_database, clock, HASHER, collections=collections)
    transport = httpx.ASGITransport(
        app=app,
        raise_app_exceptions=False,
        client=("127.0.0.1", 1),
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        http.app = app  # type: ignore[attr-defined]
        yield http, collections, clock


async def signed_in(http: httpx.AsyncClient, database: Database, clock: Clock) -> tuple[Actor, str]:
    identity = http.app.state.identity  # type: ignore[attr-defined]
    phone = "+2567" + "".join(secrets.choice("0123456789") for _ in range(8))
    client_id = await identity.create_client(
        phone=phone, email=None, password="copper-harbour-violin"
    )
    actor = Actor("client", client_id)
    async with database.actor_transaction(actor) as session:
        await ready_draft(session, actor, clock)
        check = await kyc.submit(session, SEALER, actor, ManualProvider(), clock)
    reviewer = new_staff()
    async with database.actor_transaction(reviewer) as session:
        await kyc.approve(session, SEALER, reviewer, check.id, "Documents match", clock)
    issued = await identity.issue_session(actor, second_step="passkey", transport="token")
    http.headers["Authorization"] = f"Bearer {issued.token}"
    return actor, phone


async def test_a_client_buys_a_bond_and_pays_by_mobile_money_over_http(
    http_api: tuple[httpx.AsyncClient, FakeCollections, FakeClock],
    app_database: Database,
    worker_database: Database,
) -> None:
    http, collections, clock = http_api
    client, phone = await signed_in(http, app_database, clock)
    async with live_issue(app_database, clock) as code:
        bought = await http.post(
            "/client/bond-holdings",
            json={"code": code, "face_minor": 1_000_000, "currency": "UGX"},
            headers={"Idempotency-Key": key()},
        )
        assert bought.status_code == 201, bought.text
        body = bought.json()
        assert (body["status"], body["coupon_minor"], body["face_minor"]) == (
            "unpaid",
            72_000,
            1_000_000,
        )
        # Clients never see the tax.
        assert not {k for k in body if "tax" in k}
        holding_id, cost = body["id"], body["cost_minor"]

        collections.on_send(phone, Accepted("ref-" + holding_id))
        paid = await http.post(
            f"/client/bond-holdings/{holding_id}/payments/mobile-money",
            json={"network": "mtn"},
            headers={"Idempotency-Key": key()},
        )
        assert paid.status_code == 201, paid.text
        payment = paid.json()
        assert (payment["purpose"], payment["investment_id"], payment["amount"]["minor"]) == (
            "bond",
            holding_id,
            cost,
        )
        again = await http.post(
            f"/client/bond-holdings/{holding_id}/payments/mobile-money",
            json={"network": "airtel"},
            headers={"Idempotency-Key": key()},
        )
        assert again.status_code == 409
        current = await http.get(f"/client/bond-holdings/{holding_id}/payment")
        assert current.json()["id"] == payment["id"]

        collections.on_status(phone, Confirmed("ref-" + holding_id, ugx(cost)))
        payer = deposit_worker(worker_database, collections, clock)
        await drain(payer)
        clock.advance(seconds=10)
        await drain(payer)
        starter = Worker(
            worker_database, BondJobs(worker_database, clock).handlers(), WorkerConfig(), clock
        )
        await drain(starter)
        shown = (await http.get(f"/client/bond-holdings/{holding_id}")).json()
        assert (shown["status"], shown["start_date"]) == (
            "active",
            business_date(clock.now()).isoformat(),
        )
        assert [h["id"] for h in (await http.get("/client/bond-holdings")).json()] == [holding_id]

        # Another client neither sees it nor pays for it.
        await signed_in(http, app_database, clock)
        assert (await http.get(f"/client/bond-holdings/{holding_id}")).status_code == 404
        other = await http.post(
            f"/client/bond-holdings/{holding_id}/payments/mobile-money",
            json={"network": "mtn"},
            headers={"Idempotency-Key": key()},
        )
        assert other.status_code == 404
        assert client.id is not None
