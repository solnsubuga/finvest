"""Coupons paid in on the dealing desk's bonds, against real PostgreSQL: recorded by one person
and approved by another, posted as income, expected on the bank statement, and what the
database refuses whatever the application does.

Each test makes its own issue and buys its bonds a few days before the issue's latest coupon
date, so the desk holds them on the record date. The time is the real time, so that coupon
date has come.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.bonds import catalogue
from app.modules.bonds.domain.issues import coupon_dates
from app.modules.dealing import bond_coupons, holdings
from app.modules.dealing import orders as dealing
from app.modules.dealing.domain.bond_coupons import BANK_TRUST, COUPON_INCOME, CouponStatus
from app.modules.dealing.domain.orders import Fill, Instrument, OrderKind
from app.modules.ledger import service as ledger
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.integration.test_bond_holdings import live_issue, now
from tests.integration.test_dealing_settlement import fund_bank, refused
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

M = 1_000_000
# The live_issue default pays 16% a year: 80,000 a coupon on each million of face value.
PER_MILLION = 80_000


async def last_coupon(database: Database, code: str, clock: Clock) -> date:
    async with database.actor_transaction(new_staff()) as session:
        issue = (await catalogue.issue_view(session, code)).issue
    today = business_date(clock.now())
    return max(d for d in coupon_dates(issue) if d <= today)


async def held_before(database: Database, code: str, coupon: date, face: int, clock: Clock) -> None:
    """Bonds of `code` bought and settled three days before `coupon`."""
    async with database.actor_transaction(new_staff()) as session:
        matures = (await catalogue.issue_view(session, code)).issue.maturity_date
    await fund_bank(database, face)
    preparer, approver, confirmer = new_staff(), new_staff(), new_staff()
    async with database.actor_transaction(preparer) as session:
        order = await dealing.prepare_order(
            session,
            preparer,
            kind=OrderKind.SECONDARY_PURCHASE,
            product_code=code,
            face=ugx(face),
            clock=clock,
            instrument=Instrument.BOND,
        )
    async with database.actor_transaction(approver) as session:
        await dealing.approve_order(session, approver, order.id, clock)
    fill = Fill(ugx(face), ugx(face), coupon - timedelta(days=3), matures, f"CSD-{push_id()[-8:]}")
    async with database.actor_transaction(preparer) as session:
        await dealing.record_result(session, preparer, order.id, fill, clock)
    async with database.actor_transaction(confirmer) as session:
        await dealing.confirm_result(session, confirmer, order.id, clock)


async def record(
    database: Database,
    staff: Actor,
    code: str,
    coupon: date,
    amount: int,
    clock: Clock,
    *,
    reference: str | None = None,
    note: str | None = None,
    value_date: date | None = None,
) -> bond_coupons.CouponView:
    async with database.actor_transaction(staff) as session:
        return await bond_coupons.record(
            session,
            staff,
            issue_code=code,
            coupon_date=coupon,
            amount=ugx(amount),
            value_date=value_date or coupon,
            bank_reference=reference or f"FT{push_id()[-10:]}",
            note=note,
            clock=clock,
        )


async def entry_codes(database: Database, entry_id: str) -> dict[str, int]:
    async with database.actor_transaction(new_staff()) as session:
        entry = await ledger.get_entry(session, entry_id)
        found = {}
        for line in entry.lines:
            account = await session.execute(
                text("SELECT code FROM ledger_accounts WHERE id = :id"), {"id": line.account_id}
            )
            found[account.scalar_one()] = line.amount.minor
        return found


async def test_one_person_records_a_coupon_and_another_posts_it_as_income(
    app_database: Database,
) -> None:
    clock = now()
    daniel, grace = new_staff(), new_staff()
    async with live_issue(app_database, clock) as code:
        coupon = await last_coupon(app_database, code, clock)
        await held_before(app_database, code, coupon, 2 * M, clock)
        async with app_database.actor_transaction(daniel) as session:
            due = await bond_coupons.due(session, Currency.UGX, clock)
            due_before = [d for d in due if d.issue_code == code]
        recorded = await record(app_database, daniel, code, coupon, 2 * PER_MILLION, clock)
        async with app_database.actor_transaction(grace) as session:
            approved = await bond_coupons.approve(session, grace, recorded.id, clock)
            due = await bond_coupons.due(session, Currency.UGX, clock)
            due_after = [d for d in due if d.issue_code == code]
            movements = await holdings.expected_bank_movements(
                session, date_from=coupon, date_to=coupon
            )
    assert [(d.coupon_date, d.face, d.expected) for d in due_before] == [
        (coupon, ugx(2 * M), ugx(2 * PER_MILLION))
    ]
    assert (recorded.status, recorded.face, recorded.expected, recorded.note) == (
        CouponStatus.RECORDED,
        ugx(2 * M),
        ugx(2 * PER_MILLION),
        None,
    )
    assert (approved.status, approved.decided_by) == (CouponStatus.APPROVED, grace.id)
    assert approved.ledger_entry_id is not None
    assert await entry_codes(app_database, approved.ledger_entry_id) == {
        BANK_TRUST: 2 * PER_MILLION,
        COUPON_INCOME: -2 * PER_MILLION,
    }
    assert due_after == []
    movement = next(m for m in movements if m.ledger_entry_id == approved.ledger_entry_id)
    assert (movement.amount_minor, movement.bank_reference) == (
        2 * PER_MILLION,
        recorded.bank_reference,
    )


async def test_whoever_recorded_a_coupon_cannot_approve_it_and_a_decline_needs_a_reason(
    app_database: Database,
) -> None:
    clock = now()
    daniel, grace = new_staff(), new_staff()
    async with live_issue(app_database, clock) as code:
        coupon = await last_coupon(app_database, code, clock)
        await held_before(app_database, code, coupon, 1 * M, clock)
        recorded = await record(app_database, daniel, code, coupon, PER_MILLION, clock)
        with pytest.raises(ApplicationError) as own:
            async with app_database.actor_transaction(daniel) as session:
                await bond_coupons.approve(session, daniel, recorded.id, clock)
        with pytest.raises(ApplicationError) as no_reason:
            async with app_database.actor_transaction(grace) as session:
                await bond_coupons.decline(session, grace, recorded.id, "  ", clock)
        async with app_database.actor_transaction(grace) as session:
            declined = await bond_coupons.decline(
                session, grace, recorded.id, "Not on the statement yet", clock
            )
        # Declined, so it can be recorded again, with the same bank reference.
        again = await record(
            app_database,
            daniel,
            code,
            coupon,
            PER_MILLION,
            clock,
            reference=recorded.bank_reference,
        )
        with pytest.raises(ApplicationError) as twice:
            async with app_database.actor_transaction(grace) as session:
                await bond_coupons.decline(session, grace, recorded.id, "Again", clock)
    assert own.value.code == "forbidden"
    assert refused(no_reason) == ("validation_failed", [("reason", "reason_required")])
    assert (declined.status, declined.ledger_entry_id) == (CouponStatus.DECLINED, None)
    assert again.status is CouponStatus.RECORDED
    assert twice.value.code == "conflict"


async def test_a_coupon_that_differs_from_what_the_face_earns_needs_a_note(
    app_database: Database,
) -> None:
    clock = now()
    async with live_issue(app_database, clock) as code:
        coupon = await last_coupon(app_database, code, clock)
        await held_before(app_database, code, coupon, 1 * M, clock)
        with pytest.raises(ApplicationError) as error:
            await record(app_database, new_staff(), code, coupon, 72_000, clock)
        noted = await record(
            app_database, new_staff(), code, coupon, 72_000, clock, note="10% withheld by BoU"
        )
    assert refused(error) == ("validation_failed", [("note", "note_required")])
    assert (noted.amount, noted.expected, noted.note) == (
        ugx(72_000),
        ugx(PER_MILLION),
        "10% withheld by BoU",
    )


async def test_a_coupon_is_recorded_once_on_a_coupon_date_for_bonds_held(
    app_database: Database,
) -> None:
    clock = now()
    staff = new_staff()
    async with live_issue(app_database, clock) as code, live_issue(app_database, clock) as empty:
        coupon = await last_coupon(app_database, code, clock)
        await held_before(app_database, code, coupon, 1 * M, clock)
        first = await record(app_database, staff, code, coupon, PER_MILLION, clock)
        problems = []
        for issue_code, day, kwargs in [
            (code, coupon, {}),
            (code, coupon - timedelta(days=1), {}),
            (code, min(d for d in await _dates(app_database, code) if d > coupon), {}),
            (empty, coupon, {}),
            (code, coupon, {"value_date": coupon - timedelta(days=1)}),
        ]:
            with pytest.raises(ApplicationError) as error:
                await record(app_database, staff, issue_code, day, PER_MILLION, clock, **kwargs)  # type: ignore[arg-type]
            problems.append(refused(error)[1])
        with pytest.raises(ApplicationError) as reused:
            await record(
                app_database,
                staff,
                code,
                max(d for d in await _dates(app_database, code) if d < coupon),
                PER_MILLION,
                clock,
                reference=first.bank_reference,
            )
    assert problems == [
        [("coupon_date", "already_recorded")],
        [("coupon_date", "not_a_coupon_date")],
        [("coupon_date", "coupon_date_in_future")],
        [("coupon_date", "nothing_held")],
        [("value_date", "value_date_before_coupon")],
    ]
    # The coupon before was not held, so it is refused for that before the reference is seen.
    assert refused(reused) == ("validation_failed", [("coupon_date", "nothing_held")])


async def _dates(database: Database, code: str) -> tuple[date, ...]:
    async with database.actor_transaction(new_staff()) as session:
        return coupon_dates((await catalogue.issue_view(session, code)).issue)


async def test_the_same_bank_reference_is_recorded_once(app_database: Database) -> None:
    clock = now()
    staff = new_staff()
    async with live_issue(app_database, clock) as one, live_issue(app_database, clock) as two:
        coupon = await last_coupon(app_database, one, clock)
        await held_before(app_database, one, coupon, 1 * M, clock)
        await held_before(app_database, two, coupon, 1 * M, clock)
        first = await record(app_database, staff, one, coupon, PER_MILLION, clock)
        with pytest.raises(ApplicationError) as error:
            await record(
                app_database, staff, two, coupon, PER_MILLION, clock, reference=first.bank_reference
            )
    assert refused(error) == ("validation_failed", [("bank_reference", "already_recorded")])


async def test_the_database_refuses_an_approval_without_its_entry_or_a_changed_coupon(
    app_database: Database,
) -> None:
    clock = now()
    daniel, grace = new_staff(), new_staff()
    async with live_issue(app_database, clock) as code:
        coupon = await last_coupon(app_database, code, clock)
        await held_before(app_database, code, coupon, 1 * M, clock)
        recorded = await record(app_database, daniel, code, coupon, PER_MILLION, clock)
        for statement, match in [
            (
                "UPDATE dealing_bond_coupons SET status = 'approved', decided_by = :by, "
                "decided_at = now(), ledger_entry_id = :entry WHERE id = :id",
                "names the entry it posted",
            ),
            ("DELETE FROM dealing_bond_coupons WHERE id = :id", "permission denied"),
        ]:
            with pytest.raises(DBAPIError, match=match):
                async with app_database.actor_transaction(grace) as session:
                    await session.execute(
                        text(statement), {"by": grace.id, "entry": push_id(), "id": recorded.id}
                    )
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(grace) as session:
                await session.execute(
                    text("UPDATE dealing_bond_coupons SET amount_minor = 1 WHERE id = :id"),
                    {"id": recorded.id},
                )
