"""Booking an investment for a client, against real PostgreSQL: one member of staff books it, a
different one approves, the worker saves it unpaid on the day's terms, and the client pays for
it as usual. The database holds everyone to their part."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The service first: the jobs module imported on its own meets a cycle through transfers.
import app.modules.investments.service  # noqa: F401
from app.infrastructure.database import Database
from app.modules.investments import bookings
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Money
from tests.support.clock import AsyncBarrier
from tests.support.investments import PRESENT, approved_client, pay, request, today
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

REASON = "The client called and asked for a 91-day bill"


async def book(
    app_database: Database,
    staff: Actor,
    client: Actor,
    *,
    amount: Money | None = None,
    product_code: str = "TREA-91",
    at_maturity: AtMaturity | None = None,
) -> investments.Booking:
    assert client.id is not None
    async with app_database.actor_transaction(staff) as session:
        return await investments.propose_booking(
            session,
            staff,
            client_id=client.id,
            product_code=product_code,
            amount=amount or ugx(1_000_000),
            at_maturity=at_maturity,
            reason=REASON,
            clock=PRESENT,
        )


async def approve(app_database: Database, staff: Actor, booking_id: str) -> investments.Booking:
    async with app_database.actor_transaction(staff) as session:
        return await investments.approve_booking(session, staff, booking_id, PRESENT)


async def carry_out(worker_database: Database, booking_id: str) -> investments.Booking:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await bookings.carry_out_booking(session, booking_id, PRESENT)


async def test_a_second_person_approves_and_the_client_pays_as_usual(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    booked = await book(app_database, new_staff(), client, at_maturity=AtMaturity.PAY_OUT)
    assert (booked.status, booked.investment_id, booked.product_name) == (
        "proposed",
        None,
        booked.product_name,
    )
    approved = await approve(app_database, new_staff(), booked.id)
    assert approved.status == "approved"
    async with worker_database.actor_transaction(SYSTEM) as session:
        jobs = (
            await session.execute(
                text("SELECT kind, payload FROM jobs WHERE idempotency_key = :key"),
                {"key": f"booking:{booked.id}"},
            )
        ).all()
    assert [(row.kind, row.payload) for row in jobs] == [
        (bookings.CARRY_OUT_KIND, {"booking_id": booked.id})
    ]

    done = await carry_out(worker_database, booked.id)

    assert done.status == "done" and done.investment_id is not None
    assert done.finished_at is not None
    # Running it again changes nothing.
    assert (await carry_out(worker_database, booked.id)).investment_id == done.investment_id
    async with app_database.actor_transaction(client) as session:
        mine = await investments.get_investment(session, done.investment_id)
        notices = (
            await session.execute(
                text("SELECT event, link_id FROM notifications WHERE client_id = :client"),
                {"client": client.id},
            )
        ).all()
    assert (mine.status, mine.amount, mine.client_id, mine.at_maturity) == (
        InvestmentStatus.AWAITING_PAYMENT,
        ugx(1_000_000),
        client.id,
        AtMaturity.PAY_OUT,
    )
    assert ("investment_booked", done.investment_id) in [(n.event, n.link_id) for n in notices]

    # The client pays for it as for their own, and it starts.
    await pay(worker_database, mine, mine.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        running = await investments.start_investment(
            session, mine.id, confirmed_on=today(), clock=PRESENT
        )
    assert running.status is InvestmentStatus.ACTIVE


async def test_only_a_second_person_decides_and_only_the_booker_withdraws(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, PRESENT)
    maker = new_staff()
    booked = await book(app_database, maker, client)
    with pytest.raises(ApplicationError) as own:
        await approve(app_database, maker, booked.id)
    assert own.value.code == "forbidden"
    other = new_staff()
    with pytest.raises(ApplicationError) as not_theirs:
        async with app_database.actor_transaction(other) as session:
            await investments.withdraw_booking(session, other, booked.id, PRESENT)
    assert not_theirs.value.code == "forbidden"
    with pytest.raises(ApplicationError) as no_note:
        async with app_database.actor_transaction(other) as session:
            await investments.send_back_booking(session, other, booked.id, " ", PRESENT)
    assert [f.code for f in no_note.value.fields] == ["required"]

    async with app_database.actor_transaction(other) as session:
        sent_back = await investments.send_back_booking(
            session, other, booked.id, "The client wants the 182-day bill", PRESENT
        )
    assert (sent_back.status, sent_back.decision_note) == (
        "sent_back",
        "The client wants the 182-day bill",
    )
    with pytest.raises(ApplicationError) as decided:
        await approve(app_database, new_staff(), booked.id)
    assert decided.value.code == "conflict"

    again = await book(app_database, maker, client)
    async with app_database.actor_transaction(maker) as session:
        withdrawn = await investments.withdraw_booking(session, maker, again.id, PRESENT)
    assert withdrawn.status == "withdrawn"
    async with app_database.actor_transaction(maker) as session:
        listed = await investments.list_bookings(session, maker, client_id=client.id)
    assert [b.id for b in listed] == [again.id, booked.id]


async def test_a_booking_meets_the_rules_of_a_clients_own_request(
    app_database: Database,
) -> None:
    staff = new_staff()
    with pytest.raises(ApplicationError) as unapproved:
        await book(app_database, staff, new_client())
    assert unapproved.value.code == "not_found"
    client = await approved_client(app_database, PRESENT)
    with pytest.raises(ApplicationError) as small:
        await book(app_database, staff, client, amount=ugx(1))
    assert [f.field_path[0] for f in small.value.fields] == ["amount"]
    with pytest.raises(ApplicationError) as unknown:
        await book(app_database, staff, client, product_code="NOPE-1")
    assert unknown.value.code == "not_found"


async def test_a_booking_that_cannot_be_saved_says_why_and_saves_nothing(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    booked = await book(app_database, new_staff(), client)
    await approve(app_database, new_staff(), booked.id)
    # Meanwhile the client asked in the app for as many unpaid investments as allowed.
    for _ in range(5):
        await request(app_database, client, PRESENT)

    failed = await carry_out(worker_database, booked.id)

    assert (failed.status, failed.failure_code, failed.investment_id) == (
        "failed",
        "too_many_unpaid",
        None,
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM investments WHERE source_id = :id"), {"id": booked.id}
            )
        ).scalar_one()
    assert count == 0


async def test_two_approvals_at_once_ask_the_worker_once(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    booked = await book(app_database, new_staff(), client)
    barrier = AsyncBarrier(2)

    async def one() -> str:
        staff = new_staff()
        async with app_database.actor_transaction(staff) as session:
            await barrier.wait()
            try:
                await investments.approve_booking(session, staff, booked.id, PRESENT)
            except ApplicationError as error:
                return error.code
            return "approved"

    assert sorted(await asyncio.gather(one(), one())) == ["approved", "conflict"]
    async with worker_database.actor_transaction(SYSTEM) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM jobs WHERE idempotency_key = :key"),
                {"key": f"booking:{booked.id}"},
            )
        ).scalar_one()
    assert count == 1


async def test_two_workers_at_once_save_one_investment(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    booked = await book(app_database, new_staff(), client)
    await approve(app_database, new_staff(), booked.id)

    done = await asyncio.gather(
        carry_out(worker_database, booked.id), carry_out(worker_database, booked.id)
    )

    assert {b.investment_id for b in done} == {done[0].investment_id}
    async with worker_database.actor_transaction(SYSTEM) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM investments WHERE source_id = :id"), {"id": booked.id}
            )
        ).scalar_one()
    assert count == 1


async def test_the_database_holds_everyone_to_their_part(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    maker = new_staff()
    booked = await book(app_database, maker, client)

    # The booker cannot approve their own, even past the service.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE investment_bookings SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), job_id = :me WHERE id = :id"
                ),
                {"me": maker.id, "id": booked.id},
            )
    # Staff cannot mark a booking done, and a client reads none.
    approved = await approve(app_database, new_staff(), booked.id)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text("UPDATE investment_bookings SET status = 'done' WHERE id = :id"),
                {"id": approved.id},
            )
    async with app_database.actor_transaction(client) as session:
        seen = (await session.execute(text("SELECT count(*) FROM investment_bookings"))).scalar()
    assert seen == 0
    # The worker cannot finish it with an investment that is not the one booked.
    other = await request(app_database, client, PRESENT)
    with pytest.raises(DBAPIError, match="with what was booked"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE investment_bookings SET status = 'done', investment_id = :inv, "
                    "finished_at = now() WHERE id = :id"
                ),
                {"inv": other.id, "id": approved.id},
            )
    # Nothing is deleted.
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("DELETE FROM investment_bookings WHERE id = :id"), {"id": approved.id}
            )
    await carry_out(worker_database, approved.id)
