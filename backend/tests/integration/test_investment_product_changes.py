"""Creating, editing, pausing and opening products against real PostgreSQL.

Products are never removed and tests share one database, so every test makes its own product
with its own code, and any product a test opens is paused again before the test ends: other
suites list the live catalogue and expect only the seeded products in it.
"""

import asyncio
import itertools
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import app.api.app  # noqa: F401 - loads the modules in the order the app does, which has no cycle
from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.autoinvest import service as autoinvest
from app.modules.goals import service as goals
from app.modules.investments import service as investments
from app.modules.investments.domain.products import (
    InterestPaid,
    ProductKind,
    ProductStatus,
)
from app.modules.investments.domain.rates import ProposalStatus
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

_codes = itertools.count(1)


def at(day: date) -> FakeClock:
    """Midday in Kampala on `day`."""
    return FakeClock(datetime(day.year, day.month, day.day, 9, tzinfo=UTC))


NOW = at(date(2026, 11, 13))


def fresh_code() -> str:
    return f"TEST-{next(_codes)}"


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


def new_product(code: str, **overrides: object) -> investments.NewProduct:
    values: dict[str, object] = {
        "code": code,
        "name": "364 Day Treasury Bill",
        "kind": ProductKind.TREASURY_BILL,
        "currency": Currency.UGX,
        "term_days": 364,
        "minimum_minor": 500_000,
        "interest_paid": InterestPaid.AT_MATURITY,
        "annual_rate_bps": 1500,
        "tax_bps": 1500,
    }
    values.update(overrides)
    return investments.NewProduct(**values)  # type: ignore[arg-type]


async def propose_new(
    database: Database, staff: Actor, code: str, **overrides: object
) -> investments.ProductChangeView:
    async with database.actor_transaction(staff) as session:
        return await investments.propose_new_product(
            session, staff, new_product(code, **overrides), "The 364 day bill", NOW
        )


async def approve(
    database: Database, staff: Actor, change_id: str, clock: FakeClock = NOW
) -> investments.ProductChangeView:
    async with database.actor_transaction(staff) as session:
        return await investments.approve_product_change(session, staff, change_id, clock)


async def created(database: Database) -> str:
    """A draft product made and approved by two people. Its code."""
    code = fresh_code()
    change = await propose_new(database, new_staff(), code)
    await approve(database, new_staff(), change.id)
    return code


async def set_status(database: Database, code: str, to: ProductStatus) -> None:
    maker, checker = new_staff(), new_staff()
    async with database.actor_transaction(maker) as session:
        change = await investments.propose_status(session, maker, code, to, "Test", NOW)
    await approve(database, checker, change.id)


async def product(database: Database, code: str) -> investments.ProductView:
    async with database.actor_transaction(new_staff()) as session:
        return await investments.get_product(session, code, NOW)


# New products ------------------------------------------------------------------------------------


async def test_a_new_product_waits_for_a_second_person_then_starts_as_a_draft(
    app_database: Database,
) -> None:
    daniel, grace, code = new_staff(), new_staff(), fresh_code()
    change = await propose_new(app_database, daniel, code)
    assert (change.action, change.status) == (investments.ProductAction.CREATE, "proposed")
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as missing:
            await investments.get_product(session, code, NOW)
    assert missing.value.code == "not_found"

    approved = await approve(app_database, grace, change.id)
    assert (approved.status, approved.decided_by) == (ProposalStatus.APPROVED, grace.id)
    view = await product(app_database, code)
    assert view.product.status is ProductStatus.DRAFT
    assert view.product.minimum.minor == 500_000
    assert view.rate is not None
    assert (view.rate.annual_rate_bps, view.rate.tax_bps) == (1500, 1500)
    assert view.rate.effective_from == date(2026, 11, 13)

    # A draft is nowhere a client or the website looks.
    async with app_database.actor_transaction(new_client()) as session:
        assert code not in [p.code for p in await investments.client_products(session, NOW)]
    async with app_database.transaction() as session:
        assert code not in [p.code for p in await investments.public_products(session, NOW)]

    async with app_database.actor_transaction(grace) as session:
        trail = await events_for_resource(session, investments.CHANGE_RESOURCE, change.id)
    # Newest first.
    assert [event.action for event in trail] == [
        "investments.product.create_approved",
        "investments.product.create_proposed",
    ]


async def test_nobody_approves_their_own_new_product(app_database: Database) -> None:
    daniel, code = new_staff(), fresh_code()
    change = await propose_new(app_database, daniel, code)
    with pytest.raises(ApplicationError) as error:
        await approve(app_database, daniel, change.id)
    assert error.value.code == "forbidden"


async def test_a_code_is_taken_by_a_product_or_by_a_new_one_waiting(
    app_database: Database,
) -> None:
    with pytest.raises(ApplicationError) as seeded:
        await propose_new(app_database, new_staff(), "TREA-91")
    assert refused(seeded) == ("validation_failed", [("code", "code_taken")])
    code = fresh_code()
    await propose_new(app_database, new_staff(), code)
    with pytest.raises(ApplicationError) as waiting:
        await propose_new(app_database, new_staff(), code)
    assert refused(waiting) == ("validation_failed", [("code", "code_taken")])


async def test_a_product_that_contradicts_itself_names_the_field(app_database: Database) -> None:
    with pytest.raises(ApplicationError) as open_term:
        await propose_new(app_database, new_staff(), fresh_code(), term_days=None)
    assert refused(open_term) == ("validation_failed", [("interest_paid", "invalid")])
    with pytest.raises(ApplicationError) as dollars:
        await propose_new(app_database, new_staff(), fresh_code(), annual_rate_bps=0)
    assert refused(dollars) == ("validation_failed", [("annual_rate_bps", "out_of_range")])


# New terms, pausing and opening --------------------------------------------------------------


async def test_new_terms_change_the_product_for_new_investments(app_database: Database) -> None:
    code = await created(app_database)
    daniel, grace = new_staff(), new_staff()
    terms = investments.NewTerms(
        name="364 Day T-Bill",
        kind=ProductKind.TREASURY_BILL,
        term_days=357,
        minimum_minor=250_000,
        interest_paid=InterestPaid.AT_MATURITY,
    )
    async with app_database.actor_transaction(daniel) as session:
        change = await investments.propose_new_terms(
            session, daniel, code, terms, "Match the auction calendar", NOW
        )
    assert (await product(app_database, code)).product.term_days == 364
    await approve(app_database, grace, change.id)
    after = (await product(app_database, code)).product
    assert (after.name, after.term_days, after.minimum.minor) == ("364 Day T-Bill", 357, 250_000)


async def test_a_product_opens_and_pauses_each_with_a_second_person(
    app_database: Database,
) -> None:
    code = await created(app_database)
    try:
        await set_status(app_database, code, ProductStatus.LIVE)
        async with app_database.actor_transaction(new_client()) as session:
            live = [p.code for p in await investments.client_products(session, NOW)]
        assert code in live
        with pytest.raises(ApplicationError) as again:
            await set_status(app_database, code, ProductStatus.LIVE)
        assert refused(again) == ("validation_failed", [("status", "already_live")])
    finally:
        await set_status(app_database, code, ProductStatus.PAUSED)
    assert (await product(app_database, code)).product.status is ProductStatus.PAUSED
    async with app_database.transaction() as session:
        website = {p.code: p.status for p in await investments.public_products(session, NOW)}
    assert website[code] is ProductStatus.PAUSED


async def test_the_dollar_product_cannot_be_opened_yet(app_database: Database) -> None:
    with pytest.raises(ApplicationError) as error:
        await set_status(app_database, "USD-BOND", ProductStatus.LIVE)
    assert refused(error) == ("validation_failed", [("currency", "currency_not_open")])
    assert (await product(app_database, "USD-BOND")).product.status is ProductStatus.PAUSED


async def test_a_live_product_keeps_its_kind(app_database: Database) -> None:
    daniel = new_staff()
    terms = investments.NewTerms(
        name="91 Day Treasury Bill",
        kind=ProductKind.FIXED_INCOME,
        term_days=91,
        minimum_minor=100_000,
        interest_paid=InterestPaid.AT_MATURITY,
    )
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.propose_new_terms(session, daniel, "TREA-91", terms, "Why", NOW)
    assert refused(error) == ("validation_failed", [("kind", "kind_fixed")])


async def test_a_decided_change_is_never_decided_again(app_database: Database) -> None:
    code = await created(app_database)
    daniel, grace = new_staff(), new_staff()
    terms = investments.NewTerms(
        name="Renamed",
        kind=ProductKind.TREASURY_BILL,
        term_days=364,
        minimum_minor=500_000,
        interest_paid=InterestPaid.AT_MATURITY,
    )
    async with app_database.actor_transaction(daniel) as session:
        taken_back = await investments.propose_new_terms(session, daniel, code, terms, "1", NOW)
        await investments.withdraw_product_change(session, daniel, taken_back.id, NOW)
    with pytest.raises(ApplicationError) as withdrawn:
        await approve(app_database, grace, taken_back.id)
    assert withdrawn.value.code == "conflict"
    async with app_database.actor_transaction(daniel) as session:
        landed = await investments.propose_new_terms(session, daniel, code, terms, "2", NOW)
    await approve(app_database, grace, landed.id)
    with pytest.raises(ApplicationError) as again:
        await approve(app_database, new_staff(), landed.id)
    assert again.value.code == "conflict"
    assert (await product(app_database, code)).product.name == "Renamed"


async def test_one_change_waits_per_product(app_database: Database) -> None:
    code = await created(app_database)
    daniel = new_staff()
    async with app_database.actor_transaction(daniel) as session:
        await investments.propose_status(session, daniel, code, ProductStatus.LIVE, "Launch", NOW)
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.propose_status(
                session, daniel, code, ProductStatus.LIVE, "Launch again", NOW
            )
    assert error.value.code == "conflict"


async def test_sending_back_needs_a_note_and_leaves_the_product_alone(
    app_database: Database,
) -> None:
    code = await created(app_database)
    daniel, grace = new_staff(), new_staff()
    async with app_database.actor_transaction(daniel) as session:
        change = await investments.propose_status(
            session, daniel, code, ProductStatus.LIVE, "Launch", NOW
        )
    async with app_database.actor_transaction(grace) as session:
        back = await investments.send_back_product_change(
            session, grace, change.id, "Wait for the auction", NOW
        )
    assert (back.status, back.decision_note) == (ProposalStatus.SENT_BACK, "Wait for the auction")
    assert (await product(app_database, code)).product.status is ProductStatus.DRAFT


# The database holds the rules itself -------------------------------------------------------


async def test_the_application_cannot_write_a_product_without_an_approved_change(
    app_database: Database,
) -> None:
    staff = new_staff()
    with pytest.raises(DBAPIError, match="approved change"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("UPDATE investment_products SET minimum_minor = 1 WHERE code = 'TREA-91'")
            )
    with pytest.raises(DBAPIError, match=r"never removed|permission denied"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text("DELETE FROM investment_products WHERE code = 'TREA-91'"))


async def test_an_approved_change_cannot_be_replayed_onto_its_product(
    app_database: Database,
) -> None:
    code = await created(app_database)
    daniel, grace = new_staff(), new_staff()
    async with app_database.actor_transaction(daniel) as session:
        change = await investments.propose_status(
            session, daniel, code, ProductStatus.LIVE, "Launch", NOW
        )
    await approve(app_database, grace, change.id)
    try:
        await set_status(app_database, code, ProductStatus.PAUSED)
        # Writing the old approved opening again, as the person who approved it, is refused.
        with pytest.raises(DBAPIError, match="one approved change at a time"):
            async with app_database.actor_transaction(grace) as session:
                await session.execute(
                    text(
                        "UPDATE investment_products SET status = 'live', change_id = :change, "
                        "version = version + 1 WHERE code = :code"
                    ),
                    {"change": change.id, "code": code},
                )
    finally:
        assert (await product(app_database, code)).product.status is ProductStatus.PAUSED


async def test_a_first_rate_needs_the_approved_new_product(app_database: Database) -> None:
    code = fresh_code()
    daniel = new_staff()
    change = await propose_new(app_database, daniel, code)
    with pytest.raises(DBAPIError, match="first rate"):
        async with app_database.actor_transaction(daniel) as session:
            await session.execute(
                text(
                    "INSERT INTO investment_rates (id, product_id, effective_from, "
                    "annual_rate_bps, tax_bps, product_change_id, created_at) SELECT "
                    "'FvRateTestFirst00001', id, DATE '2030-01-01', 1500, 1500, :change, now() "
                    "FROM investment_products WHERE code = 'TREA-91'"
                ),
                {"change": change.id},
            )


async def test_an_approval_that_writes_no_product_is_refused_at_commit(
    app_database: Database,
) -> None:
    change = await propose_new(app_database, new_staff(), fresh_code())
    grace = new_staff()
    with pytest.raises(DBAPIError, match="written to the catalogue"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(
                text(
                    "UPDATE investment_product_changes SET status = 'approved', "
                    "decided_by = :me, decided_at = now() WHERE id = :id"
                ),
                {"me": grace.id, "id": change.id},
            )


# Two people at once -----------------------------------------------------------------------------


async def test_two_people_approving_at_once_make_one_product(app_database: Database) -> None:
    code = fresh_code()
    change = await propose_new(app_database, new_staff(), code)
    barrier = AsyncBarrier(2)

    async def decide() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await investments.approve_product_change(session, staff, change.id, NOW)
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(decide(), decide())) == ["approved", "conflict"]
    assert (await product(app_database, code)).product.status is ProductStatus.DRAFT


async def test_two_new_products_with_one_code_at_once_leave_one_waiting(
    app_database: Database,
) -> None:
    code = fresh_code()
    barrier = AsyncBarrier(2)

    async def propose() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await investments.propose_new_product(
                    session, staff, new_product(code), "Same code", NOW
                )
        except ApplicationError as error:
            return error.code
        return "proposed"

    outcomes = sorted(await asyncio.gather(propose(), propose()))
    assert outcomes in (["proposed", "validation_failed"], ["conflict", "proposed"])


# Who a change reaches ------------------------------------------------------------------------


async def test_the_reach_of_a_new_product_is_nobody(app_database: Database) -> None:
    code = await created(app_database)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        reach = await investments.product_reach(session, staff, code)
        saving = await goals.open_goals_in_product(session, staff, code)
        plans = await autoinvest.plan_counts(session, staff, product_code=code)
    assert (reach.running, reach.awaiting_payment, reach.clients) == (0, 0, 0)
    assert (saving, plans.active, plans.paused) == (0, 0, 0)


async def test_the_reach_counts_what_the_database_holds(
    app_database: Database, migrator_database: Database
) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        reach = await investments.product_reach(session, staff, "TREA-182")
    async with migrator_database.transaction() as session:
        counts = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE i.status = 'active'), "
                    "count(*) FILTER (WHERE i.status = 'awaiting_payment'), "
                    "count(DISTINCT i.client_id) FILTER (WHERE i.status IN "
                    "('active', 'awaiting_payment')) FROM investments i "
                    "JOIN investment_products p ON p.id = i.product_id WHERE p.code = 'TREA-182'"
                )
            )
        ).one()
    assert (reach.running, reach.awaiting_payment, reach.clients) == tuple(counts)


async def test_clients_never_read_product_changes(app_database: Database) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.list_product_changes(session, client)
    assert error.value.code == "forbidden"
    async with app_database.actor_transaction(client) as session:
        rows = await session.execute(text("SELECT count(*) FROM investment_product_changes"))
    assert rows.scalar_one() == 0
