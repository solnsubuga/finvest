"""The Overview's figures follow the ledger and the investments, by the reader's role."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import started
from tests.support.ledger import client, key, ugx

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = datetime.now(UTC)
        yield running


async def read(api: Api) -> dict[str, Any]:
    response = await api.http.get("/staff/overview")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def principal(body: dict[str, Any], code: str) -> int:
    return sum(h["principal"]["minor"] for h in body["holdings"] if h["product_code"] == code)


def mtn(body: dict[str, Any]) -> int:
    return next(int(f["balance"]["minor"]) for f in body["floats"] if f["network"] == "mtn")


async def test_a_deposit_and_an_investment_move_the_figures(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    await api.staff("admin")
    before = await read(api)
    assert len(before["money"]["days"]) == 7
    assert before["money"]["days"][-1]["day"] == before["today"]
    assert len(before["maturities"]) == 4

    # A mobile money deposit lands in the MTN wallet and waits for its investment.
    payer = client()
    async with worker_database.actor_transaction(SYSTEM) as session:
        wallet = await ledger.open_account(
            session,
            AccountSpec(
                "iotec.wallet", AccountKind.ASSET, ugx(0).currency, "ioTec wallet", reference="mtn"
            ),
            api.clock,
        )
        awaiting = await ledger.open_account(
            session,
            AccountSpec(
                "client.awaiting_start",
                AccountKind.LIABILITY,
                ugx(0).currency,
                "Paid in, awaiting start",
                owner_client_id=payer.id,
                reference=push_id(),
            ),
            api.clock,
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="payment.deposit_confirmed",
                effective_date=business_date(api.clock.now()),
                lines=(debit(wallet.id, ugx(2_000_000)), credit(awaiting.id, ugx(2_000_000))),
                idempotency_key=key(),
                source_type="payment.deposit",
                source_id=push_id(),
            ),
            api.clock,
        )
    # And another client starts a 91 day bill.
    await started(app_database, worker_database, api.clock, amount=ugx(1_000_000))

    after = await read(api)
    money, was = after["money"], before["money"]
    assert (
        money["days"][-1]["money_in"]["minor"] - was["days"][-1]["money_in"]["minor"] == 2_000_000
    )
    assert money["awaiting_start"]["minor"] - was["awaiting_start"]["minor"] == 2_000_000
    assert money["client_money_held"]["minor"] - was["client_money_held"]["minor"] == 3_000_000
    assert mtn(after) - mtn(before) == 2_000_000
    assert principal(after, "TREA-91") - principal(before, "TREA-91") == 1_000_000
    assert money["clients_invested"] == was["clients_invested"] + 1


async def test_support_sees_only_what_needs_attention(api: Api) -> None:
    await api.staff("support")
    body = await read(api)
    for section in ("money", "floats", "balancing", "maturities", "holdings"):
        assert body[section] is None
    assert {item["code"] for item in body["attention"]} <= {"approvals_for_you"}


async def test_a_dollar_overview_has_no_mobile_money_floats(api: Api) -> None:
    await api.staff("finance")
    response = await api.http.get("/staff/overview", params={"currency": "USD"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currency"] == "USD"
    assert body["floats"] is None
    assert body["money"]["client_money_held"]["currency"] == "USD"
