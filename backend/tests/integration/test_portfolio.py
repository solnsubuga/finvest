"""The Home and Portfolio totals against real PostgreSQL: sums of the client's own views."""

from datetime import timedelta

import httpx
import pytest

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.goals import service as goals
from app.modules.goals.domain.goals import GoalCategory
from app.modules.identity.hashing import PasswordHasher
from app.modules.insights import service as insights
from app.modules.investments import service as investments
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import (
    PRESENT,
    approved_client,
    approved_destination,
    at,
    pay,
    request,
    today,
)
from tests.support.ledger import client as new_client
from tests.support.ledger import key, staff, ugx

pytestmark = pytest.mark.integration

# The database refuses an investment not created at the real time, so these run on the real clock.
TODAY = today()
NOW = PRESENT


async def start(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    *,
    amount: int,
    product_code: str = "TREA-91",
) -> investments.Investment:
    investment = await request(
        app_database, client, NOW, amount=ugx(amount), product_code=product_code
    )
    await pay(worker_database, investment, investment.amount, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment.id, confirmed_on=TODAY, clock=NOW
        )


async def save_to_new_goal(
    app_database: Database, worker_database: Database, client: Actor, amount: int
) -> str:
    async with app_database.actor_transaction(client) as session:
        goal = await goals.create_goal(
            session,
            client,
            idempotency_key=key(),
            name="School fees",
            category=GoalCategory.SCHOOL_FEES,
            target=ugx(5_000_000),
            target_date=None,
            clock=NOW,
        )
        saving = await goals.save_to_goal(
            session, client, goal.id, idempotency_key=key(), amount=ugx(amount), clock=NOW
        )
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, saving.id)
    await pay(worker_database, investment, investment.amount, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, saving.id, confirmed_on=TODAY, clock=NOW)
    return saving.id


async def read(
    database: Database, client: Actor, currency: Currency = Currency.UGX
) -> insights.ClientPortfolio:
    async with database.actor_transaction(client) as session:
        return await insights.client_portfolio(session, client, NOW, currency=currency)


async def test_a_new_client_has_nothing_invested(app_database: Database) -> None:
    client = new_client()
    portfolio = await read(app_database, client)
    zero = ugx(0)
    assert (portfolio.invested, portfolio.worth, portfolio.earning_per_day) == (zero, zero, zero)
    assert (portfolio.active_count, portfolio.awaiting_payment_count) == (0, 0)
    assert portfolio.outside_goals == portfolio.in_goals == insights.Holding(zero, 0)
    assert (portfolio.open_goals, portfolio.products, portfolio.next_maturity) == (0, (), None)


async def test_the_totals_add_up_the_clients_own_investments(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    bill = await start(app_database, worker_database, client, amount=5_000_000)
    longer = await start(
        app_database, worker_database, client, amount=3_000_000, product_code="TREA-182"
    )
    saved = await save_to_new_goal(app_database, worker_database, client, 2_000_000)
    # Waiting for payment: counted on its own, never in what is invested.
    await request(app_database, client, NOW, amount=ugx(500_000))

    portfolio = await read(app_database, client)
    async with app_database.actor_transaction(client) as session:
        views = {
            view.id: view for view in await investments.client_investments(session, client, NOW)
        }
    running = [views[bill.id], views[longer.id], views[saved]]

    assert portfolio.invested == ugx(10_000_000)
    assert portfolio.worth == sum((view.value for view in running), ugx(0))
    assert portfolio.interest_so_far == ugx(0)
    # Each running investment earns its next day of interest, after tax.
    assert portfolio.earning_per_day == sum((view.earning_per_day for view in running), ugx(0))
    assert all(view.earning_per_day.minor > 0 for view in running)
    assert (portfolio.active_count, portfolio.awaiting_payment_count) == (3, 1)
    assert portfolio.in_goals == insights.Holding(views[saved].value, 1)
    assert portfolio.outside_goals == insights.Holding(
        views[bill.id].value + views[longer.id].value, 2
    )
    assert portfolio.in_goals.worth + portfolio.outside_goals.worth == portfolio.worth
    assert portfolio.open_goals == 1
    assert [
        (share.product_code, share.worth, share.count, share.percent)
        for share in portfolio.products
    ] == [
        ("TREA-91", ugx(7_000_000), 2, 70),
        ("TREA-182", ugx(3_000_000), 1, 30),
    ]
    upcoming = portfolio.next_maturity
    assert upcoming is not None
    assert upcoming.investment_id in {bill.id, saved}
    assert (upcoming.on, upcoming.term_days, upcoming.days_earned) == (bill.maturity_date, 91, 0)
    assert upcoming.days_left == 91


async def test_a_goal_that_is_closed_no_longer_holds_its_savings(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    saved = await save_to_new_goal(app_database, worker_database, client, 1_000_000)
    async with app_database.actor_transaction(client) as session:
        (goal,) = await goals.client_goals(session, client, NOW)
        await goals.close_goal(session, client, goal.id, NOW)
    portfolio = await read(app_database, client)
    assert portfolio.in_goals == insights.Holding(ugx(0), 0)
    assert portfolio.outside_goals.count == 1
    assert portfolio.open_goals == 0
    assert saved


async def test_an_early_withdrawal_stops_the_daily_figure(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await start(app_database, worker_database, client, amount=1_000_000)
    destination = await approved_destination(app_database, client, NOW)
    later = at(TODAY + timedelta(days=2))
    async with app_database.actor_transaction(client) as session:
        await investments.request_withdrawal(
            session,
            client,
            investment.id,
            destination_id=destination,
            idempotency_key=key(),
            clock=later,
        )
    async with app_database.actor_transaction(client) as session:
        portfolio = await insights.client_portfolio(session, client, later)
    assert portfolio.active_count == 1
    assert portfolio.earning_per_day == ugx(0)
    assert portfolio.next_maturity is None


async def test_each_currency_stands_alone(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    await start(app_database, worker_database, client, amount=1_000_000)
    dollars = await read(app_database, client, Currency.USD)
    assert dollars.currency is Currency.USD
    assert dollars.worth == Money(0, Currency.USD)
    assert dollars.active_count == 0


async def test_a_client_never_sees_another_clients_totals(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    await start(app_database, worker_database, client, amount=1_000_000)
    other = new_client()
    portfolio = await read(app_database, other)
    assert portfolio.worth == ugx(0)
    assert portfolio.active_count == 0


async def test_only_a_client_has_a_portfolio(app_database: Database) -> None:
    for actor in (staff(), SYSTEM):
        with pytest.raises(ApplicationError) as error:
            async with app_database.actor_transaction(actor) as session:
                await insights.client_portfolio(session, actor, FakeClock())
        assert error.value.code == "forbidden"


async def test_the_route_serves_the_signed_in_clients_own_totals(
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
        assert (await http.get("/client/portfolio")).status_code == 401
        identity = app.state.identity
        client_id = await identity.create_client(
            phone="+256709123988", email=None, password="copper-harbour-violin"
        )
        issued = await identity.issue_session(
            Actor("client", client_id), second_step="signup", transport="token"
        )
        bearer = {"Authorization": f"Bearer {issued.token}"}
        response = await http.get("/client/portfolio", headers=bearer)
        assert response.status_code == 200
        assert response.json() == {
            "currency": "UGX",
            "invested_minor": 0,
            "worth_minor": 0,
            "interest_so_far_minor": 0,
            "earning_per_day_minor": 0,
            "interest_today_minor": 0,
            "active_count": 0,
            "awaiting_payment_count": 0,
            "outside_goals": {"worth_minor": 0, "count": 0},
            "in_goals": {"worth_minor": 0, "count": 0},
            "open_goals": 0,
            "products": [],
            "next_maturity": None,
        }
        dollars = await http.get("/client/portfolio", params={"currency": "USD"}, headers=bearer)
        assert dollars.json()["currency"] == "USD"
        unknown = await http.get("/client/portfolio", params={"currency": "EUR"}, headers=bearer)
        assert unknown.status_code == 422
