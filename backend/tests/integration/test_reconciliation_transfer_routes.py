"""Staff record and approve transfers between pots through the HTTP routes."""

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from httpx import Response

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.reconciliation.domain import transfers as rules
from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

ROUTE = "/staff/reconciliation/pot-transfers"
MTN = {"source": "iotec", "account_reference": "mtn"}
BANK = {"source": "bank", "account_reference": "trust"}


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = datetime.now(UTC)
        yield running


def key() -> dict[str, str]:
    return {"Idempotency-Key": push_id()}


def code(response: Response) -> str:
    error = response.json()["error"]
    fields = error.get("fields") or []
    return str(fields[0]["code"] if fields else error["code"])


def today(api: Api) -> date:
    return business_date(api.clock.now())


def sweep(api: Api, *, sent: int = 500_000, received: int = 495_000) -> dict[str, object]:
    return {
        "from_pot": MTN,
        "to_pot": BANK,
        "sent": {"minor": sent, "currency": "UGX"},
        "received": {"minor": received, "currency": "UGX"},
        "value_date": today(api).isoformat(),
        "provider_reference": f"IOT-{secrets.token_hex(6)}",
        "reason": "Daily sweep of collected mobile money",
    }


async def fund_wallet(worker_database: Database, api: Api, amount: int) -> None:
    """Money collected into the MTN wallet, posted as the worker, so a sweep has money to move."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        wallet = await ledger.open_account(
            session, rules.find_pot(StatementSource.IOTEC, "mtn").ledger_account, api.clock
        )
        owed = await ledger.open_account(
            session,
            AccountSpec("test.owed", AccountKind.LIABILITY, Currency.UGX, "Owed"),
            api.clock,
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.collected",
                effective_date=today(api),
                lines=(debit(wallet.id, ugx(amount)), credit(owed.id, ugx(amount))),
                idempotency_key=push_id(),
            ),
            api.clock,
        )


async def test_two_members_of_staff_record_and_approve_a_sweep(
    api: Api, worker_database: Database
) -> None:
    await fund_wallet(worker_database, api, 500_000)
    await api.staff("finance", name="Daniel Opio")
    body = sweep(api)
    headers = key()
    recorded = await api.http.post(ROUTE, json=body, headers=headers)
    assert recorded.status_code == 201, recorded.text
    transfer = recorded.json()
    assert transfer["status"] == "proposed"
    assert transfer["fee"] == {"minor": 5_000, "currency": "UGX"}
    assert transfer["ledger_entry_id"] is None

    # The same request again, as after a lost answer, is the same transfer.
    again = await api.http.post(ROUTE, json=body, headers=headers)
    assert (again.status_code, again.json()["id"]) == (201, transfer["id"])
    # The same key for a different transfer is refused.
    other = await api.http.post(ROUTE, json=sweep(api), headers=headers)
    assert (other.status_code, code(other)) == (422, "idempotency_key_reused")

    # The person who recorded it cannot approve it.
    own = await api.http.post(f"{ROUTE}/{transfer['id']}/approve", headers=key())
    assert (own.status_code, code(own)) == (403, "transfer_needs_second_person")

    await api.staff("admin", name="Grace Auma")
    waiting = await api.http.get(ROUTE)
    assert transfer["id"] in [each["id"] for each in waiting.json()]
    approve_key = key()
    approved = await api.http.post(f"{ROUTE}/{transfer['id']}/approve", headers=approve_key)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["ledger_entry_id"] is not None
    repeated = await api.http.post(f"{ROUTE}/{transfer['id']}/approve", headers=approve_key)
    assert repeated.json()["ledger_entry_id"] == approved.json()["ledger_entry_id"]
    twice = await api.http.post(f"{ROUTE}/{transfer['id']}/approve", headers=key())
    assert (twice.status_code, code(twice)) == (409, "transfer_already_decided")

    one = await api.http.get(f"{ROUTE}/{transfer['id']}")
    assert one.json()["status"] == "approved"
    after = await api.http.get(ROUTE)
    assert transfer["id"] not in [each["id"] for each in after.json()]


async def test_a_second_person_declines_a_transfer_with_a_reason(api: Api) -> None:
    await api.staff("finance")
    recorded = await api.http.post(ROUTE, json=sweep(api), headers=key())
    transfer_id = recorded.json()["id"]
    await api.staff("admin")
    blank = await api.http.post(
        f"{ROUTE}/{transfer_id}/decline", json={"reason": ""}, headers=key()
    )
    assert blank.status_code == 422
    declined = await api.http.post(
        f"{ROUTE}/{transfer_id}/decline", json={"reason": "Not on the bank statement"},
        headers=key(),
    )  # fmt: skip
    assert declined.status_code == 200, declined.text
    assert (declined.json()["status"], declined.json()["ledger_entry_id"]) == ("declined", None)


async def test_what_the_rules_refuse_is_named(api: Api) -> None:
    await api.staff("finance")
    same = {**sweep(api), "to_pot": MTN}
    refused = await api.http.post(ROUTE, json=same, headers=key())
    assert (refused.status_code, code(refused)) == (422, "transfer_same_pot")
    more = sweep(api, sent=100, received=200)
    refused = await api.http.post(ROUTE, json=more, headers=key())
    assert (refused.status_code, code(refused)) == (422, "transfer_received_more_than_sent")
    bank_fee = {**sweep(api), "from_pot": BANK, "to_pot": MTN}
    refused = await api.http.post(ROUTE, json=bank_fee, headers=key())
    assert (refused.status_code, code(refused)) == (422, "transfer_fee_not_allowed")
    unknown = {**sweep(api), "to_pot": {"source": "bank", "account_reference": "nowhere"}}
    refused = await api.http.post(ROUTE, json=unknown, headers=key())
    assert (refused.status_code, code(refused)) == (422, "pot_unknown")
    body = sweep(api)
    assert (await api.http.post(ROUTE, json=body, headers=key())).status_code == 201
    repeated = await api.http.post(ROUTE, json=body, headers=key())
    assert (repeated.status_code, code(repeated)) == (409, "transfer_already_recorded")
    missing = await api.http.post(ROUTE, json=sweep(api))
    assert missing.status_code == 428


async def test_an_approval_the_pot_cannot_cover_is_refused(api: Api) -> None:
    await api.staff("finance")
    # Far more than any test has collected into the MTN wallet.
    recorded = await api.http.post(
        ROUTE, json=sweep(api, sent=9 * 10**14, received=9 * 10**14), headers=key()
    )
    transfer_id = recorded.json()["id"]
    await api.staff("admin")
    refused = await api.http.post(f"{ROUTE}/{transfer_id}/approve", headers=key())
    assert (refused.status_code, code(refused)) == (409, "transfer_more_than_the_pot_holds")
    still = await api.http.get(f"{ROUTE}/{transfer_id}")
    assert still.json()["status"] == "proposed"


async def test_seeing_transfers_is_not_enough_to_record_one(api: Api) -> None:
    await api.staff("support")
    assert (await api.http.get(ROUTE)).status_code == 403
    await api.staff("operations")
    assert (await api.http.get(ROUTE)).status_code == 200
    refused = await api.http.post(ROUTE, json=sweep(api), headers=key())
    assert refused.status_code == 403
