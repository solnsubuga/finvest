"""Two staff members settle a payout the provider never answered for, from the ioTec statement."""

import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.approvals import service as approvals
from app.modules.audit import service as audit
from app.modules.identity.domain.permissions import permissions_for
from app.modules.identity.service import StaffSession
from app.modules.payouts import destinations, jobs, payouts, resolutions
from app.modules.payouts.domain.payouts import PayoutEvent, PayoutStatus
from app.modules.payouts.payouts import PayoutView
from app.modules.payouts.resolutions import Outcome, ResolutionRefused, ResolutionStatus
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.ledger import staff, ugx
from tests.support.payouts import FakeDisbursements, World

pytestmark = pytest.mark.integration

S = PayoutStatus
AMOUNT = 1_000_000
FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)


@pytest.fixture
async def world(app_database: Database, worker_database: Database) -> World:
    return await World(app_database, worker_database, FakeClock(datetime.now(UTC))).build(8_000_000)


@pytest.fixture
def now() -> FakeClock:
    """The staff steps are stamped against the database clock, so they run at the real time."""
    return FakeClock(datetime.now(UTC))


async def a_stranded_payout(
    world: World, migrator: Database, *, days_ago: int = 5, status: PayoutEvent | None = None
) -> tuple[PayoutView, date]:
    """A payout the provider took and never answered for, first sent `days_ago` days ago."""
    payout = await world.request(AMOUNT)
    moved = await world.worker_event(payout.id, status or PayoutEvent.SEND_ACCEPTED)
    sent_at = datetime.now(UTC) - timedelta(days=days_ago)
    async with migrator.transaction() as session:
        # Set-up only: the worker stamps attempts with its own clock, which here is real time.
        await session.execute(
            text("ALTER TABLE payout_send_attempts DISABLE TRIGGER payout_send_attempts_guard")
        )
        await session.execute(
            text("UPDATE payout_send_attempts SET started_at = :at WHERE payout_id = :id"),
            {"at": sent_at, "id": payout.id},
        )
        await session.execute(
            text("ALTER TABLE payout_send_attempts ENABLE TRIGGER payout_send_attempts_guard")
        )
    return moved, business_date(sent_at)


async def a_statement(
    database: Database, clock: FakeClock, day: date, rows: tuple[tuple[str, int], ...] = ()
) -> tuple[str, dict[str, str]]:
    """An ioTec statement for a pot of its own. Rows are (reference, amount out). Returns the
    statement ID and the line IDs by reference."""
    pot = f"mtn-{secrets.token_hex(4)}"
    lines = "".join(
        f"{day.isoformat()},Payout {n},{reference},,{amount}\n"
        for n, (reference, amount) in enumerate(rows)
    )
    opening = 10**12
    parsed = parse_csv(
        "Date,Details,Reference,In,Out\n" + lines,
        FORMAT,
        source=StatementSource.IOTEC,
        account_reference=pot,
        statement_date=day,
        opening_balance=ugx(opening),
        closing_balance=ugx(opening - sum(amount for _, amount in rows)),
    )
    importer = staff()
    async with database.actor_transaction(importer) as session:
        imported = await statements.import_statement(session, importer, parsed, "0" * 64, clock)
        found = await statements.find_statement_lines(
            session,
            source=StatementSource.IOTEC,
            date_from=day,
            date_to=day,
        )
    return imported.id, {line.reference: line.id for line in found if line.reference in dict(rows)}


async def propose(
    world: World, clock: FakeClock, payout_id: str, maker: Actor, **evidence: Any
) -> resolutions.ResolutionView:
    async with world.app.actor_transaction(maker) as session:
        return await resolutions.propose(
            session, maker, payout_id, reason="Checked the ioTec statement", clock=clock, **evidence
        )


async def approve(world: World, clock: FakeClock, resolution_id: str, checker: Actor) -> PayoutView:
    async with world.app.actor_transaction(checker) as session:
        return await resolutions.approve(
            session, checker, resolution_id, reason="Agree with the statement", clock=clock
        )


async def refused(coro: object) -> str:
    with pytest.raises(ResolutionRefused) as refusal:
        await coro  # type: ignore[misc]
    return refusal.value.refusal


async def test_two_people_settle_a_payout_the_statement_shows_as_paid(
    world: World, migrator_database: Database, now: FakeClock
) -> None:
    payout, sent_on = await a_stranded_payout(world, migrator_database)
    _, lines = await a_statement(world.app, now, sent_on, ((payout.id, AMOUNT),))
    maker, checker = staff(), staff()
    pot_before = await world.balance(world.pot)

    proposal = await propose(
        world, now, payout.id, maker, outcome=Outcome.PAID, statement_line_id=lines[payout.id]
    )
    assert (proposal.status, proposal.proposed_by) == (ResolutionStatus.PROPOSED, maker.id)
    # Nothing moves on one person's word.
    assert (await world.balance(payout.in_flight_account_id)) == AMOUNT

    async with world.app.actor_transaction(checker) as session:
        reader = StaffSession(
            push_id(), checker.id or "", "Grace Auma", "finance", list(permissions_for("finance"))
        )
        inbox = await approvals.waiting_for(session, reader, now.now())
    [item] = [i for i in inbox if i.id == proposal.id]
    assert (item.kind, item.subject, item.can_decide) == ("payout_resolution", "paid", True)

    assert await refused(approve(world, now, proposal.id, maker)) == "proposed_by_you"
    paid = await approve(world, now, proposal.id, checker)
    assert paid.status is S.PAID
    assert await world.balance(payout.in_flight_account_id) == 0
    assert await world.balance(world.pot) == pot_before - AMOUNT

    async with world.app.actor_transaction(checker) as session:
        events = await audit.events_for_resource(session, "payout", payout.id)
        assert proposal.id not in [r.id for r in await resolutions.waiting(session)]
    actions = [(event.action, event.actor_id) for event in events]
    assert ("payouts.resolution_proposed", maker.id) in actions
    assert ("payouts.resolution_approved", checker.id) in actions

    # The client is told, as when the provider answers, and the worker notes the first payout
    # on the destination, which only it may write.
    async with world.worker.actor_transaction(SYSTEM) as session:
        told = await session.scalar(
            text("SELECT event FROM notifications WHERE client_id = :client AND link_id = :id"),
            {"client": world.client_id, "id": payout.id},
        )
    assert told == "payout_sent"
    # Only the job this approval queued: other tests' sends are not this test's to run.
    handlers = jobs.PayoutJobs(world.worker, FakeDisbursements(), now).handlers()
    worker = Worker(
        world.worker,
        {payouts.FIRST_PAID_KIND: handlers[payouts.FIRST_PAID_KIND]},
        WorkerConfig(),
        now,
    )
    while await worker.run_once():
        pass
    async with world.worker.actor_transaction(SYSTEM) as session:
        destination = await destinations.get_destination(session, payout.destination_id)
    assert destination.first_paid_at is not None


async def test_two_people_settle_a_payout_the_statement_never_shows_as_failed(
    world: World, migrator_database: Database, now: FakeClock
) -> None:
    payout, sent_on = await a_stranded_payout(
        world, migrator_database, status=PayoutEvent.SEND_UNCERTAIN
    )
    assert payout.status is S.UNCERTAIN
    statement_id, _ = await a_statement(world.app, now, sent_on + timedelta(days=3))
    maker, checker = staff(), staff()
    pending_before = await world.pending()
    proposal = await propose(
        world, now, payout.id, maker, outcome=Outcome.FAILED, statement_id=statement_id
    )
    failed = await approve(world, now, proposal.id, checker)
    assert (failed.status, failed.failure_code) == (S.FAILED, "staff_resolved")
    # The money is parked for the client, as when the provider says it failed.
    assert await world.balance(payout.in_flight_account_id) == 0
    assert await world.pending() == pending_before + AMOUNT


async def test_what_a_first_person_may_not_propose(
    world: World, migrator_database: Database, now: FakeClock
) -> None:
    maker = staff()
    fresh = await world.request(AMOUNT)
    await world.worker_event(fresh.id, PayoutEvent.SEND_ACCEPTED)
    statement_id, _ = await a_statement(
        world.app, now, business_date(now.now()) - timedelta(days=1)
    )
    # While the provider is still being read, its answer is the one that counts.
    assert (
        await refused(
            propose(world, now, fresh.id, maker, outcome=Outcome.FAILED, statement_id=statement_id)
        )
        == "provider_still_being_read"
    )

    payout, sent_on = await a_stranded_payout(world, migrator_database)
    _, lines = await a_statement(
        world.app, now, sent_on, ((payout.id, AMOUNT - 1), (push_id(), AMOUNT))
    )
    assert (
        await refused(
            propose(
                world,
                now,
                payout.id,
                maker,
                outcome=Outcome.PAID,
                statement_line_id=lines[payout.id],
            )
        )
        == "statement_line_not_this_payout"
    )
    # Its ID is on a statement, so it did not simply fail.
    later, _ = await a_statement(world.app, now, sent_on + timedelta(days=3))
    assert (
        await refused(
            propose(world, now, payout.id, maker, outcome=Outcome.FAILED, statement_id=later)
        )
        == "payout_on_statement"
    )

    other, other_sent_on = await a_stranded_payout(world, migrator_database)
    early, _ = await a_statement(world.app, now, other_sent_on + timedelta(days=2))
    assert (
        await refused(
            propose(world, now, other.id, maker, outcome=Outcome.FAILED, statement_id=early)
        )
        == "statement_too_early"
    )
    assert (
        await refused(propose(world, now, other.id, maker, outcome=Outcome.PAID))
        == "statement_line_required"
    )


async def test_one_open_proposal_at_a_time_and_a_declined_one_can_be_proposed_again(
    world: World, migrator_database: Database, now: FakeClock
) -> None:
    payout, sent_on = await a_stranded_payout(world, migrator_database)
    statement_id, _ = await a_statement(world.app, now, sent_on + timedelta(days=3))
    maker, checker = staff(), staff()
    first = await propose(
        world, now, payout.id, maker, outcome=Outcome.FAILED, statement_id=statement_id
    )
    assert (
        await refused(
            propose(
                world, now, payout.id, checker, outcome=Outcome.FAILED, statement_id=statement_id
            )
        )
        == "resolution_already_proposed"
    )
    async with world.app.actor_transaction(checker) as session:
        assert (
            await refused(
                resolutions.withdraw(session, checker, first.id, reason="Not mine", clock=now)
            )
            == "not_proposed_by_you"
        )
    async with world.app.actor_transaction(maker) as session:
        assert (
            await refused(resolutions.decline(session, maker, first.id, reason="Mine", clock=now))
            == "proposed_by_you"
        )
    async with world.app.actor_transaction(checker) as session:
        declined = await resolutions.decline(
            session, checker, first.id, reason="Ask ioTec first", clock=now
        )
    assert (declined.status, declined.decided_by) == (ResolutionStatus.DECLINED, checker.id)
    assert (await payouts_status(world, payout.id)) is S.SENT

    async with world.app.actor_transaction(checker) as session:
        [stranded] = [
            item
            for item in await resolutions.stranded(session, now.now())
            if item.payout.id == payout.id
        ]
    assert stranded.resolution is None
    second = await propose(
        world, now, payout.id, maker, outcome=Outcome.FAILED, statement_id=statement_id
    )
    async with world.app.actor_transaction(maker) as session:
        withdrawn = await resolutions.withdraw(
            session, maker, second.id, reason="Wrong statement", clock=now
        )
    assert withdrawn.status is ResolutionStatus.WITHDRAWN
    async with world.app.actor_transaction(checker) as session:
        assert (
            await refused(approve_in(session, checker, second.id, now))
            == "resolution_already_decided"
        )


async def approve_in(session: object, checker: Actor, resolution_id: str, now: FakeClock) -> object:
    return await resolutions.approve(
        session,  # type: ignore[arg-type]
        checker,
        resolution_id,
        reason="Agree",
        clock=now,
    )


async def payouts_status(world: World, payout_id: str) -> PayoutStatus:
    async with world.worker.actor_transaction(SYSTEM) as session:
        return (await payouts.get_payout(session, payout_id)).status


# What the database refuses, whatever the application does


async def test_the_database_settles_a_payout_by_hand_only_with_an_approved_resolution(
    world: World, migrator_database: Database, now: FakeClock
) -> None:
    payout, sent_on = await a_stranded_payout(world, migrator_database)
    statement_id, _ = await a_statement(world.app, now, sent_on + timedelta(days=3))
    maker, checker = staff(), staff()

    with pytest.raises(DBAPIError, match="approved resolution"):
        async with world.app.actor_transaction(checker) as session:
            await session.execute(
                text("UPDATE payouts SET status = 'failed' WHERE id = :id"), {"id": payout.id}
            )

    proposal = await propose(
        world, now, payout.id, maker, outcome=Outcome.FAILED, statement_id=statement_id
    )
    # Approving without moving the payout cannot commit.
    with pytest.raises(DBAPIError, match="same transaction"):
        async with world.app.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE payout_resolutions SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'ok' WHERE id = :id"
                ),
                {"me": checker.id, "id": proposal.id},
            )
    # The proposer cannot approve their own, even by hand.
    with pytest.raises(DBAPIError, match="ck_payout_resolutions_two_people"):
        async with world.app.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE payout_resolutions SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'ok' WHERE id = :id"
                ),
                {"me": maker.id, "id": proposal.id},
            )
    # What was proposed never changes: the app role may write only the decision. Nothing is
    # deleted, even by the owner.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with world.app.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE payout_resolutions SET status = 'declined', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'no', reason = 'other' WHERE id = :id"
                ),
                {"me": checker.id, "id": proposal.id},
            )
    with pytest.raises(DBAPIError):
        async with migrator_database.transaction() as session:
            await session.execute(
                text("DELETE FROM payout_resolutions WHERE id = :id"), {"id": proposal.id}
            )


async def test_the_database_checks_the_evidence_itself(
    world: World, migrator_database: Database, now: FakeClock
) -> None:
    payout, sent_on = await a_stranded_payout(world, migrator_database)
    _, lines = await a_statement(world.app, now, sent_on, ((payout.id, AMOUNT),))
    early, _ = await a_statement(world.app, now, sent_on + timedelta(days=1))
    maker = staff()
    insert = text(
        "INSERT INTO payout_resolutions (id, payout_id, outcome, statement_line_id, "
        "statement_id, reason, status, proposed_by, proposed_at) VALUES (:id, :payout, "
        ":outcome, :line, :statement, 'Checked', 'proposed', :me, now())"
    )
    # Failed, though the payout's ID is on the statement, and on a statement too early.
    with pytest.raises(DBAPIError, match="does not show"):
        async with world.app.actor_transaction(maker) as session:
            await session.execute(
                insert,
                {
                    "id": push_id(),
                    "payout": payout.id,
                    "outcome": "failed",
                    "line": None,
                    "statement": early,
                    "me": maker.id,
                },
            )
    # Paid, citing a line of another payout.
    other, other_sent_on = await a_stranded_payout(world, migrator_database)
    with pytest.raises(DBAPIError, match="does not show"):
        async with world.app.actor_transaction(maker) as session:
            await session.execute(
                insert,
                {
                    "id": push_id(),
                    "payout": other.id,
                    "outcome": "paid",
                    "line": lines[payout.id],
                    "statement": None,
                    "me": maker.id,
                },
            )
    # Not in the name of the person signed in, and never as anyone but staff.
    with pytest.raises(DBAPIError):
        async with world.app.actor_transaction(maker) as session:
            await session.execute(
                insert,
                {
                    "id": push_id(),
                    "payout": payout.id,
                    "outcome": "paid",
                    "line": lines[payout.id],
                    "statement": None,
                    "me": push_id(),
                },
            )
    async with world.app.actor_transaction(world.client) as session:
        assert await session.scalar(text("SELECT count(*) FROM payout_resolutions")) == 0
    assert other_sent_on is not None
