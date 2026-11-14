"""Investing over HTTP, against real PostgreSQL as finvest_app: who may call what, and that
clients and the public see figures after tax only."""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.api import COOKIES
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.investments import service as investments
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.investments import PRESENT, pay, today
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
STAFF_HEADERS = {"Origin": STAFF_ORIGIN}
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    database: Database


@asynccontextmanager
async def serving(
    test_database: TestDatabase, app_database: Database, **settings_changed: object
) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
            "trusted_proxies": ["127.0.0.1"],
            **settings_changed,
        }
    )
    # The real time: the database refuses an investment that is not created now.
    app = create_app(settings, app_database, PRESENT, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, app_database)


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with serving(test_database, app_database) as served:
        yield served


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def signed_in_client(api: Api, *, approved: bool = True) -> tuple[str, dict[str, str]]:
    """A client with an account, an approved identity check, and a bearer token."""
    phone = f"+2567{secrets.randbelow(10**8):08d}"
    client_id = await api.identity.create_client(phone=phone, email=None, password=PASSWORD)
    client = Actor("client", client_id)
    if approved:
        async with api.database.actor_transaction(client) as session:
            await ready_draft(session, client, PRESENT)
            check = await kyc.submit(session, SEALER, client, ManualProvider(), PRESENT)
        checker = new_staff()
        async with api.database.actor_transaction(checker) as session:
            await kyc.approve(session, SEALER, checker, check.id, "Documents match", PRESENT)
    issued = await api.identity.issue_session(client, second_step="passkey", transport="token")
    return client_id, {"Authorization": f"Bearer {issued.token}"}


async def signed_in_staff(api: Api, role: str = "admin") -> tuple[str, dict[str, str]]:
    staff_id = await api.identity.invite_staff(
        email=f"staff{secrets.randbelow(10**9)}@finvestug.com",
        name="Ada Nakato",
        role=role,  # type: ignore[arg-type]
        created_by=None,
    )
    # Test set-up only: active, with a password. In the application only enrolment, which also
    # sets a second step, does this.
    async with api.database.transaction() as session:
        await session.execute(
            text(
                "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                "password_changed_at = created_at WHERE id = :id"
            ),
            {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
        )
    issued = await api.identity.issue_session(
        Actor("staff", staff_id), second_step="passkey", transport="cookie"
    )
    # Sent from the console's own site, as a browser would.
    return staff_id, {"Cookie": f"{COOKIES['staff']}={issued.token}", **STAFF_HEADERS}


def no_tax(value: object) -> None:
    """Nothing a client or the public receives may name tax or a rate before tax."""
    text = str(value).lower()
    assert "tax" not in text
    assert "gross" not in text
    assert "annual_rate_bps" not in text


# The website ------------------------------------------------------------------------------------


async def test_the_website_lists_live_and_paused_products_after_tax(api: Api) -> None:
    response = await api.http.get("/public/products", headers={"Origin": "https://finvestug.com"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["access-control-allow-origin"] == "*"
    body = response.json()
    by_code = {product["code"]: product for product in body["products"]}
    assert by_code["TREA-91"]["client_rate_percent"] == "10.63"
    assert by_code["TREA-91"]["status"] == "live"
    assert by_code["USD-BOND"]["status"] == "paused"
    assert body["updated_on"] == today().isoformat()
    no_tax(body)


async def test_the_website_calculator_quotes_after_tax(api: Api) -> None:
    response = await api.http.get(
        "/public/quote", params={"product": "TREA-91", "amount_minor": 1_000_000}
    )
    assert response.status_code == 200
    quote = response.json()
    assert (quote["interest_minor"], quote["total_minor"]) == (26_490, 1_026_490)
    assert quote["starts_on"] == today().isoformat()
    no_tax(quote)
    too_small = await api.http.get(
        "/public/quote", params={"product": "TREA-91", "amount_minor": 99_999}
    )
    assert (too_small.status_code, code(too_small)) == (422, "validation_failed")
    assert too_small.json()["error"]["fields"][0]["code"] == "below_minimum"
    unknown = await api.http.get("/public/quote", params={"product": "NOPE", "amount_minor": 5})
    assert unknown.status_code == 404
    paused = await api.http.get(
        "/public/quote", params={"product": "USD-BOND", "amount_minor": 50_000}
    )
    assert paused.status_code == 422


# The client -------------------------------------------------------------------------------------


async def test_a_client_reviews_confirms_and_sees_the_investment(api: Api) -> None:
    _, auth = await signed_in_client(api)
    products = await api.http.get("/client/products", headers=auth)
    assert products.status_code == 200
    assert [p["code"] for p in products.json()] == ["TREA-91", "TREA-182"]
    no_tax(products.json())

    quote = await api.http.get(
        "/client/products/TREA-91/quote", params={"amount_minor": 1_000_000}, headers=auth
    )
    assert quote.json()["interest_minor"] == 26_490

    key = f"invest-{push_id()}"
    body = {"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"}
    created = await api.http.post(
        "/client/investments", json=body, headers={**auth, "Idempotency-Key": key}
    )
    assert created.status_code == 201
    investment = created.json()
    assert investment["status"] == "awaiting_payment"
    assert investment["at_maturity"] == "roll_over"
    assert investment["total_at_maturity_minor"] == 1_026_490
    no_tax(investment)

    again = await api.http.post(
        "/client/investments", json=body, headers={**auth, "Idempotency-Key": key}
    )
    assert again.json()["id"] == investment["id"]
    listed = await api.http.get("/client/investments", headers=auth)
    assert [item["id"] for item in listed.json()] == [investment["id"]]
    one = await api.http.get(f"/client/investments/{investment['id']}", headers=auth)
    assert one.json() == listed.json()[0]


async def test_investing_needs_an_idempotency_key_and_exact_types(api: Api) -> None:
    _, auth = await signed_in_client(api)
    body = {"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"}
    missing = await api.http.post("/client/investments", json=body, headers=auth)
    assert missing.status_code == 428
    as_text = await api.http.post(
        "/client/investments",
        json={**body, "amount_minor": "1000000"},
        headers={**auth, "Idempotency-Key": f"k-{push_id()}"},
    )
    assert as_text.status_code == 422
    as_float = await api.http.post(
        "/client/investments",
        json={**body, "amount_minor": 1000000.5},
        headers={**auth, "Idempotency-Key": f"k-{push_id()}"},
    )
    assert as_float.status_code == 422


async def test_a_client_whose_identity_is_not_checked_cannot_invest(api: Api) -> None:
    _, auth = await signed_in_client(api, approved=False)
    refused = await api.http.post(
        "/client/investments",
        json={"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"},
        headers={**auth, "Idempotency-Key": f"k-{push_id()}"},
    )
    assert (refused.status_code, code(refused)) == (403, "forbidden")


async def test_a_client_sees_only_their_own_investments(api: Api) -> None:
    _, owner = await signed_in_client(api)
    _, stranger = await signed_in_client(api)
    created = await api.http.post(
        "/client/investments",
        json={"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"},
        headers={**owner, "Idempotency-Key": f"k-{push_id()}"},
    )
    investment_id = created.json()["id"]
    assert (
        await api.http.get(f"/client/investments/{investment_id}", headers=stranger)
    ).status_code == 404
    assert (await api.http.get("/client/investments", headers=stranger)).json() == []
    choice = await api.http.put(
        f"/client/investments/{investment_id}/at-maturity",
        json={"at_maturity": "roll_over"},
        headers=stranger,
    )
    assert choice.status_code == 404


async def test_the_choice_at_maturity_over_http(api: Api) -> None:
    _, auth = await signed_in_client(api)
    created = await api.http.post(
        "/client/investments",
        json={"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"},
        headers={**auth, "Idempotency-Key": f"k-{push_id()}"},
    )
    investment_id = created.json()["id"]
    no_destination = await api.http.put(
        f"/client/investments/{investment_id}/at-maturity",
        json={"at_maturity": "pay_out"},
        headers=auth,
    )
    assert no_destination.status_code == 422
    assert no_destination.json()["error"]["fields"][0]["code"] == "required"
    unusable = await api.http.put(
        f"/client/investments/{investment_id}/at-maturity",
        json={"at_maturity": "pay_out", "destination_id": push_id()},
        headers=auth,
    )
    assert unusable.json()["error"]["fields"][0]["code"] == "not_usable"
    back = await api.http.put(
        f"/client/investments/{investment_id}/at-maturity",
        json={"at_maturity": "roll_over"},
        headers=auth,
    )
    assert (back.status_code, back.json()["at_maturity"]) == (200, "roll_over")


async def test_client_routes_need_a_client(api: Api) -> None:
    assert (await api.http.get("/client/investments")).status_code == 401
    _, cookies = await signed_in_staff(api)
    assert (await api.http.get("/client/investments", headers=cookies)).status_code == 401


# Staff ------------------------------------------------------------------------------------------


async def test_staff_see_rates_before_tax_and_the_investments(api: Api) -> None:
    _, auth = await signed_in_client(api)
    created = await api.http.post(
        "/client/investments",
        json={"product_code": "TREA-182", "amount_minor": 500_000, "currency": "UGX"},
        headers={**auth, "Idempotency-Key": f"k-{push_id()}"},
    )
    _, cookies = await signed_in_staff(api, "finance")
    products = await api.http.get("/staff/products", headers=cookies)
    assert products.status_code == 200
    bill = next(p for p in products.json() if p["code"] == "TREA-91")
    assert (bill["rate"]["annual_rate_bps"], bill["rate"]["tax_bps"]) == (1250, 1500)
    history = await api.http.get("/staff/products/TREA-91/rates", headers=cookies)
    assert history.json()[0]["annual_rate_bps"] == 1250
    listed = await api.http.get(
        "/staff/investments", params={"status": "awaiting_payment", "limit": 200}, headers=cookies
    )
    assert created.json()["id"] in [item["id"] for item in listed.json()]
    one = await api.http.get(f"/staff/investments/{created.json()['id']}", headers=cookies)
    assert one.json()["tax_bps"] == 1500
    runs = await api.http.get("/staff/interest-runs", headers=cookies)
    assert runs.status_code == 200
    maturities = await api.http.get("/staff/maturities", headers=cookies)
    assert maturities.status_code == 200
    assert set(maturities.json()) == {"today", "days", "not_closed", "held"}


async def test_a_rate_change_takes_two_people_over_http(api: Api) -> None:
    _, first = await signed_in_staff(api, "finance")
    _, second = await signed_in_staff(api, "finance")
    proposed = await api.http.post(
        "/staff/rate-proposals",
        json={
            "product_code": "TREA-182",
            "annual_rate_bps": 1400,
            "tax_bps": 1500,
            "effective_from": "2031-07-01",
            "reason": "Auction result",
        },
        headers=first,
    )
    assert proposed.status_code == 201
    proposal_id = proposed.json()["id"]
    own = await api.http.post(f"/staff/rate-proposals/{proposal_id}/approve", headers=first)
    assert own.status_code in (403, 409, 422)
    waiting = await api.http.get(
        "/staff/rate-proposals", params={"status": "proposed"}, headers=second
    )
    assert proposal_id in [item["id"] for item in waiting.json()]
    sent_back = await api.http.post(
        f"/staff/rate-proposals/{proposal_id}/send-back",
        json={"note": "Wait for the next auction"},
        headers=second,
    )
    assert (sent_back.status_code, sent_back.json()["status"]) == (200, "sent_back")


async def test_each_staff_route_checks_its_permission(api: Api) -> None:
    _, support = await signed_in_staff(api, "support")
    for path in (
        "/staff/products",
        "/staff/products/TREA-91/rates",
        "/staff/rate-proposals",
        "/staff/investments",
        "/staff/interest-runs",
        "/staff/maturities",
    ):
        response = await api.http.get(path, headers=support)
        assert (response.status_code, code(response)) == (403, "forbidden"), path
    proposed = await api.http.post(
        "/staff/rate-proposals",
        json={
            "product_code": "TREA-91",
            "annual_rate_bps": 1300,
            "tax_bps": 1500,
            "effective_from": "2031-07-01",
            "reason": "x",
        },
        headers=support,
    )
    assert proposed.status_code == 403
    for action in ("approve", "withdraw"):
        response = await api.http.post(f"/staff/rate-proposals/x/{action}", headers=support)
        assert (response.status_code, code(response)) == (403, "forbidden"), action
    sent_back = await api.http.post(
        "/staff/rate-proposals/x/send-back", json={"note": "x"}, headers=support
    )
    assert (sent_back.status_code, code(sent_back)) == (403, "forbidden")
    _, client = await signed_in_client(api, approved=False)
    assert (await api.http.get("/staff/investments", headers=client)).status_code == 401


async def test_a_started_investment_shows_after_tax_to_its_client(
    api: Api, worker_database: Database
) -> None:
    _, auth = await signed_in_client(api)
    created = await api.http.post(
        "/client/investments",
        json={"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"},
        headers={**auth, "Idempotency-Key": f"k-{push_id()}"},
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, created.json()["id"])
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(
            session, investment.id, confirmed_on=today(), clock=PRESENT
        )
    running = await api.http.get(f"/client/investments/{investment.id}", headers=auth)
    assert running.json()["status"] == "active"
    assert running.json()["start_date"] == today().isoformat()
    assert running.json()["value_minor"] == 1_000_000
    no_tax(running.json())


async def running_investment(api: Api, worker_database: Database) -> tuple[str, dict[str, str]]:
    """A client's investment that has started, and the client's sign-in."""
    _, auth = await signed_in_client(api)
    created = await api.http.post(
        "/client/investments",
        json={"product_code": "TREA-91", "amount_minor": 1_000_000, "currency": "UGX"},
        headers={**auth, "Idempotency-Key": f"k-{push_id()}"},
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, created.json()["id"])
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(
            session, investment.id, confirmed_on=today(), clock=PRESENT
        )
    return investment.id, auth


async def test_early_withdrawal_is_refused_while_it_is_switched_off(
    api: Api, worker_database: Database
) -> None:
    # Off by default: nothing pays a withdrawal until the dealing desk's funding is wired.
    investment_id, auth = await running_investment(api, worker_database)
    shown = await api.http.get(f"/client/investments/{investment_id}", headers=auth)
    assert shown.json()["status"] == "active"
    assert shown.json()["can_withdraw"] is False

    refused = await api.http.post(
        f"/client/investments/{investment_id}/withdraw",
        json={"destination_id": push_id()},
        headers={**auth, "Idempotency-Key": f"w-{push_id()}"},
    )
    assert refused.status_code == 409
    assert code(refused) == "conflict"
    after = await api.http.get(f"/client/investments/{investment_id}", headers=auth)
    assert after.json()["withdrawal_requested_at"] is None
    assert after.json()["status"] == "active"


async def test_a_running_investment_can_be_withdrawn_once_it_is_switched_on(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    async with serving(test_database, app_database, early_withdrawal_open=True) as api:
        investment_id, auth = await running_investment(api, worker_database)
        shown = await api.http.get(f"/client/investments/{investment_id}", headers=auth)
        assert shown.json()["can_withdraw"] is True
        listed = await api.http.get("/client/investments", headers=auth)
        assert listed.json()[0]["can_withdraw"] is True
