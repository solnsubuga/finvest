"""The app PIN confirms money leaving an investment: an early withdrawal, and the payout of a
matured investment waiting for a destination. Over HTTP, against real PostgreSQL."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.identity.api import COOKIES
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import CLIENT_ORIGIN, Api, running_api
from tests.support.investments import PRESENT, approved_destination, at, pay, request, today
from tests.support.kyc import verify

pytestmark = pytest.mark.integration

PIN = "48151"
# Past a new payout account's first day's safety wait, which starts at the real time.
LATER = at(today() + timedelta(days=2)).current


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, early_withdrawal_open=True) as running:
        yield running


def code(response: object) -> tuple[int, str]:
    status: int = response.status_code  # type: ignore[attr-defined]
    return status, str(response.json()["error"]["code"])  # type: ignore[attr-defined]


async def confirmation(api: Api) -> str:
    answer = await api.http.post("/client/pin/confirm", json={"pin": PIN, "action": "withdrawal"})
    assert answer.status_code == 200, answer.text
    token: str = answer.json()["confirmation"]
    return token


async def running(
    api: Api,
    app_database: Database,
    worker_database: Database,
    at_maturity: AtMaturity | None = None,
) -> tuple[Actor, str, str]:
    """A client with an account and a running investment, and an approved payout account."""
    client = await api.client()
    await verify(app_database, client, PRESENT)
    investment = await request(app_database, client, PRESENT, at_maturity=at_maturity)
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(
            session, investment.id, confirmed_on=today(), clock=PRESENT
        )
    destination_id = await approved_destination(app_database, client, PRESENT)
    return client, investment.id, destination_id


async def on_the_phone_with_a_pin(api: Api, client: Actor) -> None:
    await api.as_client(client)
    assert (await api.http.put("/client/pin", json={"pin": PIN})).status_code == 204


async def requested_at(api: Api, investment_id: str) -> object:
    shown = await api.http.get(f"/client/investments/{investment_id}")
    return shown.json()["withdrawal_requested_at"]


async def test_a_phone_with_a_pin_confirms_an_early_withdrawal_with_it(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    client, investment_id, destination_id = await running(api, app_database, worker_database)
    api.clock.current = LATER
    await on_the_phone_with_a_pin(api, client)
    route = f"/client/investments/{investment_id}/withdraw"
    body = {"destination_id": destination_id}

    missing = await api.http.post(route, json=body, headers={"Idempotency-Key": f"w-{push_id()}"})
    assert code(missing) == (428, "pin_required")
    assert await requested_at(api, investment_id) is None

    # A request that is refused leaves the confirmation for the next try.
    given = await confirmation(api)
    nowhere = await api.http.post(
        route,
        json={"destination_id": push_id(), "pin_confirmation": given},
        headers={"Idempotency-Key": f"w-{push_id()}"},
    )
    assert nowhere.status_code not in (200, 428)
    assert await requested_at(api, investment_id) is None

    key = f"w-{push_id()}"
    asked = await api.http.post(
        route, json={**body, "pin_confirmation": given}, headers={"Idempotency-Key": key}
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["withdrawal_requested_at"] is not None

    # The same request again answers as before and asks for nothing.
    replayed = await api.http.post(route, json=body, headers={"Idempotency-Key": key})
    assert replayed.status_code == 200
    assert replayed.json()["withdrawal_requested_at"] == asked.json()["withdrawal_requested_at"]

    # A confirmation works once.
    again = await api.http.post(
        route,
        json={**body, "pin_confirmation": given},
        headers={"Idempotency-Key": f"w-{push_id()}"},
    )
    assert code(again) == (428, "pin_required")


async def test_a_confirmation_for_another_action_does_not_withdraw(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    client, investment_id, destination_id = await running(api, app_database, worker_database)
    api.clock.current = LATER
    await on_the_phone_with_a_pin(api, client)
    answer = await api.http.post(
        "/client/pin/confirm", json={"pin": PIN, "action": "payout_account"}
    )
    refused = await api.http.post(
        f"/client/investments/{investment_id}/withdraw",
        json={"destination_id": destination_id, "pin_confirmation": answer.json()["confirmation"]},
        headers={"Idempotency-Key": f"w-{push_id()}"},
    )
    assert code(refused) == (428, "pin_required")
    assert await requested_at(api, investment_id) is None


async def test_a_browser_withdraws_early_without_a_pin(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    client, investment_id, destination_id = await running(api, app_database, worker_database)
    api.clock.current = LATER
    # The client has a PIN on their phone, which a browser never asks for.
    await on_the_phone_with_a_pin(api, client)
    issued = await api.identity.issue_session(client, second_step="passkey", transport="cookie")
    api.http.headers.pop("Authorization", None)
    api.http.cookies.set(COOKIES["client"], issued.token)
    api.http.headers["Origin"] = CLIENT_ORIGIN
    try:
        asked = await api.http.post(
            f"/client/investments/{investment_id}/withdraw",
            json={"destination_id": destination_id},
            headers={"Idempotency-Key": f"w-{push_id()}"},
        )
        assert asked.status_code == 200, asked.text
        assert asked.json()["withdrawal_requested_at"] is not None
    finally:
        api.http.cookies.clear()
        api.http.headers.pop("Origin", None)


async def test_a_phone_with_a_pin_confirms_the_payout_of_a_matured_investment(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    client, investment_id, destination_id = await running(
        api, app_database, worker_database, AtMaturity.PAY_OUT
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        ends = today() + timedelta(days=91)
        await investments.mature_investment(session, investment_id, ends, at(ends))
    api.clock.current = at(today() + timedelta(days=93)).current
    await on_the_phone_with_a_pin(api, client)
    route = f"/client/investments/{investment_id}/pay-out"

    missing = await api.http.post(route, json={"destination_id": destination_id})
    assert code(missing) == (428, "pin_required")
    shown = await api.http.get(f"/client/investments/{investment_id}")
    assert shown.json()["waiting_for_destination"] is True

    # A destination payouts refuses leaves the confirmation for the next try.
    given = await confirmation(api)
    nowhere = await api.http.post(
        route, json={"destination_id": push_id(), "pin_confirmation": given}
    )
    assert nowhere.status_code not in (200, 428)

    sent = await api.http.post(
        route, json={"destination_id": destination_id, "pin_confirmation": given}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["waiting_for_destination"] is False
