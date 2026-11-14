"""A mobile money deposit through the ioTec adapter, the worker and the callback route.

ioTec is a fake that keeps its own records the way the real one does: a collection is
`Pending` until the payer approves, it can be found by our deposit ID, and callbacks carry the
token set in ioTec's portal. Everything else is real: the database, the worker and the API.
"""

import json
import uuid
from dataclasses import dataclass, field

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.iotec import IotecSettings
from app.infrastructure.jobs import Worker
from app.modules.payments.domain.deposits import DepositStatus as S
from app.modules.payments.jobs import DepositJobs
from app.modules.payments.providers.iotec import IotecCollections
from app.modules.payments.service import Deposit
from app.shared.actors import SYSTEM
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.payments import drain, phone, reload, start_deposit

pytestmark = pytest.mark.integration

PAY_URL = "https://pay.iotec.test"
TOKEN_URL = "https://id.iotec.test/connect/token"
CALLBACK_TOKEN = "callback-token-for-tests"


@dataclass
class FakeIotec:
    collections: dict[str, dict[str, object]] = field(default_factory=dict)
    sends: int = 0
    lose_the_answer: bool = False

    def approve(self) -> None:
        for record in self.collections.values():
            record["status"] = "Success"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        assert request.headers["authorization"] == "Bearer token"
        if request.method == "POST" and url == f"{PAY_URL}/api/collections/collect":
            self.sends += 1
            body = json.loads(request.content)
            # Tests share one database, and a provider reference is unique there.
            reference = str(uuid.uuid4())
            record = {
                "id": reference,
                "status": "Pending",
                "externalId": body["externalId"],
                "amount": body["amount"],
                "currency": body["currency"],
            }
            self.collections[reference] = record
            if self.lose_the_answer:
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return httpx.Response(200, json=record)
        for record in self.collections.values():
            if url in (
                f"{PAY_URL}/api/collections/status/{record['id']}",
                f"{PAY_URL}/api/collections/external-id/{record['externalId']}",
            ):
                return httpx.Response(200, json=record)
        return httpx.Response(404)


def iotec(fake: FakeIotec, clock: FakeClock) -> IotecCollections:
    settings = IotecSettings.model_validate(
        {
            "client_id": "client",
            "client_secret": SecretStr("secret"),
            "wallet_id": "5e83b187-801e-410e-b76e-f491928547e0",
            "token_url": TOKEN_URL,
            "pay_url": PAY_URL,
            "callback_token": SecretStr(CALLBACK_TOKEN),
        }
    )
    return IotecCollections(settings, httpx.AsyncClient(transport=httpx.MockTransport(fake)), clock)


async def entries(database: Database, deposit: Deposit) -> int:
    async with database.actor_transaction(SYSTEM) as session:
        found = await session.execute(
            text(
                "SELECT count(*) FROM ledger_journal_entries "
                "WHERE source_type = 'payment.deposit' AND source_id = :id"
            ),
            {"id": deposit.id},
        )
        return int(found.scalar_one())


async def until_settled(
    worker: Worker, database: Database, clock: FakeClock, deposit: Deposit
) -> Deposit:
    for _ in range(40):
        await drain(worker)
        current = await reload(database, deposit)
        if current.status in (S.CONFIRMED, S.FAILED):
            return current
        clock.advance(seconds=30)
    raise AssertionError("the deposit never settled")


async def test_a_deposit_is_collected_through_iotec_and_a_callback_only_hurries_the_read(
    app_database: Database,
    worker_database: Database,
    test_database: TestDatabase,
    fake_clock: FakeClock,
) -> None:
    fake = FakeIotec()
    provider = iotec(fake, fake_clock)
    worker = Worker(
        worker_database,
        DepositJobs(worker_database, provider, fake_clock).handlers(),
        clock=fake_clock,
    )
    app = create_app(
        test_database.settings("finvest_app"), app_database, fake_clock, collections=provider
    )
    deposit = await start_deposit(app_database, fake_clock, msisdn=phone(), provider="iotec")
    await drain(worker)
    waiting = await reload(worker_database, deposit)
    assert waiting.status is S.AWAITING_APPROVAL
    assert waiting.provider_reference in fake.collections

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as api:
        url = "/provider-callbacks/collections/iotec"
        claim = json.dumps(
            {"id": waiting.provider_reference, "status": "Success", "externalId": deposit.id}
        ).encode()
        assert (await api.post(url, content=claim)).status_code == 403
        assert (
            await api.post(url, content=claim, headers={"Authorization": "Bearer wrong"})
        ).status_code == 403
        # The callback says paid, ioTec's own record does not yet: nothing is posted.
        good = {"Authorization": f"Bearer {CALLBACK_TOKEN}"}
        assert (await api.post(url, content=claim, headers=good)).status_code == 204
        await drain(worker)
        assert (await reload(worker_database, deposit)).status is S.AWAITING_APPROVAL
        assert await entries(worker_database, deposit) == 0

        fake.approve()
        settled = await until_settled(worker, worker_database, fake_clock, deposit)
    assert settled.status is S.CONFIRMED
    assert await entries(worker_database, deposit) == 1
    assert fake.sends == 1


async def test_a_lost_answer_is_never_sent_again_and_is_found_by_the_deposit_id(
    worker_database: Database, app_database: Database, fake_clock: FakeClock
) -> None:
    fake = FakeIotec(lose_the_answer=True)
    provider = iotec(fake, fake_clock)
    worker = Worker(
        worker_database,
        DepositJobs(worker_database, provider, fake_clock).handlers(),
        clock=fake_clock,
    )
    deposit = await start_deposit(app_database, fake_clock, msisdn=phone(), provider="iotec")
    await drain(worker)
    uncertain = await reload(worker_database, deposit)
    assert uncertain.status is S.UNCERTAIN and uncertain.provider_reference is None

    fake.approve()
    settled = await until_settled(worker, worker_database, fake_clock, deposit)
    assert settled.status is S.CONFIRMED
    assert await entries(worker_database, deposit) == 1
    # ioTec took the request once. It was read back, never sent again.
    assert fake.sends == 1
