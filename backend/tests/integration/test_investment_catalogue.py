"""The product catalogue and rate approval against real PostgreSQL."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.investments import service as investments
from app.modules.investments.domain.interest import ClientReturns
from app.modules.investments.domain.products import SEED_PRODUCTS
from app.modules.investments.domain.rates import SEED_RATES, ProposalStatus, RateVersion
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

# Rates cannot be deleted, so every test proposes its own effective date. Tests share one
# database, and a product has one open proposal at a time, so they also run one after another
# on the same product only when they clean up by deciding what they proposed.
_dates = iter(date(2030, 1, 1 + day) for day in range(28))
_rate_steps = iter(range(1, 1000))


def at(day: date) -> FakeClock:
    """Midday in Kampala on `day`."""
    return FakeClock(datetime(day.year, day.month, day.day, 9, tzinfo=UTC))


NOW = at(date(2026, 11, 13))


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def propose(
    database: Database, staff: Actor, *, code: str = "TREA-182", clock: FakeClock = NOW
) -> investments.ProposalView:
    async with database.actor_transaction(staff) as session:
        return await investments.propose_rate(
            session,
            staff,
            product_code=code,
            # A different rate every time, so a proposal always changes something.
            annual_rate_bps=1000 + next(_rate_steps),
            tax_bps=1500,
            effective_from=next(_dates),
            reason="Auction cleared lower",
            clock=clock,
        )


async def withdraw(database: Database, staff: Actor, proposal_id: str) -> None:
    async with database.actor_transaction(staff) as session:
        await investments.withdraw_rate(session, staff, proposal_id, NOW)


# The seeded catalogue ---------------------------------------------------------------------------


async def test_the_catalogue_is_seeded_with_the_products_and_rates_in_the_rules(
    app_database: Database,
) -> None:
    async with app_database.actor_transaction(new_staff()) as session:
        views = await investments.list_products(session, NOW)
    assert [view.product for view in views] == list(SEED_PRODUCTS)
    assert {
        view.product.code: (view.rate.annual_rate_bps, view.rate.tax_bps)
        for view in views
        if view.rate is not None
    } == SEED_RATES


async def test_clients_see_live_products_with_the_rate_after_tax_only(
    app_database: Database,
) -> None:
    async with app_database.actor_transaction(new_client()) as session:
        products = await investments.client_products(session, NOW)
    assert [(p.code, p.rate_percent, p.minimum.to_major(), p.term_days) for p in products] == [
        ("TREA-91", "10.63", "100000", 91),
        ("TREA-182", "11.69", "200000", 182),
    ]
    # Nothing a client is given names the tax or the rate before it.
    assert not [name for name in investments.ClientProduct.__slots__ if "tax" in name]
    assert not [name for name in investments.ClientQuote.__slots__ if "tax" in name]


async def test_the_website_can_read_the_catalogue_with_nobody_signed_in(
    app_database: Database,
) -> None:
    async with app_database.transaction() as session:
        products = await investments.client_products(session, NOW)
    assert [product.code for product in products] == ["TREA-91", "TREA-182"]


async def test_a_quote_is_what_the_design_shows(app_database: Database) -> None:
    async with app_database.actor_transaction(new_client()) as session:
        quote = await investments.client_quote(session, "TREA-91", ugx(1_000_000), NOW)
    assert quote.returns == ClientReturns(interest=ugx(26_490), total=ugx(1_026_490))
    assert quote.starts_on == date(2026, 11, 13)
    assert quote.matures_on == date(2027, 2, 12)
    assert quote.product.rate_percent == "10.63"


@pytest.mark.parametrize(
    ("code", "amount", "problem"),
    [
        ("TREA-91", ugx(99_999), "below_minimum"),
        ("TREA-91", ugx(0), "amount_not_positive"),
        ("TREA-91", Money(100_000_00, Currency.USD), "currency_not_supported"),
        ("USD-BOND", Money(1_000_00, Currency.USD), "product_not_open"),
    ],
)
async def test_a_quote_is_refused_for_an_amount_the_product_would_not_take(
    app_database: Database, code: str, amount: Money, problem: str
) -> None:
    async with app_database.actor_transaction(new_client()) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.client_quote(session, code, amount, NOW)
    assert refused(error) == ("validation_failed", [("amount", problem)])


async def test_an_unknown_product_is_not_found(app_database: Database) -> None:
    async with app_database.actor_transaction(new_client()) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.client_quote(session, "TREA-364", ugx(1_000_000), NOW)
    assert error.value.code == "not_found"


# Changing a rate --------------------------------------------------------------------------------


async def test_a_rate_changes_when_a_second_person_approves_and_starts_on_its_date(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel, code="TREA-91")
    assert proposal.status is ProposalStatus.PROPOSED

    async with app_database.actor_transaction(grace) as session:
        approved = await investments.approve_rate(session, grace, proposal.id, NOW)
        history = await investments.rate_history(session, "TREA-91")
        today = await investments.get_product(session, "TREA-91", NOW)
        on_the_day = await investments.get_product(
            session, "TREA-91", at(proposal.rate.effective_from)
        )
        events = await events_for_resource(session, investments.PROPOSAL_RESOURCE, proposal.id)

    assert approved.status is ProposalStatus.APPROVED
    assert approved.decided_by == grace.id
    assert proposal.rate in history
    # Until the day comes, new investments still get the old rate.
    assert today.rate == RateVersion(date(2026, 1, 1), 1250, 1500)
    assert on_the_day.rate == proposal.rate
    assert [event.action for event in reversed(events)] == [
        "investments.rate.proposed",
        "investments.rate.approved",
    ]


async def test_nobody_approves_or_sends_back_their_own_proposal(app_database: Database) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel)
    try:
        async with app_database.actor_transaction(daniel) as session:
            with pytest.raises(ApplicationError) as approve_error:
                await investments.approve_rate(session, daniel, proposal.id, NOW)
            with pytest.raises(ApplicationError) as send_back_error:
                await investments.send_back_rate(session, daniel, proposal.id, "No", NOW)
        assert approve_error.value.code == "forbidden"
        assert send_back_error.value.code == "forbidden"
    finally:
        await withdraw(app_database, daniel, proposal.id)


async def test_the_database_refuses_a_self_approval_even_if_the_code_allowed_it(
    app_database: Database,
) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel)
    try:
        with pytest.raises(DBAPIError, match="ck_investment_rate_proposals_second_person"):
            async with app_database.actor_transaction(daniel) as session:
                await session.execute(
                    text(
                        "UPDATE investment_rate_proposals SET status = 'approved', "
                        "decided_by = :staff, decided_at = now() WHERE id = :id"
                    ),
                    {"staff": daniel.id, "id": proposal.id},
                )
    finally:
        await withdraw(app_database, daniel, proposal.id)


async def test_a_member_of_staff_cannot_decide_in_someone_elses_name(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel)
    try:
        with pytest.raises(DBAPIError, match="by the person signed in"):
            async with app_database.actor_transaction(daniel) as session:
                await session.execute(
                    text(
                        "UPDATE investment_rate_proposals SET status = 'approved', "
                        "decided_by = :other, decided_at = now() WHERE id = :id"
                    ),
                    {"other": grace.id, "id": proposal.id},
                )
    finally:
        await withdraw(app_database, daniel, proposal.id)


async def test_a_rate_cannot_be_added_without_an_approved_proposal(
    app_database: Database,
) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel)
    insert = text(
        "INSERT INTO investment_rates (id, product_id, effective_from, annual_rate_bps, tax_bps, "
        "proposal_id, created_at) VALUES (:id, :product_id, :effective_from, :rate, 1500, "
        ":proposal_id, now())"
    )
    values = {
        "id": push_id(),
        "product_id": proposal.product_id,
        "effective_from": proposal.rate.effective_from,
        "rate": proposal.rate.annual_rate_bps,
    }
    try:
        # With no proposal at all.
        with pytest.raises(DBAPIError, match="needs an approved proposal"):
            async with app_database.actor_transaction(daniel) as session:
                await session.execute(insert, {**values, "proposal_id": None})
        # For a proposal nobody has approved.
        with pytest.raises(DBAPIError, match="must match its approved proposal"):
            async with app_database.actor_transaction(daniel) as session:
                await session.execute(insert, {**values, "proposal_id": proposal.id})
    finally:
        await withdraw(app_database, daniel, proposal.id)


async def test_an_approved_rate_must_carry_the_proposals_own_figures(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel)
    try:
        with pytest.raises(DBAPIError, match="must match its approved proposal"):
            async with app_database.actor_transaction(grace) as session:
                await session.execute(
                    text(
                        "UPDATE investment_rate_proposals SET status = 'approved', "
                        "decided_by = :staff, decided_at = now() WHERE id = :id"
                    ),
                    {"staff": grace.id, "id": proposal.id},
                )
                await session.execute(
                    text(
                        "INSERT INTO investment_rates (id, product_id, effective_from, "
                        "annual_rate_bps, tax_bps, proposal_id, created_at) VALUES (:id, "
                        ":product_id, :effective_from, 4999, 0, :proposal_id, now())"
                    ),
                    {
                        "id": push_id(),
                        "product_id": proposal.product_id,
                        "effective_from": proposal.rate.effective_from,
                        "proposal_id": proposal.id,
                    },
                )
    finally:
        await withdraw(app_database, daniel, proposal.id)


async def test_a_rate_is_never_changed_or_removed(
    app_database: Database, migrator_database: Database
) -> None:
    for database in (app_database, migrator_database):
        for statement in (
            "UPDATE investment_rates SET annual_rate_bps = 4000",
            "DELETE FROM investment_rates",
            "TRUNCATE investment_rates",
        ):
            with pytest.raises(DBAPIError):
                async with database.actor_transaction(new_staff()) as session:
                    await session.execute(text(statement))


async def test_the_application_cannot_change_a_product(app_database: Database) -> None:
    for statement in (
        "UPDATE investment_products SET minimum_minor = 1",
        "DELETE FROM investment_products",
        "INSERT INTO investment_products SELECT * FROM investment_products",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(new_staff()) as session:
                await session.execute(text(statement))


async def test_one_change_waits_per_product_at_a_time(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    first = await propose(app_database, daniel)
    try:
        with pytest.raises(ApplicationError) as error:
            await propose(app_database, grace)
        assert error.value.code == "conflict"
    finally:
        await withdraw(app_database, daniel, first.id)
    # Once it is decided, the next one can be proposed.
    second = await propose(app_database, grace)
    await withdraw(app_database, grace, second.id)


async def test_sending_back_needs_a_note_and_leaves_the_rate_alone(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel)
    async with app_database.actor_transaction(grace) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.send_back_rate(session, grace, proposal.id, "  ", NOW)
        assert refused(error) == ("validation_failed", [("reason", "reason_required")])
        sent_back = await investments.send_back_rate(
            session, grace, proposal.id, "Check the auction result", NOW
        )
        history = await investments.rate_history(session, "TREA-182")
    assert sent_back.status is ProposalStatus.SENT_BACK
    assert sent_back.decision_note == "Check the auction result"
    assert proposal.rate not in history


async def test_a_decided_proposal_cannot_be_decided_again(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel)
    await withdraw(app_database, daniel, proposal.id)
    async with app_database.actor_transaction(grace) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.approve_rate(session, grace, proposal.id, NOW)
        assert error.value.code == "conflict"
    with pytest.raises(DBAPIError, match="never changes"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(
                text(
                    "UPDATE investment_rate_proposals SET status = 'approved', "
                    "decided_by = :staff, decided_at = now() WHERE id = :id"
                ),
                {"staff": grace.id, "id": proposal.id},
            )


async def test_only_the_proposer_withdraws(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel)
    try:
        async with app_database.actor_transaction(grace) as session:
            with pytest.raises(ApplicationError) as error:
                await investments.withdraw_rate(session, grace, proposal.id, NOW)
        assert error.value.code == "forbidden"
    finally:
        await withdraw(app_database, daniel, proposal.id)


async def test_an_approval_on_or_after_the_effective_date_is_refused(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel)
    try:
        async with app_database.actor_transaction(grace) as session:
            with pytest.raises(ApplicationError) as error:
                await investments.approve_rate(
                    session, grace, proposal.id, at(proposal.rate.effective_from)
                )
        assert refused(error) == (
            "validation_failed",
            [("effective_from", "effective_date_not_in_future")],
        )
    finally:
        await withdraw(app_database, daniel, proposal.id)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            {"effective_from": date(2026, 11, 13)},
            ("effective_from", "effective_date_not_in_future"),
        ),
        ({"effective_from": date(2026, 1, 1)}, ("effective_from", "effective_date_not_in_future")),
        ({"reason": " "}, ("reason", "reason_required")),
        ({"annual_rate_bps": 1375}, ("annual_rate_bps", "nothing_changes")),
        ({"annual_rate_bps": 5001}, ("annual_rate_bps", "out_of_range")),
        ({"tax_bps": 10_000}, ("annual_rate_bps", "out_of_range")),
    ],
)
async def test_a_proposal_that_breaks_a_rule_is_refused(
    app_database: Database, change: dict[str, object], problem: tuple[str, str]
) -> None:
    staff = new_staff()
    request: dict[str, object] = {
        "product_code": "TREA-182",
        "annual_rate_bps": 1350,
        "tax_bps": 1500,
        "effective_from": date(2029, 6, 1),
        "reason": "Auction cleared lower",
        **change,
    }
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.propose_rate(session, staff, clock=NOW, **request)  # type: ignore[arg-type]
    assert refused(error) == ("validation_failed", [problem])


@pytest.mark.parametrize("actor", [new_client(), SYSTEM])
async def test_only_staff_touch_proposals(
    app_database: Database, worker_database: Database, actor: Actor
) -> None:
    database = worker_database if actor is SYSTEM else app_database
    async with database.actor_transaction(actor) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.propose_rate(
                session,
                actor,
                product_code="TREA-182",
                annual_rate_bps=1350,
                tax_bps=1500,
                effective_from=date(2029, 6, 1),
                reason="Auction cleared lower",
                clock=NOW,
            )
        assert error.value.code == "forbidden"
        with pytest.raises(ApplicationError):
            await investments.list_proposals(session, actor)


async def test_a_client_cannot_read_or_write_proposals_in_the_database(
    app_database: Database,
) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel)
    try:
        client = new_client()
        async with app_database.actor_transaction(client) as session:
            seen = await session.execute(text("SELECT count(*) FROM investment_rate_proposals"))
            assert seen.scalar_one() == 0
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "INSERT INTO investment_rate_proposals (id, product_id, effective_from, "
                        "annual_rate_bps, tax_bps, reason, proposed_by, proposed_at) VALUES "
                        "(:id, :product_id, DATE '2031-01-01', 4000, 0, 'mine', :me, now())"
                    ),
                    {"id": push_id(), "product_id": proposal.product_id, "me": client.id},
                )
    finally:
        await withdraw(app_database, daniel, proposal.id)


async def test_staff_see_what_waits_for_approval(app_database: Database) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel)
    try:
        async with app_database.actor_transaction(new_staff()) as session:
            waiting = await investments.list_proposals(
                session, daniel, status=ProposalStatus.PROPOSED
            )
            everything = await investments.list_proposals(session, daniel)
        assert [view.id for view in waiting] == [proposal.id]
        assert proposal.id in [view.id for view in everything]
    finally:
        await withdraw(app_database, daniel, proposal.id)


# Races ------------------------------------------------------------------------------------------


async def test_two_people_approving_at_once_make_one_rate(app_database: Database) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel)
    barrier = AsyncBarrier(2)

    async def approve() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await investments.approve_rate(session, staff, proposal.id, NOW)
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(approve(), approve())) == ["approved", "conflict"]
    async with app_database.actor_transaction(daniel) as session:
        history = await investments.rate_history(session, "TREA-182")
    assert history.count(proposal.rate) == 1


async def test_an_approval_racing_a_withdrawal_ends_one_way(app_database: Database) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel)
    barrier = AsyncBarrier(2)

    async def approve() -> str:
        try:
            async with app_database.actor_transaction(grace) as session:
                await barrier.wait()
                await investments.approve_rate(session, grace, proposal.id, NOW)
        except ApplicationError as error:
            return error.code
        return "approved"

    async def take_back() -> str:
        try:
            async with app_database.actor_transaction(daniel) as session:
                await barrier.wait()
                await investments.withdraw_rate(session, daniel, proposal.id, NOW)
        except ApplicationError as error:
            return error.code
        return "withdrawn"

    outcomes = sorted(await asyncio.gather(approve(), take_back()))
    async with app_database.actor_transaction(daniel) as session:
        history = await investments.rate_history(session, "TREA-182")
        final = (await investments.list_proposals(session, daniel))[0]
    assert outcomes in (["approved", "conflict"], ["conflict", "withdrawn"])
    assert (proposal.rate in history) is (final.status is ProposalStatus.APPROVED)
