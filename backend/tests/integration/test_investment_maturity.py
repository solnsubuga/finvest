"""Maturity against real PostgreSQL: rolling over, paying out, holding, and the fences."""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.audit import service as audit
from app.modules.investments import accrual, maturity
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.investments.holdings import HOLDING
from app.modules.investments.jobs import MATURE_KIND, TELL_MATURING_KIND, InvestmentJobs
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
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

DAY = today()
NOW = PRESENT
MATURED, ACTIVE = InvestmentStatus.MATURED, InvestmentStatus.ACTIVE
PROCEEDS = 1_026_490


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


async def choose(
    app_database: Database,
    investment: investments.Investment,
    at_maturity: AtMaturity,
    destination_id: str | None = None,
    *,
    client: Actor | None = None,
    clock: FakeClock = NOW,
) -> investments.Investment:
    client = client or Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        return await investments.choose_at_maturity(
            session,
            client,
            investment.id,
            at_maturity=at_maturity,
            destination_id=destination_id,
            clock=clock,
        )


async def interest_owed(worker_database: Database, investment: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session, accrual.interest_account(investment), FakeClock()
        )
        return (await ledger.get_balance(session, account.id)).minor


async def handed_over(worker_database: Database, investment: investments.Investment) -> int:
    """What waits in the client's payout pending account for this investment."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            payout_posting.funding_account(investment.client_id, investment.id, Currency.UGX),
            FakeClock(),
        )
        return (await ledger.get_balance(session, account.id)).minor


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


# Rolling over -----------------------------------------------------------------------------------


async def test_a_matured_investment_rolls_into_a_new_one_with_its_interest(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)

    old = await mature(worker_database, investment.id)

    assert old.status is MATURED
    assert (old.accrued_through, old.accrued_net) == (day(91), ugx(26_490))
    assert (old.payout_id, old.payout_refusal) == (None, None)
    assert old.rolled_into_id is not None
    new = await reload(worker_database, old.rolled_into_id)
    assert (new.status, new.amount, new.rolled_from_id) == (ACTIVE, ugx(PROCEEDS), old.id)
    assert (new.start_date, new.maturity_date) == (day(91), day(182))
    assert (new.terms.annual_rate_bps, new.terms.tax_bps) == (1250, 1500)
    assert new.at_maturity is AtMaturity.ROLL_OVER
    assert await account_balance(worker_database, old, HOLDING) == 0
    assert await interest_owed(worker_database, old) == 0
    assert await account_balance(worker_database, new, HOLDING) == PROCEEDS
    assert await handed_over(worker_database, old) == 0


async def test_the_choice_and_the_maturity_are_audited_and_the_client_is_told(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    await choose(app_database, investment, AtMaturity.ROLL_OVER)
    await mature(worker_database, investment.id)

    async with app_database.actor_transaction(new_staff()) as session:
        events = await audit.events_for_resource(session, "investment", investment.id)
    actions = [(event.action, event.actor_kind) for event in events]
    assert actions[:2] == [
        ("investments.matured", "system"),
        ("investments.maturity_chosen", "client"),
    ]
    assert events[0].details == {"step": "roll_over"}
    assert events[1].details == {"at_maturity": "roll_over", "destination_id": None}
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        told = (
            await session.execute(
                text(
                    "SELECT body, link_id FROM notifications "
                    "WHERE client_id = :client AND event = 'investment_matured'"
                ),
                {"client": client.id},
            )
        ).all()
    assert [tuple(row) for row in told] == [
        ("Your 91 Day Treasury Bill has matured. See what it paid in the app.", investment.id),
    ]


async def test_the_new_term_earns_from_the_day_after_the_old_one_ended(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    old = await mature(worker_database, investment.id)
    assert old.rolled_into_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        nothing = await investments.accrue_investment(
            session, old.rolled_into_id, day(91), at(day(91))
        )
        first = await investments.accrue_investment(
            session, old.rolled_into_id, day(92), at(day(92))
        )
        # The closed investment earns nothing more.
        closed = await investments.accrue_investment(session, old.id, day(92), at(day(92)))
    assert nothing is None
    assert first is not None
    assert first.gross == ugx(351)
    assert closed is None


async def test_maturity_run_late_still_pays_only_to_the_maturity_date(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    old = await mature(worker_database, investment.id, offset=95)
    assert (old.accrued_through, old.accrued_net) == (day(91), ugx(26_490))
    assert old.rolled_into_id is not None
    new = await reload(worker_database, old.rolled_into_id)
    # No gap: the new term is counted from the old maturity date, not from the late run.
    assert new.start_date == day(91)


async def test_maturing_twice_changes_nothing(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    first = await mature(worker_database, investment.id)
    again = await mature(worker_database, investment.id, offset=92)
    assert again == first
    assert first.rolled_into_id is not None
    assert (
        await account_balance(
            worker_database, await reload(worker_database, first.rolled_into_id), HOLDING
        )
        == PROCEEDS
    )


async def test_an_investment_does_not_mature_early(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await mature(worker_database, investment.id, offset=90)
    assert error.value.code == "conflict"
    with pytest.raises(ApplicationError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await investments.mature_investment(session, investment.id, day(91), at(day(90)))
    assert (await reload(worker_database, investment.id)).status is ACTIVE


# Paying out -------------------------------------------------------------------------------------


async def test_a_client_who_chose_to_be_paid_gets_a_payout_for_principal_and_interest(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, NOW)
    chosen = await choose(app_database, investment, AtMaturity.PAY_OUT, destination_id)
    assert (chosen.at_maturity, chosen.payout_destination_id) == (
        AtMaturity.PAY_OUT,
        destination_id,
    )

    old = await mature(worker_database, investment.id)

    assert (old.status, old.rolled_into_id, old.payout_refusal) == (MATURED, None, None)
    assert old.payout_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        payout = await payouts.get_payout(session, old.payout_id)
    assert (payout.amount, payout.destination_id) == (ugx(PROCEEDS), destination_id)
    assert payout.source_reference == old.id
    assert await account_balance(worker_database, old, HOLDING) == 0
    assert await interest_owed(worker_database, old) == 0
    # Payouts has taken the money it was handed.
    assert await handed_over(worker_database, old) == 0


async def test_with_nowhere_to_send_it_the_money_waits_until_the_client_says_where(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW, at_maturity=AtMaturity.PAY_OUT)
    await pay(worker_database, investment, investment.amount, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, investment.id, confirmed_on=DAY, clock=NOW)

    held = await mature(worker_database, investment.id)

    assert (held.status, held.payout_id, held.payout_refusal) == (MATURED, None, "no_destination")
    assert await handed_over(worker_database, held) == PROCEEDS
    later = at(day(92))
    async with app_database.actor_transaction(client) as session:
        view = await investments.client_investment(session, client, investment.id, later)
    assert view.waiting_for_destination
    assert not view.can_change_maturity

    destination_id = await approved_destination(app_database, client, NOW)
    async with app_database.actor_transaction(client) as session:
        sent = await investments.pay_out_matured(
            session, client, investment.id, destination_id=destination_id, clock=at(day(93))
        )
    assert sent.payout_id is not None
    assert sent.payout_refusal is None
    assert await handed_over(worker_database, sent) == 0
    # Once it is on its way it cannot be sent again.
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(client) as session:
            await investments.pay_out_matured(
                session, client, investment.id, destination_id=destination_id, clock=at(day(93))
            )
    assert error.value.code == "conflict"


async def test_a_payout_that_payouts_refuses_leaves_the_money_waiting_with_the_reason(
    app_database: Database, worker_database: Database
) -> None:
    # More than one mobile money payout may carry.
    investment = await started(app_database, worker_database, NOW, amount=ugx(6_000_000))
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, NOW)
    await choose(app_database, investment, AtMaturity.PAY_OUT, destination_id)

    held = await mature(worker_database, investment.id)

    assert (held.status, held.payout_id) == (MATURED, None)
    assert held.payout_refusal is not None
    assert held.payout_refusal != "no_destination"
    assert await handed_over(worker_database, held) == (held.amount + held.accrued_net).minor
    assert await account_balance(worker_database, held, HOLDING) == 0


# The client's choice ----------------------------------------------------------------------------


async def test_the_choice_is_checked_and_can_be_changed_back(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, NOW)
    stranger = await approved_client(app_database, NOW)
    someone_elses = await approved_destination(app_database, stranger, NOW)

    with pytest.raises(ApplicationError) as error:
        await choose(app_database, investment, AtMaturity.PAY_OUT)
    assert refused(error) == ("validation_failed", [("destination_id", "required")])
    for unusable in (someone_elses, push_id()):
        with pytest.raises(ApplicationError) as error:
            await choose(app_database, investment, AtMaturity.PAY_OUT, unusable)
        assert refused(error) == ("validation_failed", [("destination_id", "not_usable")])
    with pytest.raises(ApplicationError) as error:
        await choose(app_database, investment, AtMaturity.ROLL_OVER, destination_id)
    assert refused(error)[0] == "validation_failed"
    with pytest.raises(ApplicationError) as error:
        await choose(app_database, investment, AtMaturity.PAY_OUT, someone_elses, client=stranger)
    assert error.value.code == "not_found"

    await choose(app_database, investment, AtMaturity.PAY_OUT, destination_id)
    back = await choose(app_database, investment, AtMaturity.ROLL_OVER)
    assert (back.at_maturity, back.payout_destination_id) == (AtMaturity.ROLL_OVER, None)


async def test_the_choice_closes_the_day_before_maturity(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    await choose(app_database, investment, AtMaturity.ROLL_OVER, clock=at(day(90)))
    with pytest.raises(ApplicationError) as error:
        await choose(app_database, investment, AtMaturity.ROLL_OVER, clock=at(day(91)))
    assert error.value.code == "conflict"
    await mature(worker_database, investment.id)
    with pytest.raises(ApplicationError) as error:
        await choose(app_database, investment, AtMaturity.ROLL_OVER, clock=at(day(91)))
    assert error.value.code == "conflict"


# The scheduled job ------------------------------------------------------------------------------


async def test_the_scheduled_job_closes_everything_that_is_due(
    app_database: Database, worker_database: Database
) -> None:
    due = await started(app_database, worker_database, NOW)
    clock = at(day(91), 12)
    queue = "inv" + "".join(c for c in push_id().lower() if c.isalnum())
    async with worker_database.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=MATURE_KIND,
                idempotency_key=f"investments.mature@{push_id()}",
                # 01:00 in Kampala on the maturity date is 22:00 UTC the day before.
                payload={"scheduled_for": f"{day(90).isoformat()}T22:00:00+00:00", "data": {}},
                queue=queue,
            ),
            now=clock.now(),
        )
    runner = Worker(
        worker_database,
        InvestmentJobs(worker_database, clock).handlers(),
        WorkerConfig(queue=queue),
        clock,
    )
    assert await runner.run_once() == 1
    async with worker_database.actor_transaction(SYSTEM) as session:
        status = await session.scalar(
            text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}
        )
    assert status == "succeeded"
    closed = await reload(worker_database, due.id)
    assert closed.status is MATURED
    assert closed.rolled_into_id is not None
    # What it rolled into is not due for another term.
    assert (await reload(worker_database, closed.rolled_into_id)).status is ACTIVE


async def maturing_notices(worker_database: Database, investment_id: str) -> list[str]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT body FROM notifications WHERE event = 'investment_maturing' "
                "AND link_kind = 'investment' AND link_id = :id"
            ),
            {"id": investment_id},
        )
        return [row.body for row in rows]


async def tell_maturing(worker_database: Database, run_date: date) -> None:
    after = ""
    while True:
        async with worker_database.actor_transaction(SYSTEM) as session:
            page = await maturity.tell_maturing_batch(
                session, run_date, at(run_date), after=after, limit=50
            )
        if page.last_id is None:
            return
        after = page.last_id


async def test_a_client_is_told_once_a_week_before_maturity(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    assert investment.maturity_date == day(91)
    # Eight days before is too early.
    await tell_maturing(worker_database, day(83))
    assert await maturing_notices(worker_database, investment.id) == []
    # Seven days before, they are told, with the date, while they can still choose.
    await tell_maturing(worker_database, day(84))
    expected = [
        f"Your 91 Day Treasury Bill matures on {day(91).day} {day(91):%b} {day(91).year}. "
        "Choose to reinvest or cash out in the app."
    ]
    assert await maturing_notices(worker_database, investment.id) == expected
    # Never twice, even when a later morning runs again.
    await tell_maturing(worker_database, day(85))
    assert await maturing_notices(worker_database, investment.id) == expected


async def test_a_missed_morning_is_caught_up_and_the_maturity_day_is_not_told(
    app_database: Database, worker_database: Database
) -> None:
    late = await started(app_database, worker_database, NOW)
    # The run for the maturity date itself says nothing: the choice has closed.
    await tell_maturing(worker_database, day(91))
    assert await maturing_notices(worker_database, late.id) == []
    # Three days before, still inside the week, it is told.
    await tell_maturing(worker_database, day(88))
    assert len(await maturing_notices(worker_database, late.id)) == 1


async def test_the_scheduled_job_tells_who_is_maturing(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    clock = at(day(85), 8)
    queue = "tell" + "".join(c for c in push_id().lower() if c.isalnum())
    async with worker_database.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=TELL_MATURING_KIND,
                idempotency_key=f"investments.tell_maturing@{push_id()}",
                # 08:15 in Kampala is 05:15 UTC.
                payload={"scheduled_for": f"{day(85).isoformat()}T05:15:00+00:00", "data": {}},
                queue=queue,
            ),
            now=clock.now(),
        )
    runner = Worker(
        worker_database,
        InvestmentJobs(worker_database, clock).handlers(),
        WorkerConfig(queue=queue),
        clock,
    )
    assert await runner.run_once() == 1
    async with worker_database.actor_transaction(SYSTEM) as session:
        status = await session.scalar(
            text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}
        )
    assert status == "succeeded"
    assert len(await maturing_notices(worker_database, investment.id)) == 1


# What the database enforces ---------------------------------------------------------------------


async def test_only_the_worker_matures_an_investment_and_a_matured_one_is_closed(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    client = Actor("client", investment.client_id)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "UPDATE investments SET status = 'matured', matured_at = now() WHERE id = :id"
                ),
                {"id": investment.id},
            )
    with pytest.raises(DBAPIError, match="only maturity records"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE investments SET payout_id = :payout WHERE id = :id"),
                {"id": investment.id, "payout": push_id()},
            )
    old = await mature(worker_database, investment.id)
    for statement in (
        "UPDATE investments SET status = 'active', matured_at = NULL, maturity_entry_id = NULL, "
        "rolled_into_id = NULL WHERE id = :id",
        "UPDATE investments SET rolled_into_id = NULL, payout_id = id WHERE id = :id",
        "UPDATE investments SET accrued_gross_minor = accrued_gross_minor + 1 WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="matured investment never changes"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": old.id})
    with pytest.raises(DBAPIError, match="matured investment never changes"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text("UPDATE investments SET at_maturity = 'pay_out' WHERE id = :id"),
                {"id": old.id},
            )


async def test_only_a_rollover_lets_the_worker_create_an_investment(
    worker_database: Database,
) -> None:
    with pytest.raises(DBAPIError, match="created by its client, a rollover or a saving source"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO investments (id, client_id, product_id, currency, "
                    "principal_minor, annual_rate_bps, tax_bps, term_days, at_maturity, "
                    "created_at, pay_by, updated_at) VALUES (:id, :client_id, "
                    "'FvProduct1TREA91_001', 'UGX', 1000000, 1250, 1500, 91, 'roll_over', "
                    "now(), now() + interval '72 hours', now())"
                ),
                {"id": push_id(), "client_id": new_client().id},
            )


async def test_a_client_cannot_pass_an_insert_off_as_a_rollover_or_a_saving_source(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    client = Actor("client", investment.client_id)
    for columns, values in (
        ("rolled_from_id", ":origin"),
        ("source_type, source_id", "'autoinvest_run', :origin"),
    ):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "INSERT INTO investments (id, client_id, product_id, currency, "  # noqa: S608
                        "principal_minor, annual_rate_bps, tax_bps, term_days, at_maturity, "
                        f"created_at, pay_by, updated_at, {columns}) VALUES (:id, :client_id, "
                        "'FvProduct1TREA91_001', 'UGX', 1000000, 1250, 1500, 91, 'roll_over', "
                        f"now(), now() + interval '72 hours', now(), {values})"
                    ),
                    {"id": push_id(), "client_id": client.id, "origin": investment.id},
                )
