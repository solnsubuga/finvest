"""Paying a card payment back to the card, against real PostgreSQL, the ledger, the job queue and
the Pesapal adapter. One person proposes, a different one approves, the worker holds the money
and asks Pesapal once, and the database holds everyone to their part.

Pesapal is the fake from the card payment tests, which also takes refund requests: it answers
as its documents say (`"200"` taken, `"500"` refused), can lose the answer, and reads a paid
back order as reversed (status code 3) once its finance team has paid it.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, enqueue
from app.modules.payments import provider_refunds as refunds
from app.modules.payments import service
from app.modules.payments.card_jobs import CardDepositJobs
from app.modules.payments.domain.deposits import DepositStatus, Purpose
from app.modules.payments.domain.provider_refunds import RefundStatus, Settlement
from app.modules.payments.provider_refunds import ProviderRefundJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.integration.test_card_deposits import (
    HASHER,
    FakePesapal,
    Setup,
    as_deposit,
    key,
    pesapal,
    until,
)
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import request as request_investment
from tests.support.ledger import staff
from tests.support.payments import drain
from tests.support.settings import clear_settings

pytestmark = pytest.mark.integration

Status = RefundStatus
AMOUNT = 1_000_000
CHARGE = 35_000


@dataclass
class RefundingPesapal(FakePesapal):
    refunds: list[dict[str, object]] = field(default_factory=list)
    # "200", "500", or "lost" for an answer that never comes back.
    answer: str = "200"

    def pay_back(self, deposit_id: str) -> None:
        """Pesapal's finance team paid the refund: the order now reads as reversed."""
        self.orders[self.tracking_id(deposit_id)]["status_code"] = 3

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.removeprefix("/v3") == "/api/Transactions/RefundRequest":
            assert request.headers["authorization"] == "Bearer token"
            self.refunds.append(json.loads(request.content))
            if self.answer == "lost":
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return httpx.Response(200, json={"status": self.answer, "message": "noted"})
        return super().__call__(request)


@pytest.fixture
async def setup(
    test_database: TestDatabase,
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
) -> AsyncIterator[Setup]:
    await clear_settings(migrator_database)
    clock = FakeClock(datetime.now(UTC))
    fake = RefundingPesapal()
    provider = pesapal(fake, clock)
    app = create_app(
        test_database.settings("finvest_app"),
        app_database,
        clock,
        HASHER,
        card_collections=provider,
    )
    handlers = {
        **CardDepositJobs(worker_database, provider, clock).handlers(),
        **ProviderRefundJobs(worker_database, provider, clock).handlers(),
    }
    worker = Worker(worker_database, handlers, clock=clock)
    yield Setup(app, app.state.identity, app_database, worker, fake, clock, worker_database)
    await clear_settings(migrator_database)


def fake(setup: Setup) -> RefundingPesapal:
    assert isinstance(setup.fake, RefundingPesapal)
    return setup.fake


async def paid_by_card(setup: Setup) -> service.Deposit:
    """A client's card payment of UGX 1,000,000 and the 3.5% charge, confirmed and waiting."""
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/card", headers=key()
        )
        assert started.status_code == 201, started.text
        await drain(setup.worker)
        deposit = await as_deposit(setup, started.json()["id"])
        setup.fake.pay(deposit.id)
        confirmed = await until(setup, deposit, DepositStatus.CONFIRMED, DepositStatus.FAILED)
    assert confirmed.status is DepositStatus.CONFIRMED
    return confirmed


async def propose(
    setup: Setup, maker: Actor, deposit_id: str, *, erred: bool = False
) -> refunds.Refund:
    async with setup.database.actor_transaction(maker) as session:
        return await refunds.propose_refund(
            session,
            maker,
            deposit_id=deposit_id,
            reason="The client asked to cancel before the investment started",
            finvest_erred=erred,
            idempotency_key=f"propose-{push_id()}",
            clock=setup.clock,
        )


async def approve(setup: Setup, checker: Actor, refund_id: str) -> refunds.Refund:
    async with setup.database.actor_transaction(checker) as session:
        return await refunds.approve_refund(
            session, checker, refund_id, idempotency_key=f"approve-{push_id()}", clock=setup.clock
        )


async def find(setup: Setup, who: Actor, refund_id: str, found: Settlement) -> refunds.Refund:
    async with setup.database.actor_transaction(who) as session:
        return await refunds.record_finding(
            session,
            who,
            refund_id,
            found,
            "Seen on the Pesapal dashboard today",
            idempotency_key=f"find-{push_id()}",
            clock=setup.clock,
        )


async def refund_now(setup: Setup, refund_id: str) -> refunds.Refund:
    async with setup.system.actor_transaction(SYSTEM) as session:
        return await refunds._refund(session, refund_id)


async def read_refunds(setup: Setup) -> None:
    async with setup.system.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(kind=refunds.READ_KIND, idempotency_key=f"read-{push_id()}", payload={}),
            now=setup.clock.now(),
        )
    await drain(setup.worker)


async def balance(setup: Setup, code: str, owner: str | None = None) -> int:
    """The sum of every line on accounts with this code, as debits less credits."""
    async with setup.system.actor_transaction(SYSTEM) as session:
        total = await session.scalar(
            text(
                "SELECT coalesce(sum(l.amount_minor), 0) FROM ledger_lines l "
                "JOIN ledger_accounts a ON a.id = l.account_id WHERE a.code = :code "
                "AND (CAST(:owner AS text) IS NULL OR a.owner_client_id = :owner)"
            ),
            {"code": code, "owner": owner},
        )
    return int(total or 0)


async def live(setup: Setup, investment_id: str) -> bool:
    async with setup.system.actor_transaction(SYSTEM) as session:
        return await service.has_live_deposit(
            session, purpose=Purpose.INVESTMENT, purpose_id=investment_id
        )


async def notices(setup: Setup, deposit: service.Deposit) -> int:
    async with setup.system.actor_transaction(SYSTEM) as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM notifications WHERE client_id = :client AND link_id = :id "
                "AND event = 'payment_refunded'"
            ),
            {"client": deposit.client_id, "id": deposit.id},
        )
    return int(count or 0)


async def test_two_people_pay_a_card_payment_back_with_the_charge_when_finvest_erred(
    setup: Setup,
) -> None:
    deposit = await paid_by_card(setup)
    due = await balance(setup, refunds.DUE_CODE)
    client = deposit.client_id
    pesapal_before = await balance(setup, "pesapal.balance")
    charges_before = await balance(setup, "payments.card_charges")
    maker, checker = staff(), staff()

    proposed = await propose(setup, maker, deposit.id, erred=True)
    assert (proposed.status, proposed.total.minor, proposed.charge.minor) == (
        Status.PROPOSED,
        AMOUNT + CHARGE,
        CHARGE,
    )
    with pytest.raises(ApplicationError) as own:
        await approve(setup, maker, proposed.id)
    assert own.value.code == "forbidden"
    with pytest.raises(ApplicationError) as twice:
        await propose(setup, staff(), deposit.id)
    assert twice.value.code == "conflict"

    await approve(setup, checker, proposed.id)
    await drain(setup.worker)
    requested = await refund_now(setup, proposed.id)
    assert requested.status is Status.REQUESTED
    assert fake(setup).refunds == [
        {
            "confirmation_code": fake(setup).orders[fake(setup).tracking_id(deposit.id)][
                "confirmation_code"
            ],
            "amount": AMOUNT + CHARGE,
            "username": "Finvest",
            "remarks": refunds.REMARKS,
        }
    ]
    # The money left the investment and is due to the card; Pesapal has not paid it yet.
    assert await balance(setup, "client.awaiting_start", client) == 0
    assert await balance(setup, "payments.card_charges") == charges_before + CHARGE
    assert await balance(setup, refunds.DUE_CODE) - due == -(AMOUNT + CHARGE)
    assert await balance(setup, "pesapal.balance") == pesapal_before
    assert await notices(setup, deposit) == 1
    # Still the investment's payment until it is paid back: the client cannot pay twice.
    assert await live(setup, deposit.purpose_id)

    await read_refunds(setup)
    assert (await refund_now(setup, proposed.id)).status is Status.REQUESTED
    fake(setup).pay_back(deposit.id)
    await read_refunds(setup)
    done = await refund_now(setup, proposed.id)
    assert done.status is Status.DONE and done.done_entry_id is not None
    assert await balance(setup, refunds.DUE_CODE) - due == 0
    assert await balance(setup, "pesapal.balance") == pesapal_before - (AMOUNT + CHARGE)
    after = await as_deposit(setup, deposit.id)
    assert after.provider_refund_id == done.id and after.status is DepositStatus.CONFIRMED
    assert not await live(setup, deposit.purpose_id)
    # Asked once, whatever happened since.
    await read_refunds(setup)
    assert len(fake(setup).refunds) == 1


async def test_without_finvests_mistake_only_the_investment_goes_back(setup: Setup) -> None:
    deposit = await paid_by_card(setup)
    due = await balance(setup, refunds.DUE_CODE)
    charges_before = await balance(setup, "payments.card_charges")
    proposed = await propose(setup, staff(), deposit.id)
    await approve(setup, staff(), proposed.id)
    await drain(setup.worker)
    assert fake(setup).refunds[-1]["amount"] == AMOUNT
    assert await balance(setup, "payments.card_charges") == charges_before
    assert await balance(setup, refunds.DUE_CODE) - due == -AMOUNT


async def test_a_refused_refund_puts_the_money_back_for_the_investment(setup: Setup) -> None:
    deposit = await paid_by_card(setup)
    due = await balance(setup, refunds.DUE_CODE)
    fake(setup).answer = "500"
    proposed = await propose(setup, staff(), deposit.id, erred=True)
    await approve(setup, staff(), proposed.id)
    await drain(setup.worker)
    failed = await refund_now(setup, proposed.id)
    assert (failed.status, failed.failure_code) == (Status.FAILED, "provider_refused")
    assert failed.release_entry_id is not None
    assert await balance(setup, "client.awaiting_start", deposit.client_id) == -AMOUNT
    assert await balance(setup, refunds.DUE_CODE) - due == 0
    assert await notices(setup, deposit) == 0
    # The payment is the investment's again, and may be proposed again.
    assert await live(setup, deposit.purpose_id)
    assert (await propose(setup, staff(), deposit.id)).status is Status.PROPOSED


async def test_a_lost_answer_is_never_asked_again_and_staff_say_what_pesapal_shows(
    setup: Setup,
) -> None:
    deposit = await paid_by_card(setup)
    due = await balance(setup, refunds.DUE_CODE)
    fake(setup).answer = "lost"
    maker = staff()
    proposed = await propose(setup, maker, deposit.id)
    await approve(setup, staff(), proposed.id)
    await drain(setup.worker)
    assert (await refund_now(setup, proposed.id)).status is Status.UNCERTAIN
    setup.clock.advance(seconds=3600)
    await read_refunds(setup)
    assert len(fake(setup).refunds) == 1
    # The money stays held while nobody knows.
    assert await balance(setup, refunds.DUE_CODE) - due == -AMOUNT

    with pytest.raises(ApplicationError) as own:
        await find(setup, maker, proposed.id, Settlement.PAID)
    assert own.value.code == "forbidden"
    checker = staff()
    await find(setup, checker, proposed.id, Settlement.REQUESTED)
    await drain(setup.worker)
    assert (await refund_now(setup, proposed.id)).status is Status.REQUESTED
    assert await notices(setup, deposit) == 1
    await find(setup, staff(), proposed.id, Settlement.PAID)
    await drain(setup.worker)
    done = await refund_now(setup, proposed.id)
    assert done.status is Status.DONE
    assert (await as_deposit(setup, deposit.id)).provider_refund_id == done.id
    with pytest.raises(ApplicationError) as finished:
        await find(setup, staff(), proposed.id, Settlement.NOT_PAID)
    assert finished.value.code == "conflict"


async def test_staff_finding_no_refund_releases_the_money(setup: Setup) -> None:
    deposit = await paid_by_card(setup)
    fake(setup).answer = "lost"
    proposed = await propose(setup, staff(), deposit.id)
    await approve(setup, staff(), proposed.id)
    await drain(setup.worker)
    await find(setup, staff(), proposed.id, Settlement.NOT_PAID)
    await drain(setup.worker)
    failed = await refund_now(setup, proposed.id)
    assert (failed.status, failed.failure_code) == (Status.FAILED, "not_paid_by_provider")
    assert await balance(setup, "client.awaiting_start", deposit.client_id) == -AMOUNT


async def test_two_approvals_at_once_send_one_refund(setup: Setup) -> None:
    deposit = await paid_by_card(setup)
    proposed = await propose(setup, staff(), deposit.id)
    answers = await asyncio.gather(
        approve(setup, staff(), proposed.id),
        approve(setup, staff(), proposed.id),
        return_exceptions=True,
    )
    failures = [answer for answer in answers if isinstance(answer, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], ApplicationError) and failures[0].code == "conflict"
    await drain(setup.worker)
    assert len(fake(setup).refunds) == 1


async def test_two_proposals_at_once_make_one(setup: Setup) -> None:
    deposit = await paid_by_card(setup)
    answers = await asyncio.gather(
        propose(setup, staff(), deposit.id),
        propose(setup, staff(), deposit.id),
        return_exceptions=True,
    )
    failures = [answer for answer in answers if isinstance(answer, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], ApplicationError) and failures[0].code == "conflict"


async def test_the_database_holds_everyone_to_their_part(setup: Setup) -> None:
    deposit = await paid_by_card(setup)
    maker = staff()
    proposed = await propose(setup, maker, deposit.id)

    # Staff cannot skip the worker, mark it done, or decide their own proposal.
    for statement, who in (
        (
            "UPDATE payment_provider_refunds SET status = 'approved', decided_by = :me, "
            "decided_at = now() WHERE id = :id",
            maker,
        ),
        ("UPDATE payment_provider_refunds SET status = 'done' WHERE id = :id", staff()),
        ("UPDATE payment_provider_refunds SET total_minor = 1 WHERE id = :id", staff()),
        ("DELETE FROM payment_provider_refunds WHERE id = :id", staff()),
    ):
        with pytest.raises(DBAPIError, match=r"refund|permission denied|violates|not allow"):
            async with setup.database.actor_transaction(who) as session:
                await session.execute(text(statement), {"id": proposed.id, "me": who.id})
    # Nor may the worker skip the hold, or a deposit be marked refunded by a refund not done.
    for statement in (
        "UPDATE payment_provider_refunds SET status = 'done', done_at = now() WHERE id = :id",
        "UPDATE payment_deposits SET provider_refund_id = :id WHERE id = :deposit",
    ):
        with pytest.raises(DBAPIError, match=r"refund|violates"):
            async with setup.system.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": proposed.id, "deposit": deposit.id})
    # A refund names the deposit's own terms, and a payment it did not take is refused.
    with pytest.raises(DBAPIError, match="a refund starts as proposed"):
        async with setup.database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "INSERT INTO payment_provider_refunds (id, deposit_id, client_id, "
                    "investment_id, provider, currency, amount_minor, charge_minor, "
                    "finvest_erred, total_minor, reason, proposed_by, proposed_at) VALUES (:id, "
                    ":deposit, :client, :investment, 'pesapal', 'UGX', 1, 35000, false, 1, "
                    "'x', :me, now())"
                ),
                {
                    "id": push_id(),
                    "deposit": deposit.id,
                    "client": deposit.client_id,
                    "investment": deposit.purpose_id,
                    "me": maker.id,
                },
            )
