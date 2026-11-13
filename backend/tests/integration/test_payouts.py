"""Payouts: limits, the safety wait, and the ledger entry that goes with every step."""

import asyncio
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import EntryDraft, credit, debit
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain import posting
from app.modules.payouts.domain.destinations import (
    BankAccount,
    DestinationKind,
    MobileMoneyNumber,
    ProofKind,
)
from app.modules.payouts.domain.payouts import IllegalTransition, PayoutEvent, PayoutStatus
from app.modules.payouts.payouts import PayoutRefused, PayoutView
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.ledger import key, staff, ugx

pytestmark = pytest.mark.integration

E, S = PayoutEvent, PayoutStatus


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

    async def build(self, funds: int, *, mobile: bool = True) -> "World":
        account: MobileMoneyNumber | BankAccount = (
            MobileMoneyNumber.parse("0772123456") if mobile else BankAccount("stanbic", "90300123")
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
        checker = staff()
        async with self.app.actor_transaction(checker) as session:
            await destinations.approve(
                session,
                checker,
                view.id,
                id_name="Sarah Nakato",
                proof_name="Nakato Sarah",
                statement_date=None,
                reason="Checked",
                clock=self.clock,
            )
        self.destination_id = view.id
        self.clock.advance(hours=24)
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


@pytest.fixture
async def world(app_database: Database, worker_database: Database, fake_clock: FakeClock) -> World:
    return await World(app_database, worker_database, fake_clock).build(8_000_000)


# --- Creating ---------------------------------------------------------------------------------


async def test_a_payout_reserves_its_money_and_is_queued(world: World) -> None:
    payout = await world.request(1_000_000)
    assert (payout.status, payout.amount, payout.client_id) == (
        S.QUEUED,
        ugx(1_000_000),
        world.client_id,
    )
    assert payout.business_date == business_date(world.clock.now())
    assert await world.balance(world.holding) == 7_000_000
    assert await world.balance(payout.in_flight_account_id) == 1_000_000
    assert await world.pot_change() == 8_000_000
    assert await world.owed(payout) == 8_000_000


async def test_paid_takes_the_money_out_of_the_pot_and_notes_the_first_payout(
    world: World,
) -> None:
    payout = await world.request(1_000_000)
    sent = await world.worker_event(payout.id, E.SEND_ACCEPTED, provider_reference="IOTEC-1")
    assert (sent.status, sent.provider_reference) == (S.SENT, "IOTEC-1")
    assert await world.pot_change() == 8_000_000
    paid = await world.worker_event(payout.id, E.STATUS_PAID)
    assert (paid.status, paid.provider_reference) == (S.PAID, "IOTEC-1")
    assert await world.pot_change() == 7_000_000
    assert await world.balance(payout.in_flight_account_id) == 0
    assert await world.owed(payout) == 7_000_000
    async with world.worker.actor_transaction(SYSTEM) as session:
        destination = await destinations.get_destination(session, world.destination_id)
    assert destination.first_paid_at == world.clock.now()
    await world.no_drift()


async def test_failed_parks_the_money_for_staff_and_owes_the_client_the_same(
    world: World,
) -> None:
    payout = await world.request(1_000_000)
    failed = await world.worker_event(payout.id, E.SEND_REJECTED, failure_code="recipient_barred")
    assert (failed.status, failed.failure_code) == (S.FAILED, "recipient_barred")
    assert await world.pending() == 1_000_000
    assert await world.balance(payout.in_flight_account_id) == 0
    assert await world.balance(world.holding) == 7_000_000
    assert await world.pot_change() == 8_000_000
    assert await world.owed(payout) == 8_000_000
    await world.no_drift()


async def test_an_uncertain_payout_holds_its_money_until_the_provider_answers(
    world: World,
) -> None:
    payout = await world.request(1_000_000)
    uncertain = await world.worker_event(payout.id, E.SEND_UNCERTAIN)
    assert uncertain.status is S.UNCERTAIN
    assert await world.balance(payout.in_flight_account_id) == 1_000_000
    for event in (E.SEND_ACCEPTED, E.SEND_CONFIRMED, E.SEND_REJECTED, E.SEND_DEFERRED):
        with pytest.raises(IllegalTransition):
            await world.worker_event(payout.id, event)
    still = await world.worker_event(payout.id, E.STATUS_UNKNOWN)
    assert still.status is S.UNCERTAIN
    assert (await world.worker_event(payout.id, E.STATUS_PAID)).status is S.PAID
    assert await world.pot_change() == 7_000_000


async def test_the_database_will_not_requeue_a_payout_that_may_have_left(world: World) -> None:
    sent = await world.request(100_000)
    await world.worker_event(sent.id, E.SEND_ACCEPTED)
    uncertain = await world.request(100_000)
    await world.worker_event(uncertain.id, E.SEND_UNCERTAIN)
    for payout_id in (sent.id, uncertain.id):
        for database, actor in ((world.worker, SYSTEM), (world.app, staff())):
            with pytest.raises(DBAPIError):
                async with database.actor_transaction(actor) as session:
                    await session.execute(
                        text("UPDATE payouts SET status = 'queued' WHERE id = :id"),
                        {"id": payout_id},
                    )


async def test_a_step_cannot_post_its_money_twice(world: World) -> None:
    payout = await world.request(1_000_000)
    await world.worker_event(payout.id, E.SEND_CONFIRMED)
    with pytest.raises(IllegalTransition):
        await world.worker_event(payout.id, E.SEND_CONFIRMED)
    with pytest.raises(IllegalTransition):
        await world.worker_event(payout.id, E.STATUS_FAILED)
    assert await world.pot_change() == 7_000_000
    assert await world.pending() == 0


# --- Refusals ---------------------------------------------------------------------------------


async def test_refusals_leave_nothing_behind(world: World) -> None:
    assert await world.refused(5_000_001) == "over_mobile_money_limit"
    assert await world.refused(0) == "amount_not_positive"
    async with world.app.actor_transaction(world.client) as session:
        with pytest.raises(PayoutRefused) as dollars:
            await payouts.create_payout(
                session,
                world.client,
                client_id=world.client_id,
                destination_id=world.destination_id,
                amount=Money(100, Currency.USD),
                source_reference=world.handover,
                request_key=key(),
                clock=world.clock,
            )
        assert dollars.value.code == "currency_not_supported"
        assert await payouts.payouts_for_client(session, world.client_id) == []
    assert await world.balance(world.holding) == 8_000_000


async def test_more_than_the_client_holds_is_refused_and_leaves_nothing(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    world = await World(app_database, worker_database, fake_clock).build(500_000)
    async with world.app.actor_transaction(world.client) as session:
        with pytest.raises(PayoutRefused) as refusal:
            await payouts.create_payout(
                session,
                world.client,
                client_id=world.client_id,
                destination_id=world.destination_id,
                amount=ugx(500_001),
                source_reference=world.handover,
                request_key=key(),
                clock=world.clock,
            )
        assert refusal.value.code == "insufficient_funds"
        # The same transaction goes on to commit, and still holds no part of the payout.
    async with world.app.actor_transaction(world.client) as session:
        assert await payouts.payouts_for_client(session, world.client_id) == []
    assert await world.balance(world.holding) == 500_000
    assert (await world.request(500_000)).status is S.QUEUED


async def test_a_new_destination_cannot_be_paid_for_24_hours(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    world = await World(app_database, worker_database, fake_clock).build(1_000_000)
    world.clock.advance(hours=-24, seconds=1)
    assert await world.refused(1_000) == "destination_in_safety_wait"
    world.clock.advance(hours=23, minutes=59, seconds=58)
    assert await world.refused(1_000) == "destination_in_safety_wait"
    world.clock.advance(seconds=1)
    assert (await world.request(1_000)).status is S.QUEUED


async def test_a_restricted_session_pays_only_a_destination_already_paid(world: World) -> None:
    restricted = world.clock.now()
    assert await world.refused(1_000, restricted_since=restricted) == "destination_not_known_yet"
    first = await world.request(1_000)
    await world.worker_event(first.id, E.SEND_CONFIRMED)
    assert await world.refused(1_000, restricted_since=restricted) == "destination_not_known_yet"
    world.clock.advance(hours=1)
    later = world.clock.now()
    assert (await world.request(1_000, restricted_since=later)).status is S.QUEUED


async def test_a_client_cannot_use_another_clients_destination_or_money(
    world: World, app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    thief = await World(app_database, worker_database, fake_clock).build(0)
    async with app_database.actor_transaction(thief.client) as session:
        with pytest.raises(ApplicationError) as hidden:
            await payouts.create_payout(
                session,
                thief.client,
                client_id=thief.client_id,
                destination_id=world.destination_id,
                amount=ugx(1_000),
                source_reference=thief.handover,
                request_key=key(),
                clock=fake_clock,
            )
        assert hidden.value.code == "not_found"
    async with app_database.actor_transaction(thief.client) as session:
        with pytest.raises(PayoutRefused) as refusal:
            await payouts.create_payout(
                session,
                thief.client,
                client_id=thief.client_id,
                destination_id=thief.destination_id,
                amount=ugx(1_000),
                source_reference=world.handover,
                request_key=key(),
                clock=fake_clock,
            )
        # The reference names the thief's own, empty, handover account. Never the victim's.
        assert refusal.value.code == "insufficient_funds"
    assert await world.balance(world.holding) == 8_000_000
    mine = await world.request(1_000)
    async with app_database.actor_transaction(thief.client) as session:
        with pytest.raises(ApplicationError):
            await payouts.get_payout(session, mine.id)


# --- Where the money may come from ------------------------------------------------------------


async def test_a_payout_cannot_be_funded_from_another_payouts_money_in_flight(
    world: World,
) -> None:
    first = await world.request(1_000_000)
    # The only handle a caller has is a reference. Naming the first payout by its ID, or by
    # its in-flight account's ID, finds an empty handover account and nothing else.
    for reference in (first.id, first.in_flight_account_id, first.source_account_id):
        for database, actor in ((world.app, world.client), (world.worker, SYSTEM)):
            async with database.actor_transaction(actor) as session:
                with pytest.raises(PayoutRefused) as refusal:
                    await payouts.create_payout(
                        session,
                        actor,
                        client_id=world.client_id,
                        destination_id=world.destination_id,
                        amount=ugx(1_000_000),
                        source_reference=reference,
                        request_key=key(),
                        clock=world.clock,
                    )
                assert refusal.value.code == "insufficient_funds"
    assert await world.balance(first.in_flight_account_id) == 1_000_000
    assert (await world.worker_event(first.id, E.SEND_CONFIRMED)).status is S.PAID
    assert await world.pot_change() == 7_000_000
    await world.no_drift()


@pytest.mark.parametrize("reference", ["", " ", "has space", "x" * 65, "naïve"])
async def test_a_payout_cannot_be_funded_from_the_failed_payout_pot_or_a_bad_reference(
    world: World, reference: str
) -> None:
    failed = await world.request(1_000_000)
    await world.worker_event(failed.id, E.SEND_REJECTED, failure_code="recipient_barred")
    assert await world.pending() == 1_000_000
    async with world.app.actor_transaction(world.client) as session:
        with pytest.raises(PayoutRefused) as refusal:
            await payouts.create_payout(
                session,
                world.client,
                client_id=world.client_id,
                destination_id=world.destination_id,
                amount=ugx(1_000),
                source_reference=reference,
                request_key=key(),
                clock=world.clock,
            )
        assert refusal.value.code == "source_not_allowed"
    assert await world.pending() == 1_000_000


async def test_the_worker_cannot_fund_one_clients_payout_from_anothers_money(
    world: World, app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    other = await World(app_database, worker_database, fake_clock).build(0)
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(PayoutRefused) as refusal:
            await payouts.create_payout(
                session,
                SYSTEM,
                client_id=other.client_id,
                destination_id=other.destination_id,
                amount=ugx(1_000),
                source_reference=world.handover,
                request_key=key(),
                clock=fake_clock,
            )
        assert refusal.value.code == "insufficient_funds"
    assert await world.balance(world.holding) == 8_000_000


async def test_the_database_refuses_a_payout_whose_source_is_its_own_in_flight_account(
    world: World,
) -> None:
    payout = await world.request(1_000)
    with pytest.raises(DBAPIError):
        async with world.app.actor_transaction(world.client) as session:
            await session.execute(
                text(
                    "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, "
                    "currency, status, business_date, source_account_id, source_reference, "
                    "in_flight_account_id, request_key, created_at, updated_at) SELECT :id, "
                    "client_id, destination_id, kind, 5, currency, 'queued', business_date, "
                    "in_flight_account_id, source_reference, in_flight_account_id, :id, now(), "
                    "now() FROM payouts WHERE id = :source"
                ),
                {"id": push_id(), "source": payout.id},
            )


# --- One request, one payout ------------------------------------------------------------------


async def test_the_same_request_key_gives_the_same_payout_and_reserves_once(world: World) -> None:
    request_key = key()

    async def ask(amount: int) -> PayoutView:
        async with world.app.actor_transaction(world.client) as session:
            return await payouts.create_payout(
                session,
                world.client,
                client_id=world.client_id,
                destination_id=world.destination_id,
                amount=ugx(amount),
                source_reference=world.handover,
                request_key=request_key,
                clock=world.clock,
            )

    first = await ask(1_000_000)
    world.clock.advance(minutes=5)
    assert (await ask(1_000_000)).id == first.id
    assert await world.balance(world.holding) == 7_000_000
    with pytest.raises(ApplicationError) as reused:
        await ask(2_000_000)
    assert reused.value.code == "idempotency_key_reused"
    results = await asyncio.gather(*(ask(1_000_000) for _ in range(5)))
    assert {payout.id for payout in results} == {first.id}
    assert await world.balance(world.holding) == 7_000_000


async def test_the_worker_can_request_a_payout_for_a_client_but_staff_cannot(
    world: World,
) -> None:
    async with world.worker.actor_transaction(SYSTEM) as session:
        payout = await payouts.create_payout(
            session,
            SYSTEM,
            client_id=world.client_id,
            destination_id=world.destination_id,
            amount=ugx(250_000),
            source_reference=world.handover,
            request_key=f"maturity:{push_id()}",
            clock=world.clock,
        )
    assert (payout.status, payout.client_id) == (S.QUEUED, world.client_id)
    assert await world.balance(world.holding) == 7_750_000
    checker = staff()
    for actor, client_id in ((checker, world.client_id), (world.client, push_id())):
        async with world.app.actor_transaction(actor) as session:
            with pytest.raises(ApplicationError) as refused:
                await payouts.create_payout(
                    session,
                    actor,
                    client_id=client_id,
                    destination_id=world.destination_id,
                    amount=ugx(1_000),
                    source_reference=world.handover,
                    request_key=key(),
                    clock=world.clock,
                )
            assert refused.value.code == "forbidden"


async def test_a_stored_choice_must_be_the_clients_own_approved_destination(
    world: World, app_database: Database, fake_clock: FakeClock
) -> None:
    async with world.worker.actor_transaction(SYSTEM) as session:
        found = await destinations.usable_destination(
            session, world.client_id, world.destination_id
        )
        assert found.id == world.destination_id
        with pytest.raises(ApplicationError):
            await destinations.usable_destination(session, push_id(), world.destination_id)
    async with app_database.actor_transaction(world.client) as session:
        other = await destinations.add_destination(
            session, world.client, BankAccount("absa", "60012345"), "Sarah Nakato", fake_clock
        )
        with pytest.raises(ApplicationError):
            await destinations.usable_destination(session, world.client_id, other.id)
        await destinations.remove(session, world.client, world.destination_id, fake_clock)
        with pytest.raises(ApplicationError):
            await destinations.usable_destination(session, world.client_id, world.destination_id)


# --- Review of large withdrawals --------------------------------------------------------------


async def bank_world(app: Database, worker: Database, clock: FakeClock) -> World:
    return await World(app, worker, clock).build(30_000_000, mobile=False)


async def test_a_large_withdrawal_is_held_until_staff_approve_it(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    world = await bank_world(app_database, worker_database, fake_clock)
    assert (await world.request(10_000_000)).status is S.QUEUED
    held = await world.request(10_000_001)
    assert held.status is S.HELD
    assert await world.balance(held.in_flight_account_id) == 10_000_001
    with pytest.raises(IllegalTransition):
        await world.worker_event(held.id, E.SEND_ACCEPTED)

    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        assert held.id in [p.id for p in await payouts.held_for_review(session, limit=100)]
        with pytest.raises(ApplicationError):
            await payouts.apply_event(session, checker, held.id, E.REVIEW_APPROVED, fake_clock)
        approved = await payouts.apply_event(
            session, checker, held.id, E.REVIEW_APPROVED, fake_clock, reason="Spoke to the client"
        )
    assert (approved.status, approved.reviewed_by) == (S.QUEUED, checker.id)
    assert (await world.worker_event(held.id, E.SEND_CONFIRMED)).status is S.PAID


async def test_declining_or_cancelling_a_held_payout_gives_the_money_back(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    world = await bank_world(app_database, worker_database, fake_clock)
    declined = await world.request(12_000_000)
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        await payouts.apply_event(
            session, checker, declined.id, E.REVIEW_DECLINED, fake_clock, reason="Could not reach"
        )
    assert await world.balance(world.holding) == 30_000_000

    cancelled = await world.request(12_000_000)
    async with app_database.actor_transaction(world.client) as session:
        result = await payouts.apply_event(
            session, world.client, cancelled.id, E.CLIENT_CANCELLED, fake_clock
        )
    assert result.status is S.CANCELLED
    assert await world.balance(world.holding) == 30_000_000
    assert await world.balance(cancelled.in_flight_account_id) == 0
    await world.no_drift()


async def test_only_the_right_actor_takes_each_step(world: World) -> None:
    payout = await world.request(100_000)
    checker = staff()
    # A person cannot report what the provider said, through the service or around it.
    for actor in (world.client, checker):
        async with world.app.actor_transaction(actor) as session:
            with pytest.raises(ApplicationError):
                await payouts.apply_event(session, actor, payout.id, E.SEND_CONFIRMED, world.clock)
        with pytest.raises(DBAPIError):
            async with world.app.actor_transaction(actor) as session:
                await session.execute(
                    text("UPDATE payouts SET status = 'paid' WHERE id = :id"), {"id": payout.id}
                )
    # Nobody changes the terms.
    for change in ("amount_minor = 1", f"destination_id = '{push_id()}'", "currency = 'USD'"):
        with pytest.raises(DBAPIError):
            async with world.worker.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(f"UPDATE payouts SET {change} WHERE id = :id"),  # noqa: S608
                    {"id": payout.id},
                )
    # A client cannot insert a payout that is already further along.
    with pytest.raises(DBAPIError):
        async with world.app.actor_transaction(world.client) as session:
            await session.execute(
                text(
                    "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, "
                    "currency, status, business_date, source_account_id, source_reference, "
                    "in_flight_account_id, request_key, created_at, updated_at) SELECT :id, "
                    "client_id, destination_id, kind, 5, currency, 'paid', business_date, "
                    "source_account_id, source_reference, in_flight_account_id, :id, now(), "
                    "now() FROM payouts WHERE id = :source"
                ),
                {"id": push_id(), "source": payout.id},
            )


async def test_a_finished_payout_never_changes_and_nothing_is_deleted(
    world: World, migrator_database: Database
) -> None:
    payout = await world.request(100_000)
    await world.worker_event(payout.id, E.SEND_CONFIRMED)
    for statement in (
        "UPDATE payouts SET status = 'queued' WHERE id = :id",
        "UPDATE payouts SET failure_code = 'x' WHERE id = :id",
        "DELETE FROM payouts WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with migrator_database.transaction() as session:
                await session.execute(text(statement), {"id": payout.id})


# --- Limits -----------------------------------------------------------------------------------


async def test_the_launch_limits_are_seeded_and_cannot_be_edited(
    world: World, migrator_database: Database
) -> None:
    async with world.app.actor_transaction(world.client) as session:
        limits = await payouts.current_limits(session, business_date(world.clock.now()))
    assert limits.mobile_money_per_payout == ugx(5_000_000)
    assert limits.review_above == ugx(10_000_000)
    assert (limits.mobile_money_per_day, limits.bank_per_payout, limits.bank_per_day) == (
        None,
        None,
        None,
    )
    with pytest.raises(DBAPIError):
        async with migrator_database.transaction() as session:
            await session.execute(text("UPDATE withdrawal_limits SET review_above = 1"))


async def test_a_limits_version_needs_two_people_and_stays_at_or_under_five_million(
    world: World,
) -> None:
    approver, proposer = staff(), staff()
    insert = text(
        "INSERT INTO withdrawal_limits (id, effective_from, currency, mobile_money_per_payout, "
        "proposed_by, approved_by, reason, created_at) VALUES (:id, :day, 'UGX', :limit, "
        ":proposed_by, :approved_by, 'Test', now())"
    )

    async def attempt(day: str, limit: int, proposed_by: str | None, by: Actor) -> None:
        async with world.app.actor_transaction(by) as session:
            await session.execute(
                insert,
                {
                    "id": push_id(),
                    "day": date.fromisoformat(day),
                    "limit": limit,
                    "proposed_by": proposed_by,
                    "approved_by": approver.id,
                },
            )

    for day, limit, proposed_by, by in (
        ("2090-01-01", 5_000_001, proposer.id, approver),  # over the mobile money ceiling
        ("2090-01-02", 1_000_000, approver.id, approver),  # one person
        ("2090-01-03", 1_000_000, None, approver),  # no proposer
        ("2090-01-04", 1_000_000, proposer.id, proposer),  # signed in another's name
        ("2090-01-05", 1_000_000, proposer.id, world.client),  # not staff
    ):
        with pytest.raises(DBAPIError):
            await attempt(day, limit, proposed_by, by)


# --- Races, in separate transactions ----------------------------------------------------------


async def test_racing_requests_cannot_spend_the_same_money_twice(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    world = await World(app_database, worker_database, fake_clock).build(1_000_000)

    async def attempt() -> str:
        try:
            await world.request(400_000)
        except PayoutRefused as refusal:
            return refusal.code
        return "created"

    results = sorted(await asyncio.gather(*(attempt() for _ in range(6))))
    assert results == ["created", "created", *["insufficient_funds"] * 4]
    assert await world.balance(world.holding) == 200_000
    await world.no_drift()


async def test_racing_reports_from_the_provider_finish_a_payout_once(world: World) -> None:
    payout = await world.request(1_000_000)
    await world.worker_event(payout.id, E.SEND_ACCEPTED)

    async def report(event: PayoutEvent) -> str:
        try:
            await world.worker_event(payout.id, event)
        except IllegalTransition:
            return "refused"
        return event.value

    results = await asyncio.gather(
        report(E.STATUS_PAID),
        report(E.STATUS_FAILED),
        report(E.STATUS_PAID),
        report(E.STATUS_FAILED),
    )
    assert results.count("refused") == 3
    final = (set(results) - {"refused"}).pop()
    pot, pending = await world.pot_change(), await world.pending()
    assert (pot, pending) == ((7_000_000, 0) if final == "status_paid" else (8_000_000, 1_000_000))
    assert await world.balance(payout.in_flight_account_id) == 0
    await world.no_drift()
