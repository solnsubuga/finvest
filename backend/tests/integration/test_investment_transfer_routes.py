"""Sending an investment to another client over HTTP, with JSON as the client app sends it.

The service has its own tests in test_investment_transfers.py. These post the real request body,
so a request model that refuses what the app sends is caught here.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.modules.investments import transfers
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.investments.domain.transfers import TransferStatus
from app.shared.actors import SYSTEM, Actor
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import PRESENT, pay, request, today
from tests.support.kyc import verify
from tests.support.ledger import key, ugx

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        # Investments are stamped against the database clock, so these tests run at real time.
        running.clock.current = datetime.now(UTC)
        yield running


async def verified_client(api: Api) -> Actor:
    client = await api.client()
    await verify(api.database, client, PRESENT)
    return client


async def number_of(app_database: Database, client: Actor) -> str:
    async with app_database.actor_transaction(client) as session:
        found = await session.scalar(
            text("SELECT account_number FROM kyc_checks WHERE client_id = :id"), {"id": client.id}
        )
    assert isinstance(found, str)
    return found


async def running(
    app_database: Database, worker_database: Database, client: Actor, amount: int
) -> str:
    investment = await request(app_database, client, PRESENT, amount=ugx(amount))
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        started = await investments.start_investment(
            session, investment.id, confirmed_on=today(), clock=PRESENT
        )
    return started.id


async def test_a_client_sends_part_of_an_investment_with_the_app_s_request(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    recipient = await verified_client(api)
    sender = await verified_client(api)
    investment_id = await running(app_database, worker_database, sender, 5_000_000)
    number = await number_of(app_database, recipient)

    found = await api.http.get("/client/transfer-recipients", params={"to": number})
    assert found.status_code == 200, found.text
    assert found.json() == {"account_number": number, "name": "Amara N."}

    body = {
        "to_account_number": number,
        "amount_minor": 1_000_000,
        "currency": "UGX",
        "message": "For the wedding",
        "purpose": "gift",
        "purpose_note": None,
        "whole": False,
    }
    idempotency_key = key()
    sent = await api.http.post(
        f"/client/investments/{investment_id}/send",
        json=body,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert sent.status_code == 201, sent.text
    transfer = sent.json()
    assert transfer["direction"] == "sent"
    assert transfer["other_name"] == "Amara N."
    assert transfer["status"] == TransferStatus.WAITING.value
    assert (transfer["amount_minor"], transfer["currency"]) == (1_000_000, "UGX")
    assert transfer["investment_id"] == investment_id
    # The reason is for staff only.
    assert "purpose" not in transfer

    # The app retrying with the same key gets the same transfer back.
    again = await api.http.post(
        f"/client/investments/{investment_id}/send",
        json=body,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] == transfer["id"]

    async with worker_database.actor_transaction(SYSTEM) as session:
        done = await transfers.carry_out(session, transfer["id"], PRESENT)
        closed = await investments.get_investment(session, investment_id)
    assert done.status is TransferStatus.DONE
    assert closed.status is InvestmentStatus.TRANSFERRED

    listed = await api.http.get("/client/transfers")
    assert [(t["id"], t["status"]) for t in listed.json()] == [
        (transfer["id"], TransferStatus.DONE.value)
    ]
    await api.as_client(recipient)
    received = await api.http.get("/client/transfers")
    assert [(t["direction"], t["investment_id"]) for t in received.json()] == [
        ("received", done.received_investment_id)
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [("currency", "XYZ"), ("purpose", "not_a_reason"), ("amount_minor", "1000000")],
)
async def test_the_send_request_still_refuses_what_is_not_allowed(
    api: Api, app_database: Database, worker_database: Database, field: str, value: object
) -> None:
    recipient = await verified_client(api)
    sender = await verified_client(api)
    investment_id = await running(app_database, worker_database, sender, 5_000_000)
    number = await number_of(app_database, recipient)
    body: dict[str, object] = {
        "to_account_number": number,
        "amount_minor": 1_000_000,
        "currency": "UGX",
        "purpose": "gift",
        field: value,
    }
    refused = await api.http.post(
        f"/client/investments/{investment_id}/send",
        json=body,
        headers={"Idempotency-Key": key()},
    )
    assert refused.status_code == 422, refused.text
