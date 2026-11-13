"""The payment routes over HTTP: a signed-in client pays, staff check a bank transfer."""

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker
from app.modules.identity.api import COOKIES
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.modules.payments import bank_transfers as bank
from app.modules.payments.bank_transfers import BankTransferJobs
from app.modules.payments.domain.bank_transfers import ReceiptDetails, ReceiptFile
from app.modules.payments.domain.deposits import Purpose
from app.modules.payments.domain.outcomes import Accepted, Pending
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import request as request_investment
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx
from tests.support.payments import FakeCollections, drain

pytestmark = pytest.mark.integration

CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
# Real time: the database refuses an investment not created now.
NOW = datetime.now(UTC)
SENT_ON = business_date(NOW) - timedelta(days=1)
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
    clock = FakeClock(NOW)
    collections = FakeCollections()
    app = create_app(settings, app_database, clock, HASHER, collections=collections)
    yield Api(app, app.state.identity, clock, app_database, collections)


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


async def submit_transfer(api: Api, client: Actor, investment_id: str, minor: int) -> str:
    """A bank transfer the client told us about. Over HTTP this arrives with the receipt upload,
    which waits for the shared file storage, so the test calls the service."""
    async with api.database.actor_transaction(client) as session:
        transfer = await bank.submit_bank_receipt(
            session,
            client,
            bank.BankTransferRequest(
                purpose=Purpose.INVESTMENT,
                purpose_id=investment_id,
                file=ReceiptFile("application/pdf", 48_000, secrets.token_hex(32)),
                details=ReceiptDetails(ugx(minor), SENT_ON, "Stanbic Bank", "FT2633100042"),
            ),
            idempotency_key=f"key-{push_id()}",
            clock=api.clock,
        )
    return transfer.deposit.id


async def import_statement(api: Api, minor: int) -> str:
    pot = f"trust-{secrets.token_hex(4)}"
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
        # Not over HTTP until receipts can be uploaded.
        closed = await client_http.post(
            f"/client/investments/{investment.id}/payments/bank-transfer", json={}, headers=key()
        )
        assert closed.status_code in (404, 405)
        payment_id = await submit_transfer(api, client, investment.id, minor)
        payment = (await client_http.get(f"/client/payments/{payment_id}")).json()
        assert payment["status"] == "in_review" and payment["method"] == "bank_transfer"
        assert payment["payer"] is None and payment["receipt"]["bank_name"] == "Stanbic Bank"

        pot = await import_statement(api, minor)
        matcher = await api.staff(first)
        await api.staff(second)
        queue = await first.get("/staff/payments/bank-transfers")
        assert queue.status_code == 200
        assert payment["id"] in [item["id"] for item in queue.json()["items"]]
        detail = (await first.get(f"/staff/payments/bank-transfers/{payment['id']}")).json()
        line = next(line for line in detail["candidates"] if line["account_reference"] == pot)

        matched = await first.post(
            f"/staff/payments/bank-transfers/{payment['id']}/review",
            json={"outcome": "matched", "statement_line_id": line["id"]},
            headers=key(),
        )
        assert matched.status_code == 200, matched.text
        assert matched.json()["review"]["reviewed_by"] == matcher.id
        assert matched.json()["candidates"] == []
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

        worker = Worker(
            worker_database,
            BankTransferJobs(worker_database, api.clock).handlers(),
            clock=api.clock,
        )
        await drain(worker)
        done = (await client_http.get(f"/client/payments/{payment['id']}")).json()
        assert done["status"] == "confirmed" and done["confirmed_at"] is not None


async def test_a_refused_transfer_says_why_and_staff_without_the_permission_are_refused(
    api: Api, worker_database: Database
) -> None:
    async with api.http() as client_http, api.http() as staff_http, api.http() as support:
        client, _ = await api.client(client_http)
        investment = await request_investment(api.database, client, api.clock)
        payment_id = await submit_transfer(api, client, investment.id, investment.amount.minor)
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
        worker = Worker(
            worker_database,
            BankTransferJobs(worker_database, api.clock).handlers(),
            clock=api.clock,
        )
        await drain(worker)
        seen = (await client_http.get(f"/client/payments/{payment_id}")).json()
        assert seen["status"] == "failed" and seen["failure"] == "name_differs"
        assert (
            await client_http.get(f"/client/investments/{investment.id}/payment")
        ).status_code == 404
