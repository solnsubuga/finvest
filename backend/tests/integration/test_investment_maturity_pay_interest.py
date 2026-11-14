"""Paying the interest at maturity and keeping the principal invested, against real PostgreSQL:
the principal rolls into a new term with the same choice, the interest is paid in the same
entry, and interest with nowhere to go waits for the client like any held maturity money."""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.investments import accrual, corrections
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.investments.holdings import HOLDING
from app.modules.ledger import service as ledger
from app.modules.payouts import payouts
from app.modules.payouts.domain import posting as payout_posting
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.investments import (
    PRESENT,
    account_balance,
    approved_client,
    approved_destination,
    at,
    pay,
    request,
    started,
    today,
)
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

DAY = today()
MATURED, ACTIVE = InvestmentStatus.MATURED, InvestmentStatus.ACTIVE
PRINCIPAL = 1_000_000
INTEREST = 26_490


def day(offset: int) -> date:
    return DAY + timedelta(days=offset)


async def mature(
    worker_database: Database, investment_id: str, offset: int = 91
) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.mature_investment(
            session, investment_id, day(offset), at(day(offset))
        )


async def reload(worker_database: Database, investment_id: str) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.get_investment(session, investment_id)


async def handed_over(worker_database: Database, investment: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            payout_posting.funding_account(investment.client_id, investment.id, Currency.UGX),
            FakeClock(),
        )
        return (await ledger.get_balance(session, account.id)).minor


async def interest_owed(worker_database: Database, investment: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session, accrual.interest_account(investment), FakeClock()
        )
        return (await ledger.get_balance(session, account.id)).minor


async def choose_interest(app_database: Database, investment: investments.Investment) -> str:
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, PRESENT)
    async with app_database.actor_transaction(client) as session:
        await investments.choose_at_maturity(
            session,
            client,
            investment.id,
            at_maturity=AtMaturity.PAY_INTEREST,
            destination_id=destination_id,
            clock=PRESENT,
        )
    return destination_id


async def test_the_interest_is_paid_and_the_principal_starts_another_term(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, PRESENT)
    destination_id = await choose_interest(app_database, investment)

    old = await mature(worker_database, investment.id)

    assert (old.status, old.accrued_net, old.payout_refusal) == (MATURED, ugx(INTEREST), None)
    assert old.rolled_into_id is not None and old.payout_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        payout = await payouts.get_payout(session, old.payout_id)
    assert (payout.amount, payout.destination_id, payout.source_reference) == (
        ugx(INTEREST),
        destination_id,
        old.id,
    )
    new = await reload(worker_database, old.rolled_into_id)
    assert (new.status, new.amount, new.rolled_from_id) == (ACTIVE, ugx(PRINCIPAL), old.id)
    assert (new.start_date, new.maturity_date) == (day(91), day(182))
    # The new term keeps the choice, so the client is paid the interest every term.
    assert (new.at_maturity, new.payout_destination_id) == (AtMaturity.PAY_INTEREST, destination_id)
    assert await account_balance(worker_database, old, HOLDING) == 0
    assert await interest_owed(worker_database, old) == 0
    assert await account_balance(worker_database, new, HOLDING) == PRINCIPAL
    assert await handed_over(worker_database, old) == 0
    assert old.waiting is None

    # The next term ends the same way.
    again = await mature(worker_database, new.id, offset=182)
    assert again.rolled_into_id is not None and again.payout_id is not None
    third = await reload(worker_database, again.rolled_into_id)
    assert (third.amount, third.at_maturity) == (ugx(PRINCIPAL), AtMaturity.PAY_INTEREST)


async def test_interest_with_nowhere_to_go_waits_and_the_client_sends_it_on(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    chosen = await request(app_database, client, PRESENT, at_maturity=AtMaturity.PAY_INTEREST)
    await pay(worker_database, chosen, chosen.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, chosen.id, confirmed_on=DAY, clock=PRESENT)

    held = await mature(worker_database, chosen.id)

    assert (held.payout_id, held.payout_refusal) == (None, "no_destination")
    assert held.rolled_into_id is not None
    new = await reload(worker_database, held.rolled_into_id)
    assert (new.amount, new.at_maturity, new.payout_destination_id) == (
        ugx(PRINCIPAL),
        AtMaturity.PAY_INTEREST,
        None,
    )
    # Only the interest waits.
    assert held.waiting == ugx(INTEREST)
    assert await handed_over(worker_database, held) == INTEREST
    async with app_database.actor_transaction(client) as session:
        view = await investments.client_investment(session, client, held.id, at(day(92)))
    assert (view.waiting_for_destination, view.waiting) == (True, ugx(INTEREST))
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        board = await investments.maturities(session, staff, at(day(92)))
    assert held.id in [i.id for i in board.held]

    destination_id = await approved_destination(app_database, client, PRESENT)
    async with app_database.actor_transaction(client) as session:
        sent = await investments.pay_out_matured(
            session, client, held.id, destination_id=destination_id, clock=at(day(93))
        )
    assert (sent.payout_refusal, sent.rolled_into_id) == (None, held.rolled_into_id)
    assert sent.payout_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        payout = await payouts.get_payout(session, sent.payout_id)
    assert payout.amount == ugx(INTEREST)
    assert await handed_over(worker_database, sent) == 0
    with pytest.raises(ApplicationError) as again:
        async with app_database.actor_transaction(client) as session:
            await investments.pay_out_matured(
                session, client, held.id, destination_id=destination_id, clock=at(day(93))
            )
    assert again.value.code == "conflict"


async def test_staff_send_held_interest_on_for_the_interest_alone(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    chosen = await request(app_database, client, PRESENT, at_maturity=AtMaturity.PAY_INTEREST)
    await pay(worker_database, chosen, chosen.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, chosen.id, confirmed_on=DAY, clock=PRESENT)
    held = await mature(worker_database, chosen.id)
    destination_id = await approved_destination(app_database, client, PRESENT)

    proposer = new_staff()
    async with app_database.actor_transaction(proposer) as session:
        proposed = await investments.propose_correction(
            session,
            proposer,
            kind=corrections.CorrectionKind.PAY_OUT,
            investment_id=held.id,
            reason="The client called and named their account",
            destination_id=destination_id,
            clock=at(day(93)),
        )
    assert proposed.amount == ugx(INTEREST)
    approver = new_staff()
    async with app_database.actor_transaction(approver) as session:
        await investments.approve_correction(session, approver, proposed.id, at(day(93)))
    async with worker_database.actor_transaction(SYSTEM) as session:
        done = await corrections.apply_correction(session, proposed.id, at(day(93)))
    assert done.status is corrections.CorrectionStatus.DONE
    assert (await reload(worker_database, held.id)).payout_id == done.payout_id


async def test_paying_the_interest_names_where_to(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, PRESENT)
    client = Actor("client", investment.client_id)
    with pytest.raises(ApplicationError) as nowhere:
        async with app_database.actor_transaction(client) as session:
            await investments.choose_at_maturity(
                session,
                client,
                investment.id,
                at_maturity=AtMaturity.PAY_INTEREST,
                destination_id=None,
                clock=PRESENT,
            )
    assert [(f.field_path[0], f.code) for f in nowhere.value.fields] == [
        ("destination_id", "required")
    ]


async def test_only_a_rollover_of_a_term_paying_its_interest_carries_a_destination(
    app_database: Database, worker_database: Database
) -> None:
    # A plain rollover keeps no destination, even one the old term had.
    investment = await started(app_database, worker_database, PRESENT)
    destination_id = await choose_interest(app_database, investment)
    paying = await reload(worker_database, investment.id)
    other = await started(app_database, worker_database, PRESENT)
    insert = text(
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
        "annual_rate_bps, tax_bps, term_days, at_maturity, payout_destination_id, created_at, "
        "pay_by, updated_at, rolled_from_id) VALUES (:id, :client_id, 'FvProduct1TREA91_001', "
        "'UGX', 1000000, 1250, 1500, 91, 'pay_interest', :destination, now(), "
        "now() + interval '72 hours', now(), :origin)"
    )
    for origin, destination in ((other.id, destination_id), (paying.id, push_id())):
        with pytest.raises(DBAPIError, match="begins unpaid"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    insert,
                    {
                        "id": push_id(),
                        "client_id": paying.client_id,
                        "destination": destination,
                        "origin": origin,
                    },
                )
