"""A paid payout that came back: two people take it back from the statement, the client is told
without the amount, and the client sends it again.

A return is named by a statement line dated on or after the day the payout was paid, which is
today, so this file has a database of its own and imports statements from today on, as if those
days had ended.
"""

import os
import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.payouts import payouts, returns
from app.modules.payouts.domain.payouts import PayoutEvent, PayoutStatus
from app.modules.payouts.payouts import PayoutView
from app.modules.payouts.returns import ReturnRefused, ReturnStatus
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.push_ids import push_id
from tests.support import database as harness
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import running_api
from tests.support.ledger import key, staff, ugx
from tests.support.payouts import World

pytestmark = pytest.mark.integration

AMOUNT = 1_500_000
FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)


@pytest.fixture(scope="module")
def test_database() -> Iterator[TestDatabase]:
    """A database of this file's own, so its statements can start today."""
    database = harness.create(f"finvest_test_returns_{os.getpid()}")
    try:
        result = harness.migrate(database)
        if result.returncode != 0:
            pytest.fail(f"migrations failed:\n{result.stdout}\n{result.stderr}")
        harness.allow_test_entries_for_staff(database)
        yield database
    finally:
        harness.drop(database)


def now() -> FakeClock:
    return FakeClock(datetime.now(UTC))


class Pot:
    """One pot's statements in this file's database, one day after another."""

    def __init__(self, source: StatementSource, reference: str) -> None:
        self.source, self.reference = source, reference
        self.day: date | None = None
        self.closing = 10_000_000_000

    async def statement(self, database: Database, rows: tuple[tuple[str, int], ...]) -> str:
        """The next day's statement with lines coming in, as (reference, amount). Returns the
        first line's ID."""
        # The world pays its payouts a day ahead, past its new account's wait.
        start = business_date(datetime.now(UTC) + timedelta(days=1))
        day = start if self.day is None else self.day + timedelta(1)
        # Each line's reference is made unique, so the first one can be found again.
        tag = secrets.token_hex(4)
        lines = "".join(
            f"{day.isoformat()},Returned {n},{reference}-{tag},{amount},\n"
            for n, (reference, amount) in enumerate(rows)
        )
        closing = self.closing + sum(amount for _, amount in rows)
        parsed = parse_csv(
            "Date,Details,Reference,In,Out\n" + lines,
            FORMAT,
            source=self.source,
            account_reference=self.reference,
            statement_date=day,
            opening_balance=ugx(self.closing),
            closing_balance=ugx(closing),
        )
        later = FakeClock(datetime.now(UTC) + timedelta(days=(day - date.today()).days + 2))
        importer = staff()
        async with database.actor_transaction(importer) as session:
            imported = await statements.import_statement(
                session, importer, parsed, secrets.token_hex(32), later
            )
            found = await statements.find_statement_lines(
                session, source=self.source, date_from=day, date_to=day
            )
        self.day, self.closing = day, closing
        assert imported.id
        first = f"{rows[0][0]}-{tag}"
        return next(
            line.id
            for line in found
            if line.reference == first and line.account_reference == self.reference
        )


MTN = Pot(StatementSource.IOTEC, "mtn")
TRUST = Pot(StatementSource.BANK, "trust")


async def paid_by_phone(world: World, amount: int = AMOUNT) -> PayoutView:
    payout = await world.request(amount)
    paid = await world.worker_event(payout.id, PayoutEvent.SEND_CONFIRMED)
    assert paid.status is PayoutStatus.PAID
    return paid


@pytest.fixture
async def world(app_database: Database, worker_database: Database) -> World:
    return await World(app_database, worker_database, now()).build(AMOUNT * 3)


async def propose(
    database: Database, payout_id: str, line_id: str, by: Actor
) -> returns.ReturnView:
    async with database.actor_transaction(by) as session:
        return await returns.propose(
            session,
            by,
            payout_id,
            statement_line_id=line_id,
            reason="Came back on the statement",
            idempotency_key=key(),
            clock=now(),
        )


async def approve(database: Database, return_id: str, by: Actor) -> returns.ReturnView:
    async with database.actor_transaction(by) as session:
        return await returns.approve(
            session, by, return_id, reason="Agree", idempotency_key=key(), clock=now()
        )


async def refused(coro: object) -> str:
    with pytest.raises(ReturnRefused) as refusal:
        await coro  # type: ignore[misc]
    return refusal.value.code


async def test_two_people_take_back_a_payout_and_the_client_is_told_without_the_amount(
    world: World, app_database: Database, worker_database: Database
) -> None:
    payout = await paid_by_phone(world)
    holding_before = await world.balance(world.holding)
    pot_before = await world.pot_change()
    line = await MTN.statement(app_database, (("reversal", AMOUNT),))

    maker = staff()
    proposed = await propose(app_database, payout.id, line, maker)
    assert proposed.status is ReturnStatus.PROPOSED
    assert await refused(approve(app_database, proposed.id, maker)) == "proposed_by_you"
    assert (
        await refused(propose(app_database, payout.id, line, staff())) == "return_already_recorded"
    )
    approved = await approve(app_database, proposed.id, staff())
    assert approved.status is ReturnStatus.APPROVED
    assert approved.ledger_entry_id is not None

    # The money is in the pot again, and owed to the client again where it was paid from.
    assert await world.balance(world.holding) == holding_before + AMOUNT
    assert await world.pot_change() == pot_before + AMOUNT
    await world.no_drift()
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert (await payouts.get_payout(session, payout.id)).status is PayoutStatus.PAID
        told = (
            await session.execute(
                text(
                    "SELECT body, sms FROM notifications WHERE client_id = :client "
                    "AND link_id = :id AND event = 'payout_returned'"
                ),
                {"client": world.client_id, "id": payout.id},
            )
        ).one()
        entry = await ledger.get_entry(session, approved.ledger_entry_id)
    # Where it was sent, never how much.
    assert "MTN Mobile Money" in told.body
    assert "1,500,000" not in told.body + told.sms
    assert "1500000" not in told.body + told.sms
    assert (entry.entry_type, entry.source_id) == ("payout.returned", payout.id)


async def test_the_line_must_be_money_coming_back_for_the_amount(
    world: World, app_database: Database
) -> None:
    payout = await paid_by_phone(world, 900_001)
    wrong_amount = await MTN.statement(app_database, (("reversal", 900_002),))
    assert (
        await refused(propose(app_database, payout.id, wrong_amount, staff()))
        == "statement_line_not_this_payout"
    )
    # The trust account is not the pot a mobile money payout was paid from.
    other_pot = await TRUST.statement(app_database, (("reversal", 900_001),))
    assert (
        await refused(propose(app_database, payout.id, other_pot, staff()))
        == "statement_line_not_this_payout"
    )
    queued = await world.request(100_000)
    line = await MTN.statement(app_database, (("reversal", 100_000),))
    assert await refused(propose(app_database, queued.id, line, staff())) == "payout_not_paid"


async def test_a_statement_line_takes_back_one_payout(world: World, app_database: Database) -> None:
    first, second = await paid_by_phone(world, 300_000), await paid_by_phone(world, 300_000)
    line = await MTN.statement(app_database, (("reversal", 300_000),))
    await propose(app_database, first.id, line, staff())
    assert await refused(propose(app_database, second.id, line, staff())) == "statement_line_in_use"


async def test_a_declined_or_withdrawn_return_posts_nothing_and_can_be_proposed_again(
    world: World, app_database: Database
) -> None:
    payout = await paid_by_phone(world, 400_000)
    holding = await world.balance(world.holding)
    line = await MTN.statement(app_database, (("reversal", 400_000),))
    maker = staff()
    first = await propose(app_database, payout.id, line, maker)
    async with app_database.actor_transaction(maker) as session:
        withdrawn = await returns.withdraw(session, maker, first.id, reason="Wrong", clock=now())
    assert (withdrawn.status, withdrawn.ledger_entry_id) == (ReturnStatus.WITHDRAWN, None)
    second = await propose(app_database, payout.id, line, maker)
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        declined = await returns.decline(session, checker, second.id, reason="No", clock=now())
    assert declined.status is ReturnStatus.DECLINED
    assert await world.balance(world.holding) == holding


async def test_the_database_refuses_an_approval_without_the_right_entry(
    world: World, app_database: Database
) -> None:
    payout = await paid_by_phone(world, 500_000)
    line = await MTN.statement(app_database, (("reversal", 500_000),))
    proposed = await propose(app_database, payout.id, line, staff())
    checker = staff()
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            # Any entry will not do: it must be this payout's return.
            any_entry = (
                await session.execute(text("SELECT id FROM ledger_journal_entries LIMIT 1"))
            ).scalar_one()
            await session.execute(
                text(
                    "UPDATE payout_returns SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x', ledger_entry_id = :entry "
                    "WHERE id = :id"
                ),
                {"me": checker.id, "entry": any_entry, "id": proposed.id},
            )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                text("DELETE FROM payout_returns WHERE id = :id"), {"id": proposed.id}
            )


async def test_the_client_sees_it_came_back_and_sends_it_again(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    async with running_api(test_database, app_database) as api:
        api.clock.current = datetime.now(UTC)
        owner = await api.client()
        world = World(app_database, worker_database, now())
        world.client = owner
        await world.build(AMOUNT * 3)
        payout = await paid_by_phone(world)
        line = await MTN.statement(app_database, (("reversal", AMOUNT),))
        proposed = await propose(app_database, payout.id, line, staff())
        await approve(app_database, proposed.id, staff())
        holding = await world.balance(world.holding)

        api.clock.current = world.clock.now()
        http: AsyncClient = api.http
        seen = await http.get(f"/client/payouts/{payout.id}")
        assert seen.status_code == 200, seen.text
        assert (seen.json()["status"], seen.json()["returned_at"] is not None) == ("paid", True)
        assert seen.json()["resent_as"] is None

        body = {"destination_id": world.destination_id}
        sent = await http.post(f"/client/payouts/{payout.id}/resend", json=body)
        assert sent.status_code == 201, sent.text
        again = await http.post(f"/client/payouts/{payout.id}/resend", json=body)
        assert again.json()["id"] == sent.json()["id"]
        assert sent.json()["amount"]["minor"] == AMOUNT
        assert sent.json()["status"] == "processing"
        assert sent.json()["resent_as"] is None
        # The payout that came back now points at the one it was sent again as.
        after = await http.get(f"/client/payouts/{payout.id}")
        assert after.json()["resent_as"] == sent.json()["id"]
        listed = {item["id"]: item for item in (await http.get("/client/payouts")).json()}
        assert listed[payout.id]["resent_as"] == sent.json()["id"]
        assert listed[sent.json()["id"]]["resent_as"] is None

        other = await http.post(f"/client/payouts/{push_id()}/resend", json=body)
        assert other.status_code == 404
    # The new payout is funded from the same handover the money came back to.
    assert await world.balance(world.holding) == holding - AMOUNT


async def test_a_payout_that_did_not_come_back_cannot_be_sent_again(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    async with running_api(test_database, app_database) as api:
        api.clock.current = datetime.now(UTC)
        owner = await api.client()
        world = World(app_database, worker_database, now())
        world.client = owner
        await world.build(AMOUNT)
        payout = await paid_by_phone(world, 200_000)
        api.clock.current = world.clock.now()
        refused_resend = await api.http.post(
            f"/client/payouts/{payout.id}/resend", json={"destination_id": world.destination_id}
        )
    assert refused_resend.status_code == 409
    assert refused_resend.json()["error"]["fields"][0]["code"] == "payout_not_returned"


async def test_staff_pick_the_paid_payout_and_its_line_coming_back(
    world: World, app_database: Database
) -> None:
    amount = 1_234_001
    payout = await paid_by_phone(world, amount)
    line = await MTN.statement(app_database, (("picked", amount), ("other", amount + 1)))

    # Searching by the amount that came in finds the payout, with where it went masked.
    async with app_database.actor_transaction(staff()) as session:
        found = await returns.returnable(session, amount=ugx(amount), now=datetime.now(UTC))
        lines = await returns.return_lines(session, payout.id)
    assert [each.payout.id for each in found] == [payout.id]
    assert found[0].network == "mtn"
    assert "•" in found[0].masked_account
    # Only lines coming in for its amount on the pot it was paid from.
    assert [each.id for each in lines] == [line]

    # Once a return names the payout and the line, neither is offered again.
    proposed = await propose(app_database, payout.id, line, staff())
    async with app_database.actor_transaction(staff()) as session:
        found = await returns.returnable(session, amount=ugx(amount), now=datetime.now(UTC))
        lines = await returns.return_lines(session, payout.id)
        shown = await returns.details(session, proposed)
    assert found == []
    assert lines == []
    # The second person sees the payout, where it went and the line itself, not only IDs.
    assert (shown.payout.id, shown.payout.amount, shown.network) == (payout.id, ugx(amount), "mtn")
    assert "•" in shown.masked_account
    assert shown.line is not None
    assert (shown.line.id, shown.line.amount) == (line, ugx(amount))


async def test_only_a_paid_payout_has_lines_to_pick(world: World, app_database: Database) -> None:
    payout = await world.request(AMOUNT)
    async with app_database.actor_transaction(staff()) as session:
        assert await refused(returns.return_lines(session, payout.id)) == "payout_not_paid"


async def test_the_picker_routes_answer_staff(
    world: World, test_database: TestDatabase, app_database: Database
) -> None:
    amount = 1_234_777
    payout = await paid_by_phone(world, amount)
    line = await MTN.statement(app_database, (("route", amount),))
    async with running_api(test_database, app_database) as api:
        api.clock.current = datetime.now(UTC)
        await api.staff(role="operations")
        listed = await api.http.get(
            "/staff/payout-returns/returnable", params={"amount_minor": amount}
        )
        offered = await api.http.get(f"/staff/payouts/{payout.id}/return-lines")
    assert listed.status_code == 200, listed.text
    assert [each["id"] for each in listed.json()] == [payout.id]
    assert listed.json()[0]["kind"] == "mobile_money"
    assert offered.status_code == 200, offered.text
    assert [each["id"] for each in offered.json()] == [line]


async def test_a_return_shows_its_line_however_many_lines_share_its_amount(
    world: World, app_database: Database
) -> None:
    amount = 1_234_999
    payout = await paid_by_phone(world, amount)
    # More lines of the same amount than a statement search gives back, all before the real one.
    noise = tuple((f"same{n}", amount) for n in range(statements.MAX_FOUND + 1))
    await MTN.statement(app_database, noise)
    line = await MTN.statement(app_database, (("real", amount),))
    proposed = await propose(app_database, payout.id, line, staff())
    async with app_database.actor_transaction(staff()) as session:
        searched = await statements.find_statement_lines(
            session,
            source=StatementSource.IOTEC,
            date_from=date.today() - timedelta(days=1),
            date_to=date.today() + timedelta(days=400),
            amount=ugx(amount),
        )
        shown = await returns.details(session, proposed)
        unknown = await statements.get_statement_line(session, push_id())
    assert unknown is None
    # A search by amount stops short of it; the return still shows it, found by its ID.
    assert line not in [each.id for each in searched]
    assert shown.line is not None
    assert shown.line.id == line


async def test_a_waiting_return_carries_what_the_second_person_checks(
    world: World, test_database: TestDatabase, app_database: Database
) -> None:
    amount = 1_234_888
    payout = await paid_by_phone(world, amount)
    line = await MTN.statement(app_database, (("waiting", amount),))
    proposed = await propose(app_database, payout.id, line, staff())
    async with running_api(test_database, app_database) as api:
        api.clock.current = datetime.now(UTC)
        await api.staff(role="operations")
        one = await api.http.get(f"/staff/payout-returns/{proposed.id}")
        waiting = await api.http.get("/staff/payout-returns")
    assert one.status_code == 200, one.text
    body = one.json()
    assert body["payout"]["amount"] == {"minor": amount, "currency": "UGX"}
    assert body["payout"]["kind"] == "mobile_money"
    assert body["payout"]["network"] == "mtn"
    assert body["line"]["id"] == line
    assert body["line"]["amount"] == {"minor": amount, "currency": "UGX"}
    assert body["line"]["reference"].startswith("waiting-")
    assert waiting.status_code == 200, waiting.text
    assert body in waiting.json()
