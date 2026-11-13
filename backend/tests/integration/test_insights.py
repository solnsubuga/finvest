"""Insights against real PostgreSQL: monthly figures read from the ledger, fenced per client."""

from datetime import date, timedelta

import httpx
import pytest

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.identity.hashing import PasswordHasher
from app.modules.insights import service as insights
from app.modules.insights.domain.months import month_start
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import PRESENT, started, today
from tests.support.ledger import client as new_client
from tests.support.ledger import key, staff, ugx

pytestmark = pytest.mark.integration

# The database refuses an investment not created at the real time, so these tests run on the
# real clock and date everything from this month back.
TODAY = today()
NOW = PRESENT
THIS_MONTH = month_start(TODAY)
LAST_MONTH = month_start(THIS_MONTH - timedelta(days=1))
TWO_BACK = month_start(LAST_MONTH - timedelta(days=1))


def this_month(day: int) -> date:
    """A day this month, no later than today."""
    return THIS_MONTH.replace(day=min(day, TODAY.day))


def end_of(month: date, days_before: int = 0) -> date:
    """The last day of `month`, or that many days before it."""
    return month_start(month + timedelta(days=31)) - timedelta(days=1 + days_before)


async def post(
    worker_database: Database,
    *,
    client_id: str,
    code: str,
    entry_type: str,
    amount: int,
    on: date,
    reference: str = "-Nabc0000000000000Z1",
    direction: str = "credit",
) -> str:
    """Move `amount` into (credit) or out of (debit) one of the client's accounts. Returns the
    entry's id."""
    money = ugx(amount)
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            AccountSpec(
                code,
                AccountKind.LIABILITY,
                Currency.UGX,
                "Test",
                owner_client_id=client_id,
                reference=reference,
            ),
            NOW,
        )
        other = await ledger.open_account(
            session, AccountSpec("test.other", AccountKind.ASSET, Currency.UGX, "Other"), NOW
        )
        lines = (
            (debit(other.id, money), credit(account.id, money))
            if direction == "credit"
            else (debit(account.id, money), credit(other.id, money))
        )
        posted = await ledger.post_entry(
            session,
            EntryDraft(
                entry_type=entry_type, effective_date=on, lines=lines, idempotency_key=key()
            ),
            NOW,
        )
        return posted.id


async def reverse(worker_database: Database, entry_id: str, on: date) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        await ledger.reverse_entry(session, entry_id, NOW, effective_date=on, idempotency_key=key())


async def read(database: Database, client: Actor, months: int = 3) -> insights.ClientInsights:
    async with database.actor_transaction(client) as session:
        return await insights.client_insights(session, client, NOW, months=months)


async def test_a_new_client_sees_empty_months(app_database: Database) -> None:
    client = new_client()
    result = await read(app_database, client)
    assert [month.month for month in result.months] == [
        TWO_BACK,
        LAST_MONTH,
        THIS_MONTH,
    ]
    zero = ugx(0)
    assert all((m.invested, m.earned, m.paid_out) == (zero, zero, zero) for m in result.months)
    assert (result.invested_now, result.earned_so_far, result.upcoming) == (zero, zero, ())


async def test_months_add_up_what_started_what_was_earned_and_what_was_paid_out(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW, confirmed_on=TODAY)
    client = Actor("client", investment.client_id)
    interest = ("client.interest_accrued", "investment.interest_accrued")
    for amount, on in ((300, end_of(LAST_MONTH)), (291, this_month(1)), (291, TODAY)):
        await post(
            worker_database,
            client_id=client.id,  # type: ignore[arg-type]
            code=interest[0],
            entry_type=interest[1],
            amount=amount,
            on=on,
            reference=investment.id,
        )
    # A payout is reserved into the in-flight account, then settled out of it.
    await post(
        worker_database,
        client_id=client.id,  # type: ignore[arg-type]
        code="client.payout_in_flight",
        entry_type="payout.reserve",
        amount=50_000,
        on=TWO_BACK,
    )
    await post(
        worker_database,
        client_id=client.id,  # type: ignore[arg-type]
        code="client.payout_in_flight",
        entry_type="payout.settle",
        amount=50_000,
        on=TWO_BACK.replace(day=2),
        direction="debit",
    )
    # Not counted: a rollover is not new money, and interest of another kind is not earnings.
    await post(
        worker_database,
        client_id=client.id,  # type: ignore[arg-type]
        code="client.holding",
        entry_type="investment.rolled_over",
        amount=999_999,
        on=this_month(2),
    )
    # Not counted: outside the window.
    await post(
        worker_database,
        client_id=client.id,  # type: ignore[arg-type]
        code=interest[0],
        entry_type=interest[1],
        amount=7,
        on=TWO_BACK - timedelta(days=1),
        reference=investment.id,
    )
    september, october, november = (await read(app_database, client)).months
    assert (september.invested, september.earned, september.paid_out) == (
        ugx(0),
        ugx(0),
        ugx(50_000),
    )
    assert (october.invested, october.earned) == (ugx(0), ugx(300))
    assert (november.invested, november.earned, november.paid_out) == (
        ugx(1_000_000),
        ugx(582),
        ugx(0),
    )


async def test_a_reversed_entry_nets_out_in_the_month_it_is_reversed(
    app_database: Database, worker_database: Database
) -> None:
    client = new_client()
    client_id: str = client.id  # type: ignore[assignment]
    interest = ("client.interest_accrued", "investment.interest_accrued")
    kept = await post(
        worker_database,
        client_id=client_id,
        code=interest[0],
        entry_type=interest[1],
        amount=300,
        on=end_of(LAST_MONTH, 1),
    )
    wrong = await post(
        worker_database,
        client_id=client_id,
        code=interest[0],
        entry_type=interest[1],
        amount=500,
        on=end_of(LAST_MONTH),
    )
    # The same day's mistake, and one found in the next month.
    started_twice = await post(
        worker_database,
        client_id=client_id,
        code="client.holding",
        entry_type="investment.started",
        amount=1_000_000,
        on=this_month(3),
    )
    await reverse(worker_database, started_twice, this_month(3))
    await reverse(worker_database, wrong, this_month(2))
    paid = await post(
        worker_database,
        client_id=client_id,
        code="client.payout_in_flight",
        entry_type="payout.reserve",
        amount=40_000,
        on=this_month(4),
    )
    settled = await post(
        worker_database,
        client_id=client_id,
        code="client.payout_in_flight",
        entry_type="payout.settle",
        amount=40_000,
        on=this_month(5),
        direction="debit",
    )
    await reverse(worker_database, settled, this_month(6))
    assert kept and paid

    _, october, november = (await read(app_database, client)).months
    assert october.earned == ugx(800)
    # The reversal is dated in November, so it lowers November, as the statement shows it.
    assert (november.invested, november.earned, november.paid_out) == (ugx(0), ugx(-500), ugx(0))


async def test_what_is_invested_now_and_what_comes_next(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW, confirmed_on=TODAY)
    client = Actor("client", investment.client_id)
    result = await read(app_database, client)
    assert result.invested_now == ugx(1_000_000)
    [upcoming] = result.upcoming
    assert upcoming.on == investment.maturity_date
    assert upcoming.investment_id == investment.id
    # What comes back at maturity, after tax: the canvas example.
    assert upcoming.amount == ugx(1_026_490)


async def test_a_client_never_sees_another_clients_figures(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW, confirmed_on=TODAY)
    other = new_client()
    async with app_database.actor_transaction(other) as session:
        # Asking with the other client's own session shows only their (empty) books.
        result = await insights.client_insights(session, other, NOW)
    assert result.invested_now == Money(0, Currency.UGX)
    assert all(month.invested == ugx(0) for month in result.months)
    assert investment.client_id != other.id


async def test_only_a_client_has_insights(app_database: Database) -> None:
    for actor in (staff(), SYSTEM):
        with pytest.raises(ApplicationError) as error:
            async with app_database.actor_transaction(actor) as session:
                await insights.client_insights(session, actor, FakeClock())
        assert error.value.code == "forbidden"


async def test_the_window_is_bounded(app_database: Database) -> None:
    client = new_client()
    for months in (0, 25):
        with pytest.raises(ApplicationError) as error:
            await read(app_database, client, months)
        assert error.value.code == "validation_failed"


# The route --------------------------------------------------------------------------------------


async def test_the_route_serves_the_signed_in_clients_own_insights(
    test_database: TestDatabase, app_database: Database
) -> None:
    app = create_app(
        test_database.settings("finvest_app"),
        app_database,
        NOW,
        PasswordHasher(memory_kib=19_456, time_cost=2),
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        assert (await http.get("/client/insights")).status_code == 401
        identity = app.state.identity
        client_id = await identity.create_client(
            phone="+256709123987", email=None, password="copper-harbour-violin"
        )
        issued = await identity.issue_session(
            Actor("client", client_id), second_step="signup", transport="token"
        )
        bearer = {"Authorization": f"Bearer {issued.token}"}
        response = await http.get("/client/insights", params={"months": 2}, headers=bearer)
        assert response.status_code == 200
        assert response.json() == {
            "currency": "UGX",
            "months": [
                {
                    "month": LAST_MONTH.isoformat(),
                    "invested_minor": 0,
                    "earned_minor": 0,
                    "paid_out_minor": 0,
                },
                {
                    "month": THIS_MONTH.isoformat(),
                    "invested_minor": 0,
                    "earned_minor": 0,
                    "paid_out_minor": 0,
                },
            ],
            "invested_now_minor": 0,
            "earned_so_far_minor": 0,
            "upcoming": [],
        }
        too_many = await http.get("/client/insights", params={"months": 25}, headers=bearer)
        assert too_many.status_code == 422
