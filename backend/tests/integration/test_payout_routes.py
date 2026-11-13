"""The client's payout routes: payout accounts, proof uploads, payouts and limits."""

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from app.infrastructure.database import Database
from app.infrastructure.storage import MemoryStorage
from app.modules.payouts import destinations
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.payouts import World

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"0" * 2048
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 2048


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
async def api(
    test_database: TestDatabase, app_database: Database, storage: MemoryStorage
) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, storage=storage) as running:
        yield running


def number() -> str:
    # A fresh number each time, so tests sharing a database never hold the same account.
    return f"07{secrets.choice('0578')}{secrets.randbelow(10**7):07d}"


async def add(api: Api, **body: object) -> dict[str, object]:
    response = await api.http.post(
        "/client/payout-destinations",
        json={"kind": "mobile_money", "holder_name": "Sarah Nakato", **body},
    )
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()
    return data


async def confirmed(api: Api, actor: Actor, destination_id: str) -> None:
    """The SMS code step, which the identity module checks before this module is told."""
    async with api.database.actor_transaction(actor) as session:
        await destinations.confirm_code(session, actor, destination_id, api.clock)


async def test_a_client_adds_a_mobile_money_number_and_only_they_see_it(api: Api) -> None:
    owner = await api.client()
    phone = number()
    added = await add(api, phone=phone)
    assert added["status"] == "awaiting_code"
    assert added["kind"] == "mobile_money"
    assert added["network"] in ("mtn", "airtel")
    assert phone[-3:] in str(added["account"]) and phone[3:7] not in str(added["account"])
    assert added["payable_from"] is None

    listed = (await api.http.get("/client/payout-destinations")).json()
    assert [item["id"] for item in listed] == [added["id"]]
    again = await api.http.post(
        "/client/payout-destinations",
        json={"kind": "mobile_money", "holder_name": "Sarah Nakato", "phone": phone},
    )
    assert again.status_code == 409

    await api.client()
    assert (await api.http.get(f"/client/payout-destinations/{added['id']}")).status_code == 404
    assert (await api.http.get("/client/payout-destinations")).json() == []
    removal = await api.http.delete(f"/client/payout-destinations/{added['id']}")
    assert removal.status_code == 404

    await api.as_client(owner)
    assert (await api.http.get(f"/client/payout-destinations/{added['id']}")).status_code == 200


@pytest.mark.parametrize(
    ("body", "field", "code"),
    [
        ({"phone": "0712"}, "phone", "phone_invalid"),
        ({"phone": "0412345678"}, "phone", "phone_network_unsupported"),
        ({}, "phone", "required"),
        ({"kind": "bank", "bank_code": "stanbic"}, "account_number", "required"),
        ({"kind": "bank", "bank_code": "x!", "account_number": "1234567890"}, "bank_code", None),
    ],
)
async def test_details_that_are_not_an_account_we_can_pay_are_refused(
    api: Api, body: dict[str, object], field: str, code: str | None
) -> None:
    await api.client()
    response = await api.http.post(
        "/client/payout-destinations",
        json={"kind": "mobile_money", "holder_name": "Sarah Nakato", **body},
    )
    assert response.status_code == 422
    problems = response.json()["error"]["fields"]
    assert [problem["field_path"][-1] for problem in problems] == [field]
    if code is not None:
        assert problems[0]["code"] == code


async def test_proof_for_a_bank_account_is_stored_and_sent_to_staff(
    api: Api, storage: MemoryStorage
) -> None:
    actor = await api.client()
    account = str(10**12 + int(datetime.now(UTC).timestamp() * 1000) % 10**12)
    added = await add(api, kind="bank", bank_code="stanbic", account_number=account)
    path = f"/client/payout-destinations/{added['id']}/proof"

    early = await api.http.post(
        path, data={"proof_kind": "bank_letter"}, files={"file": ("l.pdf", PDF, "application/pdf")}
    )
    assert early.status_code == 409  # the SMS code comes first
    assert storage.files == {}

    await confirmed(api, actor, str(added["id"]))
    wrong = await api.http.post(
        path, data={"proof_kind": "bank_letter"}, files={"file": ("l.pdf", PNG, "application/pdf")}
    )
    assert wrong.status_code == 422
    assert wrong.json()["error"]["fields"][0]["code"] == "content_does_not_match_type"
    other_type = await api.http.post(
        path, data={"proof_kind": "bank_letter"}, files={"file": ("l.txt", b"hi", "text/plain")}
    )
    assert other_type.status_code == 422
    assert storage.files == {}

    sent = await api.http.post(
        path,
        data={"proof_kind": "bank_statement"},
        files={"file": ("statement.png", PNG, "image/png")},
    )
    assert sent.status_code == 200, sent.text
    assert (sent.json()["status"], sent.json()["proof_kind"]) == ("in_review", "bank_statement")
    [key] = storage.files
    assert key.startswith(f"payouts/{added['id']}/") and key.endswith("_png")
    async with api.database.actor_transaction(actor) as session:
        view = await destinations.get_destination(session, str(added["id"]))
    assert view.proof_document_id is not None and key.endswith(view.proof_document_id)

    twice = await api.http.post(
        path, data={"proof_kind": "bank_letter"}, files={"file": ("l.pdf", PDF, "application/pdf")}
    )
    assert twice.status_code == 409
    assert len(storage.files) == 1


async def test_a_mobile_money_number_takes_no_uploaded_proof(
    api: Api, storage: MemoryStorage
) -> None:
    actor = await api.client()
    added = await add(api, phone=number())
    await confirmed(api, actor, str(added["id"]))
    response = await api.http.post(
        f"/client/payout-destinations/{added['id']}/proof",
        data={"proof_kind": "bank_card"},
        files={"file": ("c.png", PNG, "image/png")},
    )
    assert response.status_code == 409
    assert response.json()["error"]["fields"][0]["code"] == "proof_not_for_this_account"
    assert storage.files == {}


async def test_a_removed_account_is_gone_for_good(api: Api) -> None:
    await api.client()
    added = await add(api, phone=number())
    path = f"/client/payout-destinations/{added['id']}"
    assert (await api.http.delete(path)).status_code == 204
    assert (await api.http.get(path)).status_code == 404
    assert (await api.http.delete(path)).status_code == 404
    assert (await api.http.get("/client/payout-destinations")).json() == []


async def test_a_client_follows_their_payouts_and_no_one_elses(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    owner = await api.client()
    world = World(app_database, worker_database, FakeClock(datetime.now(UTC)))
    world.client = owner
    await world.build(2_000_000)
    payout = await world.request(1_500_000)

    listed = (await api.http.get("/client/payouts")).json()
    assert [(item["id"], item["status"]) for item in listed] == [(payout.id, "processing")]
    one = (await api.http.get(f"/client/payouts/{payout.id}")).json()
    assert one["amount"] == {"minor": 1_500_000, "currency": "UGX"}
    assert (one["destination_id"], one["kind"]) == (world.destination_id, "mobile_money")

    await api.client()
    assert (await api.http.get(f"/client/payouts/{payout.id}")).status_code == 404
    assert (await api.http.get("/client/payouts")).json() == []


async def test_a_client_sees_the_withdrawal_limits_in_force(api: Api) -> None:
    await api.client()
    limits = (await api.http.get("/client/withdrawal-limits")).json()
    assert limits["mobile_money_per_payout"] == {"minor": 5_000_000, "currency": "UGX"}
    assert "review_above" not in limits


async def test_the_payout_routes_need_a_signed_in_client(api: Api) -> None:
    for method, path in (
        ("GET", "/client/payout-destinations"),
        ("POST", "/client/payout-destinations"),
        ("GET", "/client/payouts"),
        ("GET", "/client/withdrawal-limits"),
    ):
        response = await api.http.request(method, path)
        assert response.status_code == 401, path
    await api.staff()
    assert (await api.http.get("/client/payouts")).status_code in (401, 403)
