"""A client's own stricter withdrawal limits, set and cleared by two members of staff."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.payouts import payouts
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.money import Currency, Money
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration

NONE: dict[str, Any] = {
    "mobile_money_per_payout": None,
    "mobile_money_per_day": None,
    "bank_per_payout": None,
    "bank_per_day": None,
    "review_above": None,
}


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def propose(api: Api, client: Actor, **limits: int | None) -> httpx.Response:
    return await api.http.post(
        f"/staff/clients/{client.id}/actions",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "kind": "withdrawal_limits",
            "reason": "Asked by compliance after a monitoring alert",
            "limits": {**NONE, **limits},
        },
    )


async def approve(api: Api, action_id: str) -> httpx.Response:
    return await api.http.post(
        f"/staff/clients/actions/{action_id}/decision",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={"decision": "approved", "reason": "Checked the alert"},
    )


async def test_two_people_lower_a_clients_limits_and_the_client_sees_them(api: Api) -> None:
    client = await api.client()
    checker = await api.staff("operations")
    await api.staff("compliance")
    proposed = await propose(api, client, mobile_money_per_payout=1_000_000, bank_per_day=2_000_000)
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["limits"]["mobile_money_per_payout"] == 1_000_000
    before = (await api.http.get(f"/staff/clients/{client.id}/limits")).json()
    assert before["own"] is None and before["applied"] == before["shared"]
    assert (await approve(api, action["id"])).status_code == 403

    await api.as_staff(checker)
    inbox = (await api.http.get("/staff/approvals")).json()["items"]
    assert [item["kind"] for item in inbox if item["id"] == action["id"]] == ["client_limits"]
    assert (await approve(api, action["id"])).status_code == 200
    after = (await api.http.get(f"/staff/clients/{client.id}/limits")).json()
    assert after["own"]["mobile_money_per_payout"] == 1_000_000
    assert after["applied"]["mobile_money_per_payout"] == 1_000_000
    assert after["applied"]["bank_per_payout"] == 2_000_000
    assert after["shared"]["mobile_money_per_payout"] == 5_000_000
    assert after["can_change"] is True

    await api.as_client(client)
    seen = (await api.http.get("/client/withdrawal-limits")).json()
    assert seen["mobile_money_per_payout"] == {"minor": 1_000_000, "currency": "UGX"}
    assert seen["bank_per_day"] == {"minor": 2_000_000, "currency": "UGX"}
    async with api.database.actor_transaction(client) as session:
        limits = await payouts.current_limits(
            session, business_date(api.clock.now()), client_id=str(client.id)
        )
    assert limits.mobile_money_per_payout == Money(1_000_000, Currency.UGX)


async def test_clearing_a_clients_limits_also_takes_two_people(api: Api) -> None:
    client = await api.client()
    first, second = await api.staff("operations"), await api.staff("compliance")
    await api.as_staff(first)
    action = (await propose(api, client, review_above=100_000)).json()
    await api.as_staff(second)
    await approve(api, action["id"])
    cleared = (await propose(api, client)).json()
    await api.as_staff(first)
    assert (await approve(api, cleared["id"])).status_code == 200
    after = (await api.http.get(f"/staff/clients/{client.id}/limits")).json()
    assert after["own"] is None and after["applied"] == after["shared"]


async def test_a_client_limit_must_be_below_the_shared_one(api: Api) -> None:
    client = await api.client()
    await api.staff("operations")
    refused = await propose(api, client, mobile_money_per_payout=5_000_000)
    assert refused.status_code == 422
    assert refused.json()["error"]["fields"][0]["code"] == "not_below_shared"
    over = await propose(api, client, mobile_money_per_payout=5_000_001)
    assert over.status_code == 422
    missing = await api.http.post(
        f"/staff/clients/{client.id}/actions",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={"kind": "withdrawal_limits", "reason": "Lower them"},
    )
    assert missing.status_code == 422
    stray = await api.http.post(
        f"/staff/clients/{client.id}/actions",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={"kind": "pin_reset", "reason": "Lost phone", "limits": NONE},
    )
    assert stray.status_code == 422


async def test_support_cannot_change_limits(api: Api) -> None:
    client = await api.client()
    await api.staff("support")
    assert (await propose(api, client, bank_per_day=1_000)).status_code == 403
    page = (await api.http.get(f"/staff/clients/{client.id}/limits")).json()
    assert page["can_change"] is False


async def test_a_limit_is_never_written_without_an_approval_in_the_same_transaction(
    api: Api,
) -> None:
    client = await api.client()
    staff = await api.staff("admin")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with api.database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO payout_client_limits (id, client_id, currency, bank_per_day, "
                    "action_id, created_at) VALUES ('-Nabc0000000000000Z7', :client, 'UGX', "
                    "1000, '-Nabc0000000000000Z6', now())"
                ),
                {"client": client.id},
            )
