"""Sending on the money a matured investment holds, by two members of staff, against real
PostgreSQL: one proposes another of the client's payout accounts, a different one approves,
and the worker makes the payout the client's own request would make, under the same key."""

from datetime import timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.investments import corrections
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity
from app.modules.payouts import payouts
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from tests.support.investments import (
    PRESENT,
    approved_client,
    approved_destination,
    at,
    pay,
    request,
    started,
    today,
)
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

DAY = today()
MATURED = DAY + timedelta(days=91)
# The day after: the client calls about the money, past any new account's safety wait.
CALLED = at(MATURED + timedelta(days=1))
Kind = corrections.CorrectionKind
Status = corrections.CorrectionStatus


async def held(app_database: Database, worker_database: Database) -> investments.Investment:
    """A matured investment whose client chose to be paid but never said where."""
    client = await approved_client(app_database, PRESENT)
    chosen = await request(app_database, client, PRESENT, at_maturity=AtMaturity.PAY_OUT)
    await pay(worker_database, chosen, chosen.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, chosen.id, confirmed_on=DAY, clock=PRESENT)
        await investments.mature_investment(session, chosen.id, MATURED, at(MATURED))
        matured = await investments.get_investment(session, chosen.id)
    assert matured.payout_id is None and matured.payout_refusal == "no_destination"
    return matured


async def propose(
    app_database: Database, investment_id: str, destination_id: str | None
) -> corrections.Correction:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        return await investments.propose_correction(
            session,
            staff,
            kind=Kind.PAY_OUT,
            investment_id=investment_id,
            reason="The client called and asked for it to go to their other number",
            destination_id=destination_id,
            clock=CALLED,
        )


async def approve(app_database: Database, correction_id: str) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await investments.approve_correction(session, staff, correction_id, CALLED)


async def apply(worker_database: Database, correction_id: str) -> corrections.Correction:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await corrections.apply_correction(session, correction_id, CALLED)


async def test_two_staff_send_held_money_to_another_account_of_the_clients(
    app_database: Database, worker_database: Database
) -> None:
    investment = await held(app_database, worker_database)
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, PRESENT)

    proposed = await propose(app_database, investment.id, destination_id)
    # The whole of what the term handed over: principal and interest after tax.
    assert proposed.amount == investment.amount + investment.accrued_net
    assert investment.accrued_net.is_positive
    await approve(app_database, proposed.id)
    done = await apply(worker_database, proposed.id)

    assert (done.status, done.entry_id) == (Status.DONE, None)
    assert done.payout_id is not None
    async with app_database.actor_transaction(client) as session:
        payout = await payouts.get_payout(session, done.payout_id)
    assert (payout.amount, payout.destination_id) == (proposed.amount, destination_id)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        after = await investments.get_investment(session, investment.id)
        view = await investments.maturities(session, staff, CALLED)
    assert (after.payout_id, after.payout_refusal) == (done.payout_id, None)
    assert investment.id not in [i.id for i in view.held]

    # The client cannot send it again from the app, and running the job again changes nothing.
    with pytest.raises(ApplicationError) as again:
        async with app_database.actor_transaction(client) as session:
            await investments.pay_out_matured(
                session, client, investment.id, destination_id=destination_id, clock=CALLED
            )
    assert again.value.code == "conflict"
    assert (await apply(worker_database, proposed.id)).payout_id == done.payout_id


async def test_money_the_client_sent_on_meanwhile_is_left_alone(
    app_database: Database, worker_database: Database
) -> None:
    investment = await held(app_database, worker_database)
    client = Actor("client", investment.client_id)
    theirs = await approved_destination(app_database, client, PRESENT)
    other = await approved_destination(app_database, client, PRESENT)
    proposed = await propose(app_database, investment.id, other)
    await approve(app_database, proposed.id)
    # Before the worker gets to it, the client chooses in the app.
    async with app_database.actor_transaction(client) as session:
        sent = await investments.pay_out_matured(
            session, client, investment.id, destination_id=theirs, clock=CALLED
        )

    failed = await apply(worker_database, proposed.id)

    assert (failed.status, failed.failure_code, failed.payout_id) == (
        Status.FAILED,
        "investment_not_held",
        None,
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert (await investments.get_investment(session, investment.id)).payout_id == (
            sent.payout_id
        )


async def test_only_held_money_and_only_to_an_account_of_the_clients(
    app_database: Database, worker_database: Database
) -> None:
    running = await started(app_database, worker_database, PRESENT)
    destination_id = await approved_destination(
        app_database, Actor("client", running.client_id), PRESENT
    )
    with pytest.raises(ApplicationError) as not_matured:
        await propose(app_database, running.id, destination_id)
    assert not_matured.value.code == "conflict"

    investment = await held(app_database, worker_database)
    with pytest.raises(ApplicationError) as nowhere:
        await propose(app_database, investment.id, None)
    assert nowhere.value.code == "validation_failed"
    # Another client's account is not this client's.
    with pytest.raises(ApplicationError) as someone_elses:
        await propose(app_database, investment.id, destination_id)
    assert [f.code for f in someone_elses.value.fields] == ["not_usable"]
