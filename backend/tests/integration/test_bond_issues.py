"""Bond issues and their prices against real PostgreSQL: two people to every change.

Issues are never removed and tests share one database, so every test makes its own issue with
its own code and ISIN, and any issue a test opens is paused again before the test ends.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import app.api.app  # noqa: F401 - loads the modules in the order the app does, which has no cycle
from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.bonds import catalogue
from app.modules.bonds.domain.changes import BondChangeAction, BondChangeStatus
from app.modules.bonds.domain.issues import BondStatus
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


def at(day: date) -> FakeClock:
    """Midday in Kampala on `day`."""
    return FakeClock(datetime(day.year, day.month, day.day, 9, tzinfo=UTC))


NOW = at(date(2026, 11, 16))
PAR = 1_000_000


def fresh() -> tuple[str, str]:
    """A new code and ISIN."""
    tag = uuid.uuid4().hex[:8].upper()
    return f"BOND-{tag}", f"UGT{tag}0"


def new_issue(code: str, isin: str, **overrides: object) -> catalogue.NewIssue:
    values: dict[str, object] = {
        "code": code,
        "isin": isin,
        "name": "10 Year Treasury Bond 2037",
        "currency": Currency.UGX,
        "coupon_bps": 1600,
        "tax_bps": 1000,
        "issue_date": date(2025, 5, 14),
        "maturity_date": date(2037, 5, 14),
        "minimum_face_minor": 100_000,
        "face_step_minor": 100_000,
        "price": 985_000,
    }
    values.update(overrides)
    return catalogue.NewIssue(**values)  # type: ignore[arg-type]


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def propose(
    database: Database, staff: Actor, code: str, isin: str, **overrides: object
) -> catalogue.ChangeView:
    async with database.actor_transaction(staff) as session:
        return await catalogue.propose_issue(
            session, staff, new_issue(code, isin, **overrides), "The May 2037 bond", NOW
        )


async def approve(database: Database, staff: Actor, change_id: str) -> catalogue.ChangeView:
    async with database.actor_transaction(staff) as session:
        return await catalogue.approve(session, staff, change_id, NOW)


async def created(database: Database) -> str:
    code, isin = fresh()
    change = await propose(database, new_staff(), code, isin)
    await approve(database, new_staff(), change.id)
    return code


async def set_status(database: Database, code: str, to: BondStatus) -> None:
    maker = new_staff()
    async with database.actor_transaction(maker) as session:
        change = await catalogue.propose_status(session, maker, code, to, "Test", NOW)
    await approve(database, new_staff(), change.id)


async def issue(database: Database, code: str) -> catalogue.IssueView:
    async with database.actor_transaction(new_staff()) as session:
        return await catalogue.staff_issue(session, new_staff(), code)


async def test_a_new_issue_waits_for_a_second_person_then_starts_as_a_draft(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    code, isin = fresh()
    change = await propose(app_database, daniel, code, isin)
    assert (change.action, change.status, change.price) == (
        BondChangeAction.CREATE,
        BondChangeStatus.PROPOSED,
        985_000,
    )
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as missing:
            await catalogue.staff_issue(session, daniel, code)
    assert missing.value.code == "not_found"

    with pytest.raises(ApplicationError) as own:
        await approve(app_database, daniel, change.id)
    assert own.value.code == "forbidden"

    decided = await approve(app_database, grace, change.id)
    assert (decided.status, decided.decided_by) == (BondChangeStatus.APPROVED, grace.id)
    view = await issue(app_database, code)
    assert (view.issue.status, view.issue.isin, view.price, view.version) == (
        BondStatus.DRAFT,
        isin,
        985_000,
        1,
    )
    # A draft is never shown to clients or on the website.
    async with app_database.transaction() as session:
        listed = [b.code for b in await catalogue.client_issues(session, NOW)]
    assert code not in listed
    async with app_database.actor_transaction(new_staff()) as session:
        events = await events_for_resource(session, "bond_change", change.id)
    assert sorted(e.action for e in events) == ["bonds.change.approved", "bonds.change.proposed"]


async def test_a_code_or_isin_is_taken_by_an_issue_or_one_waiting(app_database: Database) -> None:
    code, isin = fresh()
    await propose(app_database, new_staff(), code, isin)
    other_code, other_isin = fresh()
    with pytest.raises(ApplicationError) as by_code:
        await propose(app_database, new_staff(), code, other_isin)
    with pytest.raises(ApplicationError) as by_isin:
        await propose(app_database, new_staff(), other_code, isin)
    assert refused(by_code) == ("validation_failed", [("code", "code_taken")])
    assert refused(by_isin) == ("validation_failed", [("isin", "isin_taken")])


async def test_an_issue_that_contradicts_itself_is_refused(app_database: Database) -> None:
    code, isin = fresh()
    with pytest.raises(ApplicationError) as dates:
        await propose(app_database, new_staff(), code, isin, maturity_date=date(2020, 1, 1))
    with pytest.raises(ApplicationError) as steps:
        await propose(app_database, new_staff(), code, isin, minimum_face_minor=150_000)
    assert refused(dates) == ("validation_failed", [("issue", "invalid")])
    assert refused(steps) == ("validation_failed", [("issue", "invalid")])


async def test_opening_needs_two_people_and_clients_then_see_it_after_tax(
    app_database: Database,
) -> None:
    code = await created(app_database)
    await set_status(app_database, code, BondStatus.LIVE)
    try:
        async with app_database.actor_transaction(new_client()) as session:
            bond = await catalogue.client_issue(session, code, NOW)
            found = await catalogue.client_bond_quote(session, code, 1_000_000, NOW)
        # 16% before 10% tax.
        assert (bond.coupon_percent, bond.can_buy, bond.next_coupon_date) == (
            "14.40",
            True,
            date(2027, 5, 14),
        )
        # 985,000 clean, and two days of the 181 day period since 14 November: 80,000 x 2/181.
        assert (found.price, found.accrued.minor) == (985_000, 883)
        assert found.cost.minor == 985_000 + 883
        assert found.coupon_net.minor == 72_000
        assert found.coupons_left == 21
        assert found.total_back.minor == 1_000_000 + 21 * 72_000
    finally:
        await set_status(app_database, code, BondStatus.PAUSED)


async def test_a_price_change_is_in_force_once_approved(app_database: Database) -> None:
    code = await created(app_database)
    maker = new_staff()
    async with app_database.actor_transaction(maker) as session:
        change = await catalogue.propose_price(session, maker, code, 1_012_500, "Auction", NOW)
        with pytest.raises(ApplicationError) as twice:
            await catalogue.propose_price(session, maker, code, 1_013_000, "Again", NOW)
    assert twice.value.code == "conflict"
    assert (await issue(app_database, code)).price == 985_000
    await approve(app_database, new_staff(), change.id)
    assert (await issue(app_database, code)).price == 1_012_500

    async with app_database.actor_transaction(maker) as session:
        with pytest.raises(ApplicationError) as same:
            await catalogue.propose_price(session, maker, code, 1_012_500, "Same", NOW)
    assert refused(same) == ("validation_failed", [("price", "same_price")])


async def test_sending_back_needs_a_note_and_only_the_proposer_withdraws(
    app_database: Database,
) -> None:
    code, isin = fresh()
    daniel, grace = new_staff(), new_staff()
    change = await propose(app_database, daniel, code, isin)
    async with app_database.actor_transaction(grace) as session:
        with pytest.raises(ApplicationError) as not_theirs:
            await catalogue.withdraw(session, grace, change.id, NOW)
    assert not_theirs.value.code == "forbidden"
    async with app_database.actor_transaction(grace) as session:
        sent = await catalogue.send_back(session, grace, change.id, "Wrong ISIN", NOW)
    assert (sent.status, sent.decision_note) == (BondChangeStatus.SENT_BACK, "Wrong ISIN")
    with pytest.raises(ApplicationError) as decided:
        await approve(app_database, new_staff(), change.id)
    assert decided.value.code == "conflict"


async def test_the_application_cannot_write_an_issue_or_price_without_an_approved_change(
    app_database: Database,
) -> None:
    code = await created(app_database)
    staff = new_staff()
    with pytest.raises(DBAPIError, match="approved change"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("UPDATE bond_issues SET status = 'live' WHERE code = :code"),
                {"code": code},
            )
    with pytest.raises(DBAPIError, match=r"never changed or removed|permission denied"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE bond_prices SET price = 1 FROM bond_issues b "
                    "WHERE b.id = bond_prices.issue_id AND b.code = :code"
                ),
                {"code": code},
            )
    with pytest.raises(DBAPIError, match=r"never removed|permission denied"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("DELETE FROM bond_issues WHERE code = :code"), {"code": code}
            )


async def test_an_approved_opening_cannot_be_replayed(app_database: Database) -> None:
    code = await created(app_database)
    daniel, grace = new_staff(), new_staff()
    async with app_database.actor_transaction(daniel) as session:
        change = await catalogue.propose_status(
            session, daniel, code, BondStatus.LIVE, "Launch", NOW
        )
    await approve(app_database, grace, change.id)
    await set_status(app_database, code, BondStatus.PAUSED)
    with pytest.raises(DBAPIError, match="one approved change at a time"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(
                text(
                    "UPDATE bond_issues SET status = 'live', change_id = :change, "
                    "version = version + 1 WHERE code = :code"
                ),
                {"change": change.id, "code": code},
            )
    assert (await issue(app_database, code)).issue.status is BondStatus.PAUSED


async def test_an_approval_that_writes_nothing_is_refused_at_commit(
    app_database: Database,
) -> None:
    code, isin = fresh()
    change = await propose(app_database, new_staff(), code, isin)
    grace = new_staff()
    with pytest.raises(DBAPIError, match="written to the catalogue"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(
                text(
                    "UPDATE bond_changes SET status = 'approved', decided_by = :me, "
                    "decided_at = now() WHERE id = :id"
                ),
                {"me": grace.id, "id": change.id},
            )


async def test_nobody_decides_in_another_persons_name(app_database: Database) -> None:
    code, isin = fresh()
    change = await propose(app_database, new_staff(), code, isin)
    grace, someone = new_staff(), new_staff()
    with pytest.raises(DBAPIError, match="person signed in"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(
                text(
                    "UPDATE bond_changes SET status = 'sent_back', decided_by = :them, "
                    "decided_at = now(), decision_note = 'x' WHERE id = :id"
                ),
                {"them": someone.id, "id": change.id},
            )


async def test_two_people_approving_at_once_make_one_issue(app_database: Database) -> None:
    code, isin = fresh()
    change = await propose(app_database, new_staff(), code, isin)
    barrier = AsyncBarrier(2)

    async def decide() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await catalogue.approve(session, staff, change.id, NOW)
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(decide(), decide())) == ["approved", "conflict"]
    assert (await issue(app_database, code)).version == 1


async def test_clients_never_read_bond_changes(app_database: Database) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError) as error:
            await catalogue.list_changes(session, client)
    assert error.value.code == "forbidden"
    async with app_database.actor_transaction(client) as session:
        rows = await session.execute(text("SELECT count(*) FROM bond_changes"))
    assert rows.scalar_one() == 0
