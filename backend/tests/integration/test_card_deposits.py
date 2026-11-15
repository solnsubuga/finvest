"""A card payment through the Pesapal adapter, the worker and the notification address.

Pesapal is a fake that keeps its own records the way the real one does: an order is unpaid
(status 0) until the payer pays on its page, notifications are unsigned and carry only the
order's IDs, and the address they come to carries our token. Everything else is real: the
database, the worker, the API and the ledger.
"""

import asyncio
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, enqueue
from app.infrastructure.pesapal import PesapalSettings
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.modules.payments import cards, service
from app.modules.payments.card_jobs import CardDepositJobs
from app.modules.payments.domain.cards import CARD_WINDOW
from app.modules.payments.domain.deposits import DepositStatus as S
from app.modules.payments.providers.pesapal import PesapalCards
from app.modules.payments.service import Deposit
from app.modules.platform_settings.domain.catalogue import CHANNELS
from app.shared.actors import SYSTEM, Actor
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import request as request_investment
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import staff as new_staff
from tests.support.payments import drain, reload
from tests.support.settings import clear_settings, set_setting

pytestmark = pytest.mark.integration

BASE = "https://pesapal.test/v3"
IPN_TOKEN = "t" * 40
PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)


@dataclass
class FakePesapal:
    orders: dict[str, dict[str, object]] = field(default_factory=dict)
    sends: int = 0
    cancels: int = 0
    registrations: int = 0
    lose_the_answer: bool = False

    def pay(self, deposit_id: str, *, amount: object | None = None) -> None:
        for order in self.orders.values():
            if order["merchant_reference"] == deposit_id:
                order["status_code"] = 1
                order["payment_method"] = "Visa"
                order["payment_account"] = "476173**0010"
                order["confirmation_code"] = "PSP" + secrets.token_hex(4)
                if amount is not None:
                    order["amount"] = amount

    def tracking_id(self, deposit_id: str) -> str:
        return next(
            reference
            for reference, order in self.orders.items()
            if order["merchant_reference"] == deposit_id
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path.removeprefix("/v3")
        if path == "/api/Auth/RequestToken":
            return httpx.Response(200, json={"token": "token", "status": "200"})
        assert request.headers["authorization"] == "Bearer token"
        if path == "/api/URLSetup/GetIpnList":
            return httpx.Response(200, json=[])
        if path == "/api/URLSetup/RegisterIPN":
            self.registrations += 1
            body = json.loads(request.content)
            assert body["url"].endswith(f"/provider-callbacks/cards/pesapal/{IPN_TOKEN}")
            return httpx.Response(200, json={"ipn_id": "ipn-1", "status": "200"})
        if path == "/api/Transactions/SubmitOrderRequest":
            self.sends += 1
            body = json.loads(request.content, parse_float=Decimal)
            assert body["notification_id"] == "ipn-1"
            reference = str(uuid.uuid4())
            self.orders[reference] = {
                "merchant_reference": body["id"],
                "amount": Decimal(body["amount"]),
                "currency": body["currency"],
                "status_code": 0,
                "payment_status_description": "INVALID",
            }
            if self.lose_the_answer:
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return httpx.Response(
                200,
                json={
                    "order_tracking_id": reference,
                    "merchant_reference": body["id"],
                    "redirect_url": f"https://pay.pesapal.test/iframe?OrderTrackingId={reference}",
                    "status": "200",
                },
            )
        if path == "/api/Transactions/GetTransactionStatus":
            order = self.orders.get(url.params["orderTrackingId"])
            if order is None:
                return httpx.Response(500, json={"status": "500"})
            return httpx.Response(
                200,
                content=json.dumps(
                    {**order, "status": "200"}, default=lambda value: float(value)
                ).encode(),
            )
        if path == "/api/Transactions/CancelOrder":
            self.cancels += 1
            order = self.orders[json.loads(request.content)["order_tracking_id"]]
            if order["status_code"] != 0:
                return httpx.Response(200, json={"status": "500"})
            order["status_code"] = 2
            order["payment_status_description"] = "Failed"
            return httpx.Response(200, json={"status": "200"})
        return httpx.Response(404)


def pesapal(fake: FakePesapal, clock: FakeClock) -> PesapalCards:
    settings = PesapalSettings.model_validate(
        {
            "consumer_key": SecretStr("key"),
            "consumer_secret": SecretStr("secret"),
            "base_url": BASE,
            "ipn_token": SecretStr(IPN_TOKEN),
        }
    )
    return PesapalCards(settings, httpx.AsyncClient(transport=httpx.MockTransport(fake)), clock)


@dataclass
class Setup:
    app: object
    identity: IdentityService
    database: Database
    worker: Worker
    fake: FakePesapal
    clock: FakeClock
    # The worker's view, for reading as the system.
    system: Database

    def http(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=self.app,  # type: ignore[arg-type]
            raise_app_exceptions=False,
            client=("127.0.0.1", 1),
        )
        return httpx.AsyncClient(transport=transport, base_url="https://api")

    async def client(self, http: httpx.AsyncClient) -> Actor:
        phone = "+2567" + "".join(secrets.choice("0123456789") for _ in range(8))
        client_id = await self.identity.create_client(phone=phone, email=None, password=PASSWORD)
        actor = Actor("client", client_id)
        async with self.database.actor_transaction(actor) as session:
            await ready_draft(session, actor, self.clock)
            check = await kyc.submit(session, SEALER, actor, ManualProvider(), self.clock)
        reviewer = new_staff()
        async with self.database.actor_transaction(reviewer) as session:
            await kyc.approve(session, SEALER, reviewer, check.id, "Documents match", self.clock)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="token")
        http.headers["Authorization"] = f"Bearer {issued.token}"
        return actor


@pytest.fixture
async def setup(
    test_database: TestDatabase,
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
) -> AsyncIterator[Setup]:
    await clear_settings(migrator_database)
    clock = FakeClock(datetime.now(UTC))
    fake = FakePesapal()
    provider = pesapal(fake, clock)
    app = create_app(
        test_database.settings("finvest_app"),
        app_database,
        clock,
        HASHER,
        card_collections=provider,
    )
    worker = Worker(
        worker_database, CardDepositJobs(worker_database, provider, clock).handlers(), clock=clock
    )
    yield Setup(app, app.state.identity, app_database, worker, fake, clock, worker_database)
    await clear_settings(migrator_database)


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"key-{push_id()}"}


async def entry_lines(database: Database, deposit_id: str) -> list[tuple[str, int]]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT a.code, l.amount_minor FROM ledger_journal_entries e "
                "JOIN ledger_lines l ON l.entry_id = e.id "
                "JOIN ledger_accounts a ON a.id = l.account_id "
                "WHERE e.source_type = 'payment.deposit' AND e.source_id = :id "
                "ORDER BY l.amount_minor"
            ),
            {"id": deposit_id},
        )
        return [(row.code, int(row.amount_minor)) for row in rows.all()]


async def until(setup: Setup, deposit: Deposit, *statuses: S) -> Deposit:
    for _ in range(80):
        await drain(setup.worker)
        current = await reload(setup.system, deposit)
        if current.status in statuses:
            return current
        setup.clock.advance(seconds=30)
    raise AssertionError(f"the deposit never reached {statuses}")


async def as_deposit(setup: Setup, payment_id: str) -> Deposit:
    async with setup.system.actor_transaction(SYSTEM) as session:
        return await service.get_deposit(session, payment_id)


async def test_a_card_payment_is_paid_on_pesapal_and_confirmed_only_by_its_status(
    setup: Setup,
) -> None:
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        offer = await http.get(f"/client/investments/{investment.id}/payments/card")
        assert offer.status_code == 200, offer.text
        # 3.5% of UGX 1,000,000 by default, in shillings.
        assert offer.json()["charge"] == {"minor": 35_000, "currency": "UGX"}
        assert offer.json()["total"] == {"minor": 1_035_000, "currency": "UGX"}
        assert offer.json()["refused"] is None

        started = await http.post(
            f"/client/investments/{investment.id}/payments/card", headers=key()
        )
        assert started.status_code == 201, started.text
        body = started.json()
        assert body["method"] == "card" and body["status"] == "created"
        assert body["payer"] is None and body["card_page_url"] is None
        await drain(setup.worker)
        waiting = (await http.get(f"/client/payments/{body['id']}")).json()
        assert waiting["status"] == "awaiting_approval"
        assert waiting["card_page_url"].startswith("https://pay.pesapal.test/")
        assert setup.fake.sends == 1 and setup.fake.registrations == 1
        deposit = await as_deposit(setup, body["id"])
        tracking = setup.fake.tracking_id(deposit.id)

        url = f"/provider-callbacks/cards/pesapal/{IPN_TOKEN}"
        notice = {
            "OrderTrackingId": tracking,
            "OrderMerchantReference": deposit.id,
            "OrderNotificationType": "IPNCHANGE",
        }
        refused = await http.get("/provider-callbacks/cards/pesapal/" + "x" * 40, params=notice)
        assert refused.status_code == 403
        # A notification that says nothing true yet: the order is unpaid, so nothing moves.
        heard = await http.get(url, params=notice)
        assert heard.status_code == 200 and heard.json()["status"] == 200
        assert heard.json()["orderTrackingId"] == tracking
        await drain(setup.worker)
        assert (await reload(setup.system, deposit)).status is S.AWAITING_APPROVAL
        assert await entry_lines(setup.system, deposit.id) == []

        setup.fake.pay(deposit.id)
        # The client is back from Pesapal's page: that asks for a read, and changes nothing.
        back = await http.post(f"/client/payments/{deposit.id}/card-return")
        assert back.status_code == 202
        settled = await until(setup, deposit, S.CONFIRMED, S.FAILED)
        assert settled.status is S.CONFIRMED
        paid = (await http.get(f"/client/payments/{deposit.id}")).json()
        assert paid["card"] == {"brand": "Visa", "last_four": "0010"}
        assert paid["card_page_url"] is None
        # One entry: Pesapal holds the whole payment, the investment gets its amount and the
        # charge the client paid is Finvest's income.
        assert await entry_lines(setup.system, deposit.id) == [
            ("client.awaiting_start", -1_000_000),
            ("payments.card_charges", -35_000),
            ("pesapal.balance", 1_035_000),
        ]
        # A repeated notification after the payment changes nothing.
        assert (await http.get(url, params=notice)).status_code == 200
        await drain(setup.worker)
        assert len(await entry_lines(setup.system, deposit.id)) == 3
    assert setup.fake.sends == 1


async def test_a_lost_answer_fails_the_payment_and_is_never_sent_again(setup: Setup) -> None:
    setup.fake.lose_the_answer = True
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/card", headers=key()
        )
        await drain(setup.worker)
        deposit = await as_deposit(setup, started.json()["id"])
        failed = await reload(setup.system, deposit)
        # Nobody has the page the payer could pay on, so it cannot have been paid.
        assert failed.status is S.FAILED and failed.code == "provider_unavailable"
        setup.clock.advance(seconds=600)
        await drain(setup.worker)
        assert setup.fake.sends == 1
        # The investment is free to be paid again.
        setup.fake.lose_the_answer = False
        again = await http.post(f"/client/investments/{investment.id}/payments/card", headers=key())
        assert again.status_code == 201, again.text


async def test_a_page_left_unpaid_is_cancelled_then_failed(setup: Setup) -> None:
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/card", headers=key()
        )
        await drain(setup.worker)
        deposit = await as_deposit(setup, started.json()["id"])
        setup.clock.advance(seconds=int(CARD_WINDOW.total_seconds()) + 60)
        await _sweep(setup)
        failed = await until(setup, deposit, S.FAILED, S.CONFIRMED)
        assert failed.status is S.FAILED and failed.code == "approval_timed_out"
        assert setup.fake.cancels == 1


async def test_a_payment_paid_for_a_different_amount_is_held_for_staff(setup: Setup) -> None:
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/card", headers=key()
        )
        await drain(setup.worker)
        deposit = await as_deposit(setup, started.json()["id"])
        setup.fake.pay(deposit.id, amount=Decimal("1035000.50"))
        await http.post(f"/client/payments/{deposit.id}/card-return")
        await drain(setup.worker)
        # A fraction of a shilling is unreadable, so nothing is decided on it.
        assert (await reload(setup.system, deposit)).status is S.AWAITING_APPROVAL
        setup.fake.pay(deposit.id, amount=Decimal(1_000_000))
        held = await until(setup, deposit, S.UNCERTAIN, S.CONFIRMED)
        assert held.status is S.UNCERTAIN and held.code == "amount_mismatch"
        assert await entry_lines(setup.system, deposit.id) == []


async def test_card_limits_and_a_paused_channel_refuse_a_new_payment(setup: Setup) -> None:
    async with setup.http() as http:
        client = await setup.client(http)
        small = await request_investment(setup.database, client, setup.clock)
        await set_setting(
            setup.database,
            "card_payments",
            {
                "charge_basis_points": 0,
                "max_payment_minor": 1_500_000,
                "max_30_days_minor": 1_900_000,
            },
            setup.clock,
        )
        offer = (await http.get(f"/client/investments/{small.id}/payments/card")).json()
        assert offer["charge"]["minor"] == 0 and offer["total"]["minor"] == 1_000_000
        assert (
            await http.post(f"/client/investments/{small.id}/payments/card", headers=key())
        ).status_code == 201
        second = await request_investment(setup.database, client, setup.clock)
        over = await http.post(f"/client/investments/{second.id}/payments/card", headers=key())
        assert over.status_code == 422
        assert over.json()["error"]["fields"][0]["code"] == "above_card_30_day_limit"
        large = await request_investment(
            setup.database, client, setup.clock, amount=Money(1_600_000, Currency.UGX)
        )
        shown = (await http.get(f"/client/investments/{large.id}/payments/card")).json()
        assert shown["refused"] == "above_card_payment_limit"

        await set_setting(
            setup.database,
            "payment_channels",
            {channel: channel != "card_pay_in" for channel in CHANNELS},
            setup.clock,
        )
        third = await request_investment(setup.database, client, setup.clock)
        paused = await http.post(f"/client/investments/{third.id}/payments/card", headers=key())
        assert paused.status_code == 503


async def test_two_card_payments_at_once_for_one_investment_start_one(setup: Setup) -> None:
    async with setup.http() as first, setup.http() as second:
        client = await setup.client(first)
        second.headers["Authorization"] = first.headers["Authorization"]
        investment = await request_investment(setup.database, client, setup.clock)
        url = f"/client/investments/{investment.id}/payments/card"
        answers = await asyncio.gather(
            first.post(url, headers=key()), second.post(url, headers=key())
        )
        assert sorted(answer.status_code for answer in answers) == [201, 409]
    async with setup.system.actor_transaction(SYSTEM) as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM payment_card_orders o JOIN payment_deposits d "
                "ON d.id = o.deposit_id WHERE d.purpose_id = :id"
            ),
            {"id": investment.id},
        )
    assert count == 1


async def test_a_card_order_cannot_be_changed_once_filled_in(
    setup: Setup, worker_database: Database
) -> None:
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/card", headers=key()
        )
        await drain(setup.worker)
    deposit_id = started.json()["id"]
    for statement in (
        "UPDATE payment_card_orders SET page_url = 'https://elsewhere.test/' "
        "WHERE deposit_id = :id",
        "UPDATE payment_card_orders SET charge_minor = 0 WHERE deposit_id = :id",
        # Only a mobile money order keeps the number that paid.
        "UPDATE payment_card_orders SET paid_from_msisdn = '+256701999999' WHERE deposit_id = :id",
        "DELETE FROM payment_card_orders WHERE deposit_id = :id",
    ):
        with pytest.raises(DBAPIError, match=r"card order|does not allow|permission denied"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": deposit_id})
    async with worker_database.actor_transaction(SYSTEM) as session:
        order = await cards.find_order(session, deposit_id)
    assert order is not None and order.charge.minor == 35_000


async def _sweep(setup: Setup) -> None:
    async with setup.system.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(kind=cards.SWEEP_KIND, idempotency_key=f"sweep-{push_id()}", payload={}),
            now=setup.clock.now(),
        )
    await drain(setup.worker)
