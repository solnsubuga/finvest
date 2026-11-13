"""Withholding tax against real PostgreSQL: the month's return and the certificate read from the
ledger, and a payment to URA recorded by one person and approved by another.

Every test shares one database and the one `tax.withholding_payable` account, so a month's totals
include other tests' tax. Tests check their own clients' lines, and month totals as changes.
"""

import asyncio
import dataclasses
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import DocumentType, required_photos
from app.modules.kyc.providers import ManualProvider
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.tax import remittances, service, withholding
from app.modules.tax.domain.periods import month_end, month_of, months_back, tax_year_of
from app.modules.tax.domain.remittances import RemittanceStatus
from app.modules.tax.domain.withholding import MonthSummary
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import PRESENT, at, pay, request, today
from tests.support.kyc import SEALER, add_photos, details
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

TIN = "1000123456"
# A month that has ended, so a payment for it can be recorded today.
LAST_MONTH = months_back(month_of(today()), 1)


async def approved_client(database: Database, *, tin: str | None = None) -> Actor:
    """A client a member of staff approved, who gave a TIN if `tin` is set."""
    client = Actor("client", push_id())
    async with database.actor_transaction(client) as session:
        await kyc.choose_document(session, SEALER, client, DocumentType.NATIONAL_ID, "UG", PRESENT)
        await add_photos(
            session, client, PRESENT, *sorted(required_photos(DocumentType.NATIONAL_ID))
        )
        await kyc.confirm_details(
            session, SEALER, client, dataclasses.replace(details(), tin=tin), PRESENT
        )
        check = await kyc.submit(session, SEALER, client, ManualProvider(), PRESENT)
    reviewer = new_staff()
    async with database.actor_transaction(reviewer) as session:
        await kyc.approve(session, SEALER, reviewer, check.id, "Documents match", PRESENT)
    return client


async def running(
    app_database: Database, worker_database: Database, client: Actor, *, amount: int = 1_000_000
) -> investments.Investment:
    investment = await request(app_database, client, PRESENT, amount=ugx(amount))
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment.id, confirmed_on=today(), clock=PRESENT
        )


async def withhold(
    worker_database: Database,
    investment: investments.Investment,
    on: date,
    gross: int,
    tax: int,
) -> None:
    """An interest entry for the investment dated `on`, shaped as the daily run posts it, for a
    day the run itself cannot reach, such as last month."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        expense = await ledger.open_account(
            session,
            AccountSpec("interest.expense", AccountKind.EXPENSE, Currency.UGX, "Interest"),
            PRESENT,
        )
        payable = await ledger.open_account(
            session,
            AccountSpec("tax.withholding_payable", AccountKind.LIABILITY, Currency.UGX, "Tax"),
            PRESENT,
        )
        earned = await ledger.open_account(
            session,
            AccountSpec(
                "client.interest_accrued",
                AccountKind.LIABILITY,
                Currency.UGX,
                "Interest earned",
                owner_client_id=investment.client_id,
                reference=investment.id,
            ),
            PRESENT,
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.interest",
                effective_date=on,
                lines=(
                    (
                        debit(expense.id, ugx(gross)),
                        credit(earned.id, ugx(gross - tax)),
                        credit(payable.id, ugx(tax)),
                    )
                    if gross > 0
                    # Negative figures are a reversal, as an unwind posts it.
                    else (
                        credit(expense.id, ugx(-gross)),
                        debit(earned.id, ugx(tax - gross)),
                        debit(payable.id, ugx(-tax)),
                    )
                ),
                idempotency_key=key(),
                source_type="investment",
                source_id=investment.id,
            ),
            PRESENT,
        )


async def fund_trust(worker_database: Database, amount: int) -> None:
    """Money on the trust account for a payment to leave from, as test set-up."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        bank = await ledger.open_account(
            session,
            AccountSpec("bank.trust", AccountKind.ASSET, Currency.UGX, "Trust bank account"),
            PRESENT,
        )
        source = await ledger.open_account(
            session,
            AccountSpec("test.funding", AccountKind.LIABILITY, Currency.UGX, "Test funding"),
            PRESENT,
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.funding",
                effective_date=LAST_MONTH,
                lines=(debit(bank.id, ugx(amount)), credit(source.id, ugx(amount))),
                idempotency_key=key(),
            ),
            PRESENT,
        )


async def report(database: Database, month: date) -> withholding.MonthReport:
    async with database.actor_transaction(new_staff()) as session:
        return await withholding.month_report(session, SEALER, Currency.UGX, month)


async def summary(database: Database, month: date) -> MonthSummary:
    async with database.actor_transaction(new_staff()) as session:
        return await remittances.month_summary(session, Currency.UGX, month)


async def propose(
    database: Database,
    staff: Actor,
    amount: int,
    *,
    month: date = LAST_MONTH,
    paid_on: date | None = None,
    clock: FakeClock = PRESENT,
) -> remittances.RemittanceView:
    async with database.actor_transaction(staff) as session:
        return await remittances.propose(
            session,
            staff,
            month=month,
            amount=ugx(amount),
            ura_reference=f"PRN{push_id()}",
            bank_reference=f"FT{push_id()}",
            paid_on=paid_on or today(),
            note="Withholding for the month",
            clock=clock,
        )


async def approve(
    database: Database, staff: Actor, remittance_id: str
) -> remittances.RemittanceView:
    async with database.actor_transaction(staff) as session:
        return await remittances.approve(session, staff, remittance_id, PRESENT)


def refusal(error: pytest.ExceptionInfo[ApplicationError]) -> str:
    return error.value.fields[0].code


# The month's return -----------------------------------------------------------------------------


async def test_the_daily_run_lands_on_the_months_return_with_name_number_and_tin(
    app_database: Database, worker_database: Database
) -> None:
    amara = await approved_client(app_database, tin=TIN)
    first = await running(app_database, worker_database, amara)
    second = await running(app_database, worker_database, amara, amount=2_000_000)
    run_date = today() + timedelta(days=3)
    for investment in (first, second):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await investments.accrue_investment(session, investment.id, run_date, at(run_date))
    month = month_of(run_date)
    found = await report(app_database, month)
    async with worker_database.actor_transaction(SYSTEM) as session:
        posted = await investments.get_investments(session, [first.id, second.id])
    gross = sum(i.accrued_gross.minor for i in posted)
    tax = sum(i.accrued_tax.minor for i in posted)
    assert tax > 0
    (line,) = [p for p in found.payees if p.client_id == amara.id]
    assert (line.gross, line.tax, line.tin, line.name) == (
        ugx(gross),
        ugx(tax),
        TIN,
        "NAKATO Amara",
    )
    assert line.account_number is not None and len(line.account_number) == 10
    assert found.tins_shown
    assert found.withheld == sum((p.tax for p in found.payees), ugx(0))


async def test_a_client_without_a_tin_is_on_the_return_without_one(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database)
    investment = await running(app_database, worker_database, client)
    await withhold(worker_database, investment, LAST_MONTH, 1_000, 150)
    (line,) = [
        p for p in (await report(app_database, LAST_MONTH)).payees if p.client_id == client.id
    ]
    assert (line.gross, line.tax, line.tin) == (ugx(1_000), ugx(150), None)
    async with app_database.actor_transaction(new_staff()) as session:
        without_key = await withholding.month_report(session, None, Currency.UGX, LAST_MONTH)
    assert not without_key.tins_shown


async def test_a_month_summary_counts_what_was_withheld_by_the_entries_dates(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database)
    investment = await running(app_database, worker_database, client)
    before = await summary(app_database, LAST_MONTH)
    await withhold(worker_database, investment, month_end(LAST_MONTH), 2_000, 300)
    after = await summary(app_database, LAST_MONTH)
    assert after.withheld - before.withheld == ugx(300)
    assert after.outstanding - before.outstanding == ugx(300)


# The certificate --------------------------------------------------------------------------------


async def test_a_certificate_has_each_investment_that_earned_in_the_tax_year(
    app_database: Database, worker_database: Database
) -> None:
    amara = await approved_client(app_database, tin=TIN)
    first = await running(app_database, worker_database, amara)
    second = await running(app_database, worker_database, amara, amount=500_000)
    year = tax_year_of(LAST_MONTH)
    inside = max(LAST_MONTH, year.first_day)
    await withhold(worker_database, first, inside, 31_164, 4_674)
    await withhold(worker_database, second, inside, 1_000, 150)
    await withhold(worker_database, second, inside, -100, -15)
    # The year before is not on it.
    await withhold(worker_database, first, year.first_day - timedelta(days=1), 9_999, 1_499)
    async with worker_database.actor_transaction(SYSTEM) as session:
        made = await service.certificate_figures(session, SEALER, str(amara.id), year)
    assert (made.holder_name, made.tin, made.tax_year) == ("Amara NAKATO", TIN, year)
    assert {(line.investment_id, line.gross, line.tax, line.net) for line in made.lines} == {
        (first.id, ugx(31_164), ugx(4_674), ugx(26_490)),
        (second.id, ugx(900), ugx(135), ugx(765)),
    }
    assert [(t.gross, t.tax, t.net) for t in made.totals] == [
        (ugx(32_064), ugx(4_809), ugx(27_255))
    ]


async def test_no_certificate_for_a_client_who_is_not_approved(worker_database: Database) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(service.NoAccount):
            await service.certificate_figures(session, SEALER, push_id(), tax_year_of(today()))


# Paying URA -------------------------------------------------------------------------------------


async def withheld_last_month(
    app_database: Database, worker_database: Database, tax: int
) -> MonthSummary:
    client = await approved_client(app_database)
    investment = await running(app_database, worker_database, client)
    await withhold(worker_database, investment, LAST_MONTH, tax * 10, tax)
    await fund_trust(worker_database, tax)
    return await summary(app_database, LAST_MONTH)


async def held(database: Database) -> Money:
    async with database.actor_transaction(new_staff()) as session:
        return await remittances.held_for_ura(session, Currency.UGX)


async def test_a_payment_to_ura_posts_once_a_second_person_approves(
    app_database: Database, worker_database: Database
) -> None:
    await withheld_last_month(app_database, worker_database, 5_000)
    held_before = await held(app_database)
    before = await summary(app_database, LAST_MONTH)
    daniel, grace = new_staff(), new_staff()
    recorded = await propose(app_database, daniel, 5_000)
    assert recorded.status is RemittanceStatus.PROPOSED
    assert (await summary(app_database, LAST_MONTH)).waiting - before.waiting == ugx(5_000)
    assert await held(app_database) == held_before
    with pytest.raises(ApplicationError) as same:
        await approve(app_database, daniel, recorded.id)
    assert same.value.code == "forbidden"

    approved = await approve(app_database, grace, recorded.id)
    assert (approved.status, approved.decided_by) == (RemittanceStatus.APPROVED, grace.id)
    assert approved.ledger_entry_id is not None
    assert held_before - await held(app_database) == ugx(5_000)
    after = await summary(app_database, LAST_MONTH)
    assert after.remitted - before.remitted == ugx(5_000)
    assert after.outstanding == before.outstanding - ugx(5_000)
    with pytest.raises(ApplicationError) as again:
        await approve(app_database, new_staff(), recorded.id)
    assert again.value.code == "conflict"
    async with app_database.actor_transaction(new_staff()) as session:
        entry = await ledger.get_entry(session, approved.ledger_entry_id)
    assert (entry.source_type, entry.source_id, entry.effective_date) == (
        "tax.remittance",
        recorded.id,
        today(),
    )


async def test_a_payment_is_refused_for_a_month_not_ended_or_more_than_it_owes(
    app_database: Database, worker_database: Database
) -> None:
    owed = await withheld_last_month(app_database, worker_database, 700)
    staff = new_staff()
    with pytest.raises(ApplicationError) as too_much:
        await propose(app_database, staff, owed.outstanding.minor + 1)
    assert refusal(too_much) == "more_than_outstanding"
    with pytest.raises(ApplicationError) as this_month:
        await propose(app_database, staff, 1, month=month_of(today()))
    assert refusal(this_month) == "month_not_ended"
    recorded = await propose(app_database, staff, 1)
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as twice:
            await remittances.propose(
                session,
                staff,
                month=LAST_MONTH,
                amount=ugx(1),
                ura_reference=recorded.ura_reference,
                bank_reference=f"FT{push_id()}",
                paid_on=today(),
                note="Again",
                clock=PRESENT,
            )
    assert refusal(twice) == "already_recorded"


async def test_two_people_recording_at_once_never_pay_more_than_the_month_owes(
    app_database: Database, worker_database: Database
) -> None:
    owed = await withheld_last_month(app_database, worker_database, 900)
    amount = owed.outstanding.minor
    assert amount > 0
    barrier = AsyncBarrier(2)

    async def record() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await remittances.propose(
                    session,
                    staff,
                    month=LAST_MONTH,
                    amount=ugx(amount),
                    ura_reference=f"PRN{push_id()}",
                    bank_reference=f"FT{push_id()}",
                    paid_on=today(),
                    note="Withholding",
                    clock=PRESENT,
                )
        except ApplicationError as error:
            return error.fields[0].code
        return "recorded"

    results = await asyncio.gather(record(), record())
    assert sorted(results) == ["more_than_outstanding", "recorded"]
    assert (await summary(app_database, LAST_MONTH)).outstanding == ugx(0)


async def test_a_decline_posts_nothing_and_frees_the_amount(
    app_database: Database, worker_database: Database
) -> None:
    owed = await withheld_last_month(app_database, worker_database, 400)
    recorded = await propose(app_database, new_staff(), owed.outstanding.minor)
    grace = new_staff()
    with pytest.raises(ApplicationError) as no_reason:
        async with app_database.actor_transaction(grace) as session:
            await remittances.decline(session, grace, recorded.id, " ", PRESENT)
    assert refusal(no_reason) == "reason_required"
    async with app_database.actor_transaction(grace) as session:
        declined = await remittances.decline(
            session, grace, recorded.id, "PRN not on URA's receipt", PRESENT
        )
    assert (declined.status, declined.ledger_entry_id) == (RemittanceStatus.DECLINED, None)
    assert (await summary(app_database, LAST_MONTH)).outstanding == owed.outstanding
    # The references can be recorded again once declined.
    async with app_database.actor_transaction(grace) as session:
        await remittances.propose(
            session,
            grace,
            month=LAST_MONTH,
            amount=ugx(1),
            ura_reference=recorded.ura_reference,
            bank_reference=recorded.bank_reference,
            paid_on=today(),
            note="Recorded again",
            clock=PRESENT,
        )


async def test_the_database_refuses_one_person_a_changed_payment_and_a_made_up_entry(
    app_database: Database, worker_database: Database
) -> None:
    await withheld_last_month(app_database, worker_database, 800)
    daniel = new_staff()
    recorded = await propose(app_database, daniel, 300)

    async def as_staff(staff: Actor, statement: str, values: dict[str, object]) -> None:
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text(statement), values)

    approve_sql = (
        "UPDATE tax_remittances SET status = 'approved', decided_by = :by, "
        "decided_at = now(), ledger_entry_id = :entry WHERE id = :id"
    )
    with pytest.raises(DBAPIError, match="ck_tax_remittances_two_people"):
        await as_staff(
            daniel,
            "UPDATE tax_remittances SET status = 'declined', decided_by = :by, "
            "decided_at = now(), decision_reason = 'Mine' WHERE id = :id",
            {"by": daniel.id, "id": recorded.id},
        )
    grace = new_staff()
    with pytest.raises(DBAPIError, match="decided by the person signed in"):
        await as_staff(grace, approve_sql, {"by": push_id(), "entry": None, "id": recorded.id})
    other = await approve(app_database, new_staff(), (await propose(app_database, daniel, 1)).id)
    with pytest.raises(DBAPIError, match="names the entry it posted"):
        await as_staff(
            grace, approve_sql, {"by": grace.id, "entry": other.ledger_entry_id, "id": recorded.id}
        )
    with pytest.raises(DBAPIError):
        # The amount is not even granted.
        await as_staff(
            grace, "UPDATE tax_remittances SET amount_minor = 1 WHERE id = :id", {"id": recorded.id}
        )
    with pytest.raises(DBAPIError, match="once"):
        await as_staff(
            grace,
            "UPDATE tax_remittances SET decision_reason = 'changed' WHERE id = :id",
            {"id": other.id},
        )
    with pytest.raises(DBAPIError, match="proposed by the person signed in"):
        await as_staff(
            grace,
            "INSERT INTO tax_remittances (id, month, currency, amount_minor, ura_reference, "
            "bank_reference, paid_on, note, status, proposed_by, proposed_at) VALUES (:id, "
            ":month, 'UGX', 1, :ura, 'FT1', :paid, 'x', 'proposed', :by, now())",
            {
                "id": push_id(),
                "month": LAST_MONTH,
                "ura": push_id(),
                "paid": today(),
                "by": daniel.id,
            },
        )
    with pytest.raises(DBAPIError):
        await as_staff(grace, "DELETE FROM tax_remittances WHERE id = :id", {"id": recorded.id})
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE tax_remittances SET status = 'declined' WHERE id = :id"),
                {"id": recorded.id},
            )
