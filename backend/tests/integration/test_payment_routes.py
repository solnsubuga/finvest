"""The payment routes over HTTP: a signed-in client pays, staff check a bank transfer."""

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from structlog.testing import capture_logs

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker
from app.infrastructure.storage import (
    DisabledStorage,
    MemoryStorage,
    StorageUnavailable,
    sha256_hex,
)
from app.modules.dealing.domain.settlement import BANK_TRUST
from app.modules.dealing.domain.settlement import accounts as settlement_accounts
from app.modules.identity.api import COOKIES
from app.modules.identity.domain.permissions import ROLE_PERMISSIONS
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.modules.ledger import service as ledger
from app.modules.payments import bank_transfers as bank
from app.modules.payments.bank_transfers import BankTransferJobs
from app.modules.payments.domain.bank_accounts import BankAccount
from app.modules.payments.domain.outcomes import Accepted, Pending
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.pots import CASH_POTS
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import request as request_investment
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx
from tests.support.payments import FakeCollections, drain, use_bank_account

pytestmark = pytest.mark.integration

CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
# Real time: the database refuses an investment not created now.
# The database refuses an investment not created at the current time, so each test's clock
# starts when the test does (`now()`), not when this file was imported. NOW only fixes the day
# a transfer was sent.
NOW = datetime.now(UTC)
SENT_ON = business_date(NOW) - timedelta(days=1)


def now() -> datetime:
    return datetime.now(UTC)


FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)


@dataclass
class Api:
    app: object
    identity: IdentityService
    clock: FakeClock
    database: Database
    collections: FakeCollections
    storage: MemoryStorage

    def http(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=self.app,  # type: ignore[arg-type]
            raise_app_exceptions=False,
            client=("127.0.0.1", 1),
        )
        return httpx.AsyncClient(transport=transport, base_url="https://api")

    async def client(self, http: httpx.AsyncClient) -> tuple[Actor, str]:
        """A signed-in client who passed the identity check. Returns them and their phone."""
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
        return actor, phone

    async def staff(self, http: httpx.AsyncClient, role: str = "finance") -> Actor:
        staff_id = await self.identity.invite_staff(
            email=f"staff-{secrets.token_hex(6)}@finvestug.com",
            name="Grace Auma",
            role=role,  # type: ignore[arg-type]
            created_by=None,
        )
        # Test set-up only: active, with a password. In the application only enrolment, which
        # also sets a second step, does this.
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                    "password_changed_at = created_at WHERE id = :id"
                ),
                {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
            )
        actor = Actor("staff", staff_id)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="cookie")
        http.cookies.set(COOKIES["staff"], issued.token)
        http.headers["Origin"] = STAFF_ORIGIN
        return actor


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
        }
    )
    clock = FakeClock(now())
    collections = FakeCollections()
    storage = MemoryStorage()
    app = create_app(
        settings,
        app_database,
        clock,
        HASHER,
        collections=collections,
        storage=storage,
    )
    await use_bank_account(app_database, Currency.UGX, TRUST_ACCOUNT, clock)
    yield Api(app, app.state.identity, clock, app_database, collections, storage)


TRUST_ACCOUNT = BankAccount(
    bank_name="Example Bank Uganda",
    account_name="Finvest Clients Trust Account",
    account_number="9030001234567",
    branch="Kampala Road",
)


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"key-{push_id()}"}


async def test_a_client_pays_by_mobile_money_from_their_own_number(api: Api) -> None:
    async with api.http() as http:
        investment_id = push_id()
        refused = await http.post(
            f"/client/investments/{investment_id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
        assert refused.status_code == 401
        client, phone = await api.client(http)
        investment = await request_investment(api.database, client, api.clock)
        api.collections.on_send(phone, Accepted("ref-1"))
        api.collections.on_status(phone, Pending("ref-1"))

        without_key = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money", json={"network": "mtn"}
        )
        assert without_key.status_code == 428
        paid = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
        assert paid.status_code == 201, paid.text
        body = paid.json()
        assert body["amount"] == {"minor": investment.amount.minor, "currency": "UGX"}
        assert body["method"] == "mtn" and body["status"] == "created"
        assert body["payer"].startswith("+256 ") and phone not in body["payer"]
        # The number is the account's own. The request cannot name one.
        other = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn", "phone": "+256772000000"},
            headers=key(),
        )
        assert other.status_code == 422
        again = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "airtel"},
            headers=key(),
        )
        assert again.status_code == 409
        current = await http.get(f"/client/investments/{investment.id}/payment")
        assert current.status_code == 200 and current.json()["id"] == body["id"]
        assert (await http.get(f"/client/payments/{body['id']}")).json()["id"] == body["id"]


async def test_a_client_is_shown_where_to_send_a_transfer_until_a_payment_starts(
    api: Api,
) -> None:
    async with api.http() as http, api.http() as stranger_http:
        client, phone = await api.client(http)
        investment = await request_investment(api.database, client, api.clock)
        async with api.database.actor_transaction(client) as session:
            assert client.id is not None
            holder = await kyc.account_holder(session, client.id)
        assert holder is not None

        shown = await http.get(f"/client/investments/{investment.id}/bank-transfer-details")
        assert shown.status_code == 200, shown.text
        assert shown.json() == {
            "bank_name": "Example Bank Uganda",
            "account_name": "Finvest Clients Trust Account",
            "account_number": "9030001234567",
            "branch": "Kampala Road",
            "reference": holder.account_number,
            "amount": {"minor": investment.amount.minor, "currency": "UGX"},
        }
        await api.client(stranger_http)
        assert (
            await stranger_http.get(f"/client/investments/{investment.id}/bank-transfer-details")
        ).status_code == 404
        assert (
            await http.get(f"/client/investments/{push_id()}/bank-transfer-details")
        ).status_code == 404

        api.collections.on_send(phone, Accepted("ref-details"))
        paid = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
        assert paid.status_code == 201
        # A payment is under way: the app shows that one instead.
        assert (
            await http.get(f"/client/investments/{investment.id}/bank-transfer-details")
        ).status_code == 409


async def test_until_two_people_set_an_account_no_details_are_shown(
    api: Api, migrator_database: Database
) -> None:
    async with migrator_database.transaction() as session:
        await session.execute(text("TRUNCATE payment_bank_accounts"))
    async with api.http() as http, api.http() as maker, api.http() as checker:
        client, _ = await api.client(http)
        investment = await request_investment(api.database, client, api.clock)
        url = f"/client/investments/{investment.id}/bank-transfer-details"
        answer = await http.get(url)
        assert answer.status_code == 503
        assert answer.json()["error"]["code"] == "unavailable"

        await api.staff(maker)
        await api.staff(checker)
        listed = (await maker.get("/staff/payments/bank-accounts")).json()["accounts"]
        assert [(a["currency"], a["in_use"], a["proposal"]) for a in listed] == [
            ("UGX", None, None),
            ("USD", None, None),
        ]
        proposed = await maker.post(
            "/staff/payments/bank-accounts/UGX/proposals",
            json={
                "account": {
                    "bank_name": "Example Bank Uganda",
                    "account_name": "Finvest Clients Trust Account",
                    "account_number": "9030 0012 34567",
                    "branch": "Kampala Road",
                },
                "reason": "The trust account, from the bank's letter",
            },
        )
        assert proposed.status_code == 201, proposed.text
        proposal = proposed.json()
        assert proposal["yours"] is True
        assert proposal["account"]["account_number"] == "9030001234567"
        # Still nothing for the client while it waits.
        assert (await http.get(url)).status_code == 503
        seen = (await checker.get("/staff/payments/bank-accounts")).json()["accounts"][0]
        assert seen["proposal"]["id"] == proposal["id"] and seen["proposal"]["yours"] is False
        inbox = (await checker.get("/staff/approvals")).json()["items"]
        assert any(
            item["kind"] == "bank_account_change" and item["id"] == proposal["id"] for item in inbox
        )

        decision = f"/staff/payments/bank-accounts/proposals/{proposal['id']}/decision"
        own = await maker.post(decision, json={"decision": "approved", "reason": "Mine"})
        assert own.status_code == 403
        approved = await checker.post(decision, json={"decision": "approved", "reason": "Checked"})
        assert approved.status_code == 200, approved.text
        shown = await http.get(url)
        assert shown.status_code == 200
        assert shown.json()["account_number"] == "9030001234567"
        listed = (await maker.get("/staff/payments/bank-accounts")).json()["accounts"]
        assert listed[0]["in_use"]["bank_name"] == "Example Bank Uganda"
        assert listed[0]["changed_at"] is not None and listed[0]["proposal"] is None


async def test_only_staff_who_may_change_the_account_propose_or_decide(api: Api) -> None:
    async with api.http() as support, api.http() as operations, api.http() as client_http:
        await api.staff(support, role="support")
        await api.staff(operations, role="operations")
        await api.client(client_http)
        body = {
            "account": {
                "bank_name": "Bank",
                "account_name": "Finvest",
                "account_number": "1234567",
                "branch": None,
            },
            "reason": "Why",
        }
        for http in (support, operations):
            assert (await http.get("/staff/payments/bank-accounts")).status_code == 403
            assert (
                await http.post("/staff/payments/bank-accounts/UGX/proposals", json=body)
            ).status_code == 403
        assert (await client_http.get("/staff/payments/bank-accounts")).status_code == 401
        assert (
            await client_http.post("/staff/payments/bank-accounts/UGX/proposals", json=body)
        ).status_code == 401
    assert "bank_accounts.change" in ROLE_PERMISSIONS["finance"]
    assert "bank_accounts.change" not in ROLE_PERMISSIONS["operations"]


async def test_a_client_cannot_pay_for_someone_elses_investment_or_see_their_payment(
    api: Api,
) -> None:
    async with api.http() as owner_http, api.http() as stranger_http:
        owner, phone = await api.client(owner_http)
        investment = await request_investment(api.database, owner, api.clock)
        api.collections.on_send(phone, Accepted("ref-2"))
        paid = await owner_http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
        await api.client(stranger_http)
        stolen = await stranger_http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
        assert stolen.status_code == 404
        assert (await stranger_http.get(f"/client/payments/{paid.json()['id']}")).status_code == 404
        assert (
            await stranger_http.get(f"/client/investments/{investment.id}/payment")
        ).status_code == 404


async def test_a_client_lists_only_their_own_payments_newest_first_a_page_at_a_time(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.payments import routes

    monkeypatch.setattr(routes, "CLIENT_PAGE", 2)
    async with api.http() as http, api.http() as stranger_http:
        assert (await http.get("/client/payments")).status_code == 401
        client, phone = await api.client(http)
        await api.client(stranger_http)
        assert (await stranger_http.get("/client/payments")).json() == {
            "items": [],
            "next_before": None,
        }
        paid = []
        for n in range(3):
            investment = await request_investment(api.database, client, api.clock)
            api.collections.on_send(phone, Accepted(f"ref-list-{n}"))
            answer = await http.post(
                f"/client/investments/{investment.id}/payments/mobile-money",
                json={"network": "mtn"},
                headers=key(),
            )
            assert answer.status_code == 201, answer.text
            paid.append(answer.json()["id"])

        first = await http.get("/client/payments")
        assert first.status_code == 200, first.text
        body = first.json()
        assert [item["id"] for item in body["items"]] == [paid[2], paid[1]]
        assert body["next_before"] == paid[1]
        item = body["items"][0]
        assert item["method"] == "mtn" and item["receipt"] is None
        assert phone not in str(body)
        rest = (await http.get("/client/payments", params={"before": body["next_before"]})).json()
        assert [item["id"] for item in rest["items"]] == [paid[0]]
        assert rest["next_before"] is None
        # Another client sees none of them.
        assert (await stranger_http.get("/client/payments")).json()["items"] == []


RECEIPT = b"%PDF-1.7\n" + secrets.token_bytes(4096)


def transfer_form(minor: int, **changes: str) -> dict[str, str]:
    return {
        "amount_minor": str(minor),
        "sent_on": SENT_ON.isoformat(),
        "bank_name": "Stanbic Bank",
        "transaction_number": "FT2633100042",
    } | changes


async def send_transfer(
    http: httpx.AsyncClient,
    investment_id: str,
    minor: int,
    *,
    receipt: bytes = RECEIPT,
    content_type: str = "application/pdf",
    headers: dict[str, str] | None = None,
    **changes: str,
) -> httpx.Response:
    return await http.post(
        f"/client/investments/{investment_id}/payments/bank-transfer",
        data=transfer_form(minor, **changes),
        files={"receipt": ("receipt.pdf", receipt, content_type)},
        headers=headers or key(),
    )


async def submit_transfer(http: httpx.AsyncClient, investment_id: str, minor: int) -> str:
    """A bank transfer the client told us about, with its receipt, over HTTP."""
    sent = await send_transfer(http, investment_id, minor)
    assert sent.status_code == 201, sent.text
    return str(sent.json()["id"])


async def import_statement(api: Api, minor: int, pot: str | None = None) -> str:
    pot = pot or f"trust-{secrets.token_hex(4)}"
    parsed = parse_csv(
        f"Date,Details,Reference,In,Out\n{SENT_ON.isoformat()},Transfer,REF1,{minor},\n",
        FORMAT,
        source=StatementSource.BANK,
        account_reference=pot,
        statement_date=SENT_ON,
        opening_balance=ugx(10**12),
        closing_balance=ugx(10**12 + minor),
    )
    importer = new_staff()
    async with api.database.actor_transaction(importer) as session:
        await statements.import_statement(session, importer, parsed, "0" * 64, api.clock)
    return pot


async def test_a_bank_transfer_is_checked_by_one_person_and_approved_by_another(
    api: Api, worker_database: Database
) -> None:
    minor = 1_000_000 + secrets.randbelow(10**6) * 1000
    async with api.http() as client_http, api.http() as first, api.http() as second:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock, amount=ugx(minor))
        payment_id = await submit_transfer(client_http, investment.id, minor)
        payment = (await client_http.get(f"/client/payments/{payment_id}")).json()
        # The file is stored once, under the transfer's key, and is the bytes the client sent.
        stored_key = f"payments/receipts/{payment_id}"
        assert await api.storage.read(stored_key, sha256=sha256_hex(RECEIPT)) == RECEIPT
        assert payment["receipt"]["byte_size"] == len(RECEIPT)
        assert payment["status"] == "in_review" and payment["method"] == "bank_transfer"
        assert payment["payer"] is None and payment["receipt"]["bank_name"] == "Stanbic Bank"

        pot = await import_statement(api, minor)
        matcher = await api.staff(first)
        await api.staff(second)

        async def awaiting() -> str:
            queue = await first.get("/staff/payments/bank-transfers")
            assert queue.status_code == 200
            found = [item for item in queue.json()["items"] if item["id"] == payment["id"]]
            assert len(found) == 1
            return str(found[0]["awaiting"])

        assert await awaiting() == "check"
        detail = (await first.get(f"/staff/payments/bank-transfers/{payment['id']}")).json()
        line = next(line for line in detail["candidates"] if line["account_reference"] == pot)
        assert detail["receipt_link"]["url"].startswith(f"memory://{stored_key}?")

        matched = await first.post(
            f"/staff/payments/bank-transfers/{payment['id']}/review",
            json={"outcome": "matched", "statement_line_id": line["id"]},
            headers=key(),
        )
        assert matched.status_code == 200, matched.text
        assert matched.json()["review"]["reviewed_by"] == matcher.id
        assert matched.json()["candidates"] == []
        assert await awaiting() == "second_person"
        # The second person sees the chosen line beside the receipt before approving.
        shown = (await second.get(f"/staff/payments/bank-transfers/{payment['id']}")).json()
        assert shown["matched_line"]["id"] == line["id"]
        assert shown["matched_line"]["amount"] == {"minor": minor, "currency": "UGX"}
        assert shown["matched_line"]["value_date"] == SENT_ON.isoformat()
        own = await first.post(
            f"/staff/payments/bank-transfers/{payment['id']}/approve", headers=key()
        )
        assert own.status_code == 403
        approved = await second.post(
            f"/staff/payments/bank-transfers/{payment['id']}/approve", headers=key()
        )
        assert approved.status_code == 200, approved.text
        assert await awaiting() == "posting"

        worker = Worker(
            worker_database,
            BankTransferJobs(worker_database, api.clock).handlers(),
            clock=api.clock,
        )
        await drain(worker)
        done = (await client_http.get(f"/client/payments/{payment['id']}")).json()
        assert done["status"] == "confirmed" and done["confirmed_at"] is not None
        assert await told(worker_database, payment["id"]) == [
            ("payment_confirmed", "We received your payment from your bank.")
        ]


async def told(worker_database: Database, deposit_id: str) -> list[tuple[str, str]]:
    """What the client was told about a payment, oldest first."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text("SELECT event, body FROM notifications WHERE link_id = :id ORDER BY created_at"),
            {"id": deposit_id},
        )
        return [(row.event, row.body) for row in rows]


async def test_a_transfer_into_the_trust_account_is_the_money_dealing_and_the_day_close_see(
    api: Api, worker_database: Database
) -> None:
    """The trust account has one ledger account. A transfer matched to its statement adds to
    it, so the day close finds it there and the dealing desk can spend it."""
    minor = 3_000_000 + secrets.randbelow(10**6) * 1000
    trust = next(pot for pot in CASH_POTS if pot.account_reference == "trust")
    async with api.http() as client_http, api.http() as first, api.http() as second:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock, amount=ugx(minor))
        payment_id = await submit_transfer(client_http, investment.id, minor)
        await import_statement(api, minor, pot="trust")
        await api.staff(first)
        await api.staff(second)
        detail = (await first.get(f"/staff/payments/bank-transfers/{payment_id}")).json()
        line = next(line for line in detail["candidates"] if line["account_reference"] == "trust")
        matched = await first.post(
            f"/staff/payments/bank-transfers/{payment_id}/review",
            json={"outcome": "matched", "statement_line_id": line["id"]},
            headers=key(),
        )
        assert matched.status_code == 200, matched.text
        async with worker_database.actor_transaction(SYSTEM) as session:
            account = await ledger.open_account(session, trust.ledger_account, api.clock)
            before = (await ledger.get_balance(session, account.id)).minor
        approved = await second.post(
            f"/staff/payments/bank-transfers/{payment_id}/approve", headers=key()
        )
        assert approved.status_code == 200, approved.text
        worker = Worker(
            worker_database,
            BankTransferJobs(worker_database, api.clock).handlers(),
            clock=api.clock,
        )
        await drain(worker)
        assert (await client_http.get(f"/client/payments/{payment_id}")).json()["status"] == (
            "confirmed"
        )
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert (await ledger.get_balance(session, account.id)).minor == before + minor
            # The account dealing settles purchases from is the same one.
            dealing_trust = await ledger.open_account(
                session, settlement_accounts(Currency.UGX)[BANK_TRUST], api.clock
            )
            assert dealing_trust.id == account.id


async def test_a_refused_transfer_says_why_and_staff_without_the_permission_are_refused(
    api: Api, worker_database: Database
) -> None:
    async with api.http() as client_http, api.http() as staff_http, api.http() as support:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock)
        payment_id = await submit_transfer(client_http, investment.id, investment.amount.minor)
        await api.staff(support, role="support")
        assert (await support.get("/staff/payments/bank-transfers")).status_code == 403
        assert (
            await support.post(
                f"/staff/payments/bank-transfers/{payment_id}/review",
                json={"outcome": "not_matched", "reason": "other"},
                headers=key(),
            )
        ).status_code == 403
        # A client cannot use the staff routes.
        assert (await client_http.get("/staff/payments/bank-transfers")).status_code == 401

        await api.staff(staff_http)
        refused = await staff_http.post(
            f"/staff/payments/bank-transfers/{payment_id}/review",
            json={"outcome": "not_matched", "reason": "name_differs"},
            headers=key(),
        )
        assert refused.status_code == 200
        # Refused: no second person is needed, the worker settles it.
        queue = (await staff_http.get("/staff/payments/bank-transfers")).json()["items"]
        assert [item["awaiting"] for item in queue if item["id"] == payment_id] == ["posting"]
        worker = Worker(
            worker_database,
            BankTransferJobs(worker_database, api.clock).handlers(),
            clock=api.clock,
        )
        await drain(worker)
        seen = (await client_http.get(f"/client/payments/{payment_id}")).json()
        assert seen["status"] == "failed" and seen["failure"] == "name_differs"
        # Not told "no money was taken": the transfer may have reached the bank.
        assert await told(worker_database, payment_id) == []
        assert (
            await client_http.get(f"/client/investments/{investment.id}/payment")
        ).status_code == 404


async def test_seeing_transfers_is_not_enough_to_confirm_one_and_each_look_is_audited(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No role sees reconciliation without the right to decide transfers today. Give it to
    # support, as the owner might, and it can look but neither match, refuse nor approve.
    monkeypatch.setitem(
        ROLE_PERMISSIONS, "support", ROLE_PERMISSIONS["support"] | {"reconciliation.view"}
    )
    async with api.http() as client_http, api.http() as viewer_http:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock)
        payment_id = await submit_transfer(client_http, investment.id, investment.amount.minor)
        viewer = await api.staff(viewer_http, role="support")
        assert (await viewer_http.get("/staff/payments/bank-transfers")).status_code == 200
        opened = await viewer_http.get(f"/staff/payments/bank-transfers/{payment_id}")
        assert opened.status_code == 200
        for route, body in (
            ("review", {"outcome": "not_matched", "reason": "other"}),
            ("approve", None),
        ):
            refused = await viewer_http.post(
                f"/staff/payments/bank-transfers/{payment_id}/{route}", json=body, headers=key()
            )
            assert refused.status_code == 403
        async with api.database.actor_transaction(viewer) as session:
            audited = await session.scalar(
                text(
                    "SELECT count(*) FROM audit_events WHERE resource_id = :id "
                    "AND action = 'payments.bank_transfer_opened' AND category = 'access' "
                    "AND actor_id = :staff"
                ),
                {"id": payment_id, "staff": viewer.id},
            )
        assert audited == 1


async def test_a_receipt_is_checked_before_it_is_stored(api: Api) -> None:
    async with api.http() as client_http:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock)
        owed = investment.amount.minor
        refusals = [
            (
                await send_transfer(client_http, investment.id, owed + 1000),
                "amount_minor",
                "amount_differs_from_investment",
            ),
            (
                await send_transfer(client_http, investment.id, owed, content_type="text/html"),
                "file",
                "unsupported_content_type",
            ),
            (
                await send_transfer(client_http, investment.id, owed, content_type="image/png"),
                "receipt",
                "content_does_not_match_type",
            ),
            (
                await send_transfer(client_http, investment.id, owed, receipt=b""),
                "file",
                "invalid_size",
            ),
            (
                await send_transfer(
                    client_http,
                    investment.id,
                    owed,
                    sent_on=(SENT_ON + timedelta(days=3)).isoformat(),
                ),
                "sent_on",
                "in_the_future",
            ),
        ]
        for response, field, code in refusals:
            assert response.status_code == 422, response.text
            problem = response.json()["error"]["fields"][0]
            assert problem["field_path"][-1] == field and problem["code"] == code
        assert api.storage.files == {}

        # A body over the receipt limit is refused before it is read.
        too_big = await send_transfer(
            client_http, investment.id, owed, receipt=b"%PDF" + bytes(12 * 1024 * 1024)
        )
        assert too_big.status_code == 413
        assert api.storage.files == {}

        # The same key and the same receipt give back the same transfer.
        headers = key()
        first = await send_transfer(client_http, investment.id, owed, headers=headers)
        again = await send_transfer(client_http, investment.id, owed, headers=headers)
        assert first.status_code == 201 and again.status_code == 201
        assert again.json()["id"] == first.json()["id"]
        # The replay stored nothing: one file, the first transfer's.
        assert list(api.storage.files) == [f"payments/receipts/{first.json()['id']}"]
        # A second transfer for an investment already being paid is refused, and stores nothing.
        other = await send_transfer(client_http, investment.id, owed)
        assert other.status_code == 409
        assert len(api.storage.files) == 1


class UnreachableOnDelete(MemoryStorage):
    async def delete(self, key: str) -> None:
        raise StorageUnavailable("down")


@pytest.mark.parametrize("delete_works", [True, False])
async def test_a_receipt_stored_for_a_transfer_that_is_not_saved_is_deleted(
    api: Api, monkeypatch: pytest.MonkeyPatch, delete_works: bool
) -> None:
    if not delete_works:
        api.storage = UnreachableOnDelete()
        api.app.state.storage = api.storage  # type: ignore[attr-defined]
    real = bank.submit_bank_receipt

    async def fails_once_stored(*args: Any, **kwargs: Any) -> bank.BankTransfer:
        # The preflight passes; the transaction that would record the stored file fails.
        if kwargs.get("_file_stored", True):
            raise ApplicationError("unavailable")
        return await real(*args, **kwargs)

    monkeypatch.setattr(bank, "submit_bank_receipt", fails_once_stored)
    async with api.http() as client_http:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock)
        with capture_logs() as logs:
            failed = await send_transfer(client_http, investment.id, investment.amount.minor)
        assert failed.status_code == 503
        missing = await client_http.get(f"/client/investments/{investment.id}/payment")
        assert missing.status_code == 404
    orphaned = [entry for entry in logs if entry["event"] == "receipt_orphaned"]
    if delete_works:
        assert api.storage.files == {} and orphaned == []
    else:
        # The store could not be reached to delete it: the log names the deposit, never the key.
        assert len(api.storage.files) == 1 and len(orphaned) == 1
        assert set(orphaned[0]) == {"event", "log_level", "deposit_id"}


async def test_without_storage_no_transfer_is_saved(
    test_database: TestDatabase, app_database: Database
) -> None:
    settings = test_database.settings("finvest_app").model_copy(
        update={"client_allowed_origins": [CLIENT_ORIGIN]}
    )
    clock = FakeClock(now())
    app = create_app(
        settings,
        app_database,
        clock,
        HASHER,
        collections=FakeCollections(),
        storage=DisabledStorage(),
    )
    api = Api(app, app.state.identity, clock, app_database, FakeCollections(), MemoryStorage())
    async with api.http() as client_http:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock)
        refused = await send_transfer(client_http, investment.id, investment.amount.minor)
        assert refused.status_code == 503
        missing = await client_http.get(f"/client/investments/{investment.id}/payment")
        assert missing.status_code == 404
