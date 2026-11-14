"""The days the journeys moved money in are reconciled and closed by two people.

This runs after every other journey: a closed day can never take another entry. It closes,
in order, each day the trust bank account has a statement for, and then checks today, the day
every journey's mobile money arrived, against what ioTec says it collected.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.dealing import holdings
from app.modules.ledger import closing as ledger_closing
from app.modules.ledger.domain.errors import DayNotEnded
from app.modules.reconciliation import day_close
from app.modules.reconciliation.domain.pots import CASH_POTS
from app.modules.reconciliation.domain.statements import (
    Direction,
)
from app.shared.business_day import KAMPALA
from tests.journeys import gaps
from tests.journeys.money import TrustBank, today, ugx
from tests.support.books import audit_for, missing
from tests.support.journey import Collected, Person, World

pytestmark = [pytest.mark.journey, pytest.mark.closes_a_day]

IOTEC_POTS = ("mtn", "airtel")
HEADER = "Date,Details,Reference,In,Out\n"


def morning_after(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(9), KAMPALA).astimezone(UTC)


async def import_iotec(
    world: World,
    importer: Person,
    network: str,
    day: date,
    collections: list[Collected],
    opening: int,
) -> int:
    """ioTec's wallet statement for one network and day, uploaded through the console's route.
    Returns its closing balance."""
    # ioTec reports each collection under the request ID it was sent with.
    rows = "".join(
        f"{day.isoformat()},Collection,{collection.request_id},{collection.minor},\n"
        for collection in collections
    )
    closing = opening + sum(collection.minor for collection in collections)
    uploaded = await importer.http.post(
        "/staff/reconciliation/statements",
        data={
            "source": "iotec",
            "account_reference": network,
            "statement_date": day.isoformat(),
            "opening_balance": str(opening),
            "closing_balance": str(closing),
            "format": "standard",
        },
        files={"file": (f"{network}-{day}.csv", (HEADER + rows).encode(), "text/csv")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return closing


async def pair_by_hand(world: World, staff: Person, day: date) -> list[int]:
    """What staff do at the close: pair each bank line the references left alone with the
    ledger movement of the same amount and direction. Returns the signed amounts paired."""
    trust = next(pot for pot in CASH_POTS if pot.account_reference == "trust")
    async with world.api_database.actor_transaction(staff.actor) as session:
        await day_close.refresh(session, staff.actor, day, world.clock)
        found = await day_close.open_exceptions(session, trust, day)
        lines = [line for item in found for line in item.statement_lines]
        moves = [move for item in found for move in item.movements]
        paired = []
        for line in lines:
            move = next(
                m for m in moves if (m.amount, m.direction) == (line.amount, line.direction)
            )
            moves.remove(move)
            entry_id, _, line_no = move.line_id.rpartition(":")
            await day_close.match_by_hand(
                session,
                staff.actor,
                statement_line_id=line.fingerprint,
                entry_id=entry_id,
                line_no=int(line_no),
                reason="Same transfer: the reviewed receipt names this line",
                clock=world.clock,
            )
            sign = 1 if line.direction is Direction.IN else -1
            paired.append(sign * line.amount.minor)
        assert moves == [], moves
    return paired


async def test_each_day_ties_to_its_statements_and_is_closed_by_two_people(
    world: World, migrator_database: Database, bank: TrustBank, collected: dict[str, Collected]
) -> None:
    now = today(world)
    first = now - timedelta(days=TrustBank.FIRST_DAY_BACK)
    # Importing is a change to reconciliation, so it takes finance too: a third person, since
    # whoever imports a day's statement cannot approve its close.
    importer, preparer, approver = (
        await world.staff("finance"),
        await world.staff("finance"),
        await world.staff("finance"),
    )
    days = [first + timedelta(days=n) for n in range((now - first).days)]
    # The statements nobody had a reason to import yet: quiet days on every pot.
    wallet = dict.fromkeys(IOTEC_POTS, 0)
    for day in days:
        if day not in bank.lines and (bank.next_day or first) <= day:
            await bank.statement(world, importer, day, [])
        for network in IOTEC_POTS:
            wallet[network] = await import_iotec(world, importer, network, day, [], wallet[network])

    signers: list[str] = [preparer.id, approver.id]
    for day in days:
        world.clock.current = max(world.clock.current, morning_after(day))
        # Each morning's two people sign in that morning, through the console's routes.
        opener, closer = await world.staff("finance"), await world.staff("finance")
        signers += [opener.id, closer.id]
        base = f"/staff/reconciliation/days/{day.isoformat()}"
        signed = await opener.http.post(f"{base}/prepare")
        assert signed.status_code == 200, signed.text
        figures = (await opener.http.get(base)).json()
        assert figures["close"]["status"] == "prepared", figures
        # A transfer is on the statement the day it arrived and in the books the day staff
        # confirmed it, so until then it stands open, and accounted for.
        assert all(p["unexplained"]["minor"] == 0 for p in figures["positions"]), figures
        own = await opener.http.post(f"{base}/approve")
        assert own.json()["error"]["fields"][0]["code"] == "close_needs_second_person", own.text
        closed = await closer.http.post(f"{base}/approve")
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"

    async with world.api_database.actor_transaction(approver.actor) as session:
        assert await ledger_closing.closed_through(session) == days[-1]
    trail = await audit_for(migrator_database, signers)
    assert missing(trail, "reconciliation.day_close_prepared", "reconciliation.day_closed") == []

    # Today: every mobile money collection the journeys made, as ioTec's statement shows it.
    world.clock.current = morning_after(now)
    # A day or more has passed since the morning's sign-in, so the importer signs in again.
    importer = await world.staff("finance")
    for network in IOTEC_POTS:
        mine = [c for c in collected.values() if c.confirmed_at.astimezone(KAMPALA).date() == now]
        await import_iotec(
            world, importer, network, now, mine if network == "mtn" else [], wallet[network]
        )
    # The bank shows what the dealing desk paid for bills today, under the desk's reference.
    async with world.api_database.actor_transaction(importer.actor) as session:
        dealt = await holdings.expected_bank_movements(session, date_from=now, date_to=now)
    await bank.statement(
        world, importer, now, [(move.bank_reference, move.amount_minor) for move in dealt]
    )
    # Every collection pairs with its ledger movement by its reference. The bank's lines pair
    # with what was recorded about them: the transfer's approved review names its line, and the
    # dealing desk gives the bank's reference. Nothing is left for staff to pair by hand.
    hand_matched = await pair_by_hand(world, preparer, now)
    assert hand_matched == [], hand_matched
    assert gaps.HAND_MATCHED == ()
    async with world.api_database.actor_transaction(preparer.actor) as session:
        prepared = await day_close.prepare(session, preparer.actor, now, world.clock)
        for pot in CASH_POTS:
            assert await day_close.open_exceptions(session, pot, now) == (), pot
    positions = {p.account_reference: p for p in prepared.positions}
    assert positions["mtn"].statement_closing == positions["mtn"].ledger_closing, positions
    assert positions["mtn"].open_exceptions_count == 0, positions["mtn"]
    assert all(p.unexplained == ugx(0) for p in positions.values()), positions
    # Signing today off waits for today to end in Kampala: the database will not close a day
    # that has not ended, whatever the application's clock says.
    with pytest.raises(DayNotEnded):
        async with world.api_database.actor_transaction(approver.actor) as session:
            await day_close.approve(session, approver.actor, now, world.clock)
