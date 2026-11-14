"""Asking for the interest run or the maturity run again, against real PostgreSQL: one person
asks, a different one approves, the approval queues the run, and the database holds everyone
to their part.

Every test leaves no request open, because only one may wait for each run and day."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The service first: the jobs module imported on its own meets a cycle through transfers.
import app.modules.investments.service  # noqa: F401
from app.infrastructure.database import Database
from app.modules.investments import jobs, run_requests
from app.modules.investments import service as investments
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from tests.support.clock import AsyncBarrier
from tests.support.investments import PRESENT, after, today
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

Kind = investments.RunKind
REASON = "The run stopped part way through"


async def propose(
    app_database: Database, staff: Actor, kind: Kind, days_ago: int
) -> investments.RunRequest:
    async with app_database.actor_transaction(staff) as session:
        return await investments.propose_run(
            session,
            staff,
            kind=kind,
            run_date=today() - timedelta(days=days_ago),
            reason=REASON,
            clock=PRESENT,
        )


async def approve(app_database: Database, staff: Actor, request_id: str) -> investments.RunRequest:
    async with app_database.actor_transaction(staff) as session:
        return await investments.approve_run(session, staff, request_id, PRESENT)


async def queued(worker_database: Database, request_id: str) -> list[dict[str, object]]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text("SELECT kind, payload FROM jobs WHERE idempotency_key = :key"),
            {"key": f"staff-run:{request_id}"},
        )
        return [{"kind": row.kind, "payload": row.payload} for row in rows.all()]


async def test_a_second_person_approves_and_the_run_is_queued_for_its_own_day(
    app_database: Database, worker_database: Database
) -> None:
    maker, checker = new_staff(), new_staff()
    proposed = await propose(app_database, maker, Kind.INTEREST, 3)
    assert (proposed.status, proposed.job_id) == ("proposed", None)
    assert await queued(worker_database, proposed.id) == []

    # The proposer never approves their own, and nothing is queued when they try.
    with pytest.raises(ApplicationError) as refused:
        await approve(app_database, maker, proposed.id)
    assert refused.value.code == "forbidden"
    assert await queued(worker_database, proposed.id) == []

    approved = await approve(app_database, checker, proposed.id)
    assert (approved.status, approved.decided_by) == ("approved", checker.id)
    [job] = await queued(worker_database, proposed.id)
    assert job["kind"] == jobs.ACCRUE_KIND
    payload = job["payload"]
    assert isinstance(payload, dict)
    # The run reads its day back from the payload as the scheduled runs do.
    assert jobs._run_date(payload) == proposed.run_date
    assert approved.job_id is not None


async def test_the_maturity_run_is_asked_for_today_only(
    app_database: Database, worker_database: Database
) -> None:
    with pytest.raises(ApplicationError) as yesterday:
        await propose(app_database, new_staff(), Kind.MATURITY, 1)
    assert [f.code for f in yesterday.value.fields] == ["maturity_today_only"]

    proposed = await propose(app_database, new_staff(), Kind.MATURITY, 0)
    approved = await approve(app_database, new_staff(), proposed.id)
    [job] = await queued(worker_database, approved.id)
    assert job["kind"] == jobs.MATURE_KIND


async def test_a_maturity_request_from_an_earlier_day_cannot_be_approved(
    app_database: Database, worker_database: Database
) -> None:
    proposed = await propose(app_database, new_staff(), Kind.MATURITY, 0)
    checker = new_staff()
    with pytest.raises(ApplicationError) as passed:
        async with app_database.actor_transaction(checker) as session:
            await investments.approve_run(session, checker, proposed.id, after(days=1))
    assert passed.value.code == "conflict"
    assert [f.code for f in passed.value.fields] == ["run_date_passed"]
    assert await queued(worker_database, proposed.id) == []
    async with app_database.actor_transaction(checker) as session:
        declined = await investments.decline_run(
            session, checker, proposed.id, "Today's run already covers it", PRESENT
        )
    assert declined.status == "declined"


async def test_days_that_have_not_come_or_are_too_old_are_refused(app_database: Database) -> None:
    for days_ago, code in ((-1, "not_yet"), (32, "too_old")):
        with pytest.raises(ApplicationError) as refused:
            await propose(app_database, new_staff(), Kind.INTEREST, days_ago)
        assert refused.value.code == "validation_failed"
        assert [f.code for f in refused.value.fields] == [code]
    # The database refuses a day that has not come by its own clock, whatever the code says.
    staff = new_staff()
    with pytest.raises(DBAPIError, match="starts as proposed"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO investment_run_requests (id, kind, run_date, reason, "
                    "proposed_by, proposed_at) VALUES (:id, 'interest', :day, 'x', :me, now())"
                ),
                {"id": new_staff().id, "day": today() + timedelta(days=1), "me": staff.id},
            )


async def test_one_request_waits_for_each_run_and_day(app_database: Database) -> None:
    maker = new_staff()
    first = await propose(app_database, maker, Kind.INTEREST, 5)
    with pytest.raises(ApplicationError) as twice:
        await propose(app_database, new_staff(), Kind.INTEREST, 5)
    assert twice.value.code == "conflict"
    # Another day is another run.
    other = await propose(app_database, maker, Kind.INTEREST, 6)

    # Only the proposer withdraws, and then the day may be asked for again.
    someone_else = new_staff()
    with pytest.raises(ApplicationError) as not_yours:
        async with app_database.actor_transaction(someone_else) as session:
            await investments.withdraw_run(session, someone_else, first.id, PRESENT)
    assert not_yours.value.code == "forbidden"
    for request in (first, other):
        async with app_database.actor_transaction(maker) as session:
            withdrawn = await investments.withdraw_run(session, maker, request.id, PRESENT)
        assert withdrawn.status == "withdrawn"
    again = await propose(app_database, new_staff(), Kind.INTEREST, 5)
    async with app_database.actor_transaction(maker) as session:
        await investments.decline_run(session, maker, again.id, "Not needed", PRESENT)


async def test_two_approvals_at_once_queue_the_run_once(
    app_database: Database, worker_database: Database
) -> None:
    proposed = await propose(app_database, new_staff(), Kind.INTEREST, 7)
    barrier = AsyncBarrier(2)

    async def one() -> str:
        staff = new_staff()
        async with app_database.actor_transaction(staff) as session:
            await barrier.wait()
            try:
                await investments.approve_run(session, staff, proposed.id, PRESENT)
            except ApplicationError as error:
                return error.code
            return "approved"

    assert sorted(await asyncio.gather(one(), one())) == ["approved", "conflict"]
    assert len(await queued(worker_database, proposed.id)) == 1


async def test_the_database_holds_everyone_to_their_part(
    app_database: Database, worker_database: Database
) -> None:
    maker = new_staff()
    proposed = await propose(app_database, maker, Kind.INTEREST, 8)

    # The proposer cannot approve it, whatever the code says.
    with pytest.raises(DBAPIError, match="second_person"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE investment_run_requests SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), job_id = :me WHERE id = :id"
                ),
                {"me": maker.id, "id": proposed.id},
            )
    # A decision is made in the decider's own name, and approving means a run was queued.
    other = new_staff()
    with pytest.raises(DBAPIError, match="only decided"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(
                text(
                    "UPDATE investment_run_requests SET status = 'declined', decided_by = :them, "
                    "decided_at = now(), decision_note = 'x' WHERE id = :id"
                ),
                {"them": maker.id, "id": proposed.id},
            )
    with pytest.raises(DBAPIError, match="ck_investment_run_requests_job"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(
                text(
                    "UPDATE investment_run_requests SET status = 'approved', decided_by = :me, "
                    "decided_at = now() WHERE id = :id"
                ),
                {"me": other.id, "id": proposed.id},
            )
    # The day never changes, and the worker only reads.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(
                text("UPDATE investment_run_requests SET run_date = run_date - 1 WHERE id = :id"),
                {"id": proposed.id},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE investment_run_requests SET status = 'withdrawn' WHERE id = :id"),
                {"id": proposed.id},
            )
    # Nobody deletes one.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(
                text("DELETE FROM investment_run_requests WHERE id = :id"), {"id": proposed.id}
            )
    async with app_database.actor_transaction(other) as session:
        declined = await investments.decline_run(session, other, proposed.id, "No", PRESENT)
    # A decided request never changes again.
    with pytest.raises(DBAPIError, match="never changes"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE investment_run_requests SET status = 'withdrawn', decided_by = :me "
                    "WHERE id = :id"
                ),
                {"me": maker.id, "id": declined.id},
            )


async def test_a_client_reads_no_request(app_database: Database) -> None:
    maker = new_staff()
    proposed = await propose(app_database, maker, Kind.INTEREST, 9)
    client = Actor("client", new_staff().id)
    async with app_database.actor_transaction(client) as session:
        seen = (
            await session.execute(text("SELECT count(*) FROM investment_run_requests"))
        ).scalar_one()
    assert seen == 0
    with pytest.raises(ApplicationError) as refused:
        async with app_database.actor_transaction(client) as session:
            await run_requests.list_runs(session, client)
    assert refused.value.code == "forbidden"
    async with app_database.actor_transaction(maker) as session:
        await investments.withdraw_run(session, maker, proposed.id, PRESENT)


async def test_not_even_the_owner_deletes_or_truncates_a_request(
    app_database: Database, migrator_database: Database
) -> None:
    maker = new_staff()
    proposed = await propose(app_database, maker, Kind.INTEREST, 10)
    for statement in (
        "DELETE FROM investment_run_requests WHERE id = :id",
        "TRUNCATE investment_run_requests",
    ):
        with pytest.raises(DBAPIError, match="never deleted"):
            async with migrator_database.transaction() as session:
                await session.execute(text(statement), {"id": proposed.id})
    async with app_database.actor_transaction(maker) as session:
        await investments.withdraw_run(session, maker, proposed.id, PRESENT)
