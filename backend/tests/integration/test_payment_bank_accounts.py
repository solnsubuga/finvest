"""The bank account clients pay into by transfer, set by two people, against real PostgreSQL."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.payments import bank_accounts
from app.modules.payments.domain.bank_accounts import BankAccount
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

TRUST = BankAccount("Example Bank Uganda", "Finvest Clients Trust Account", "9030001234567", None)
NEW = BankAccount("Other Bank Uganda", "Finvest Clients Trust Account", "1100223344", "Kampala")


async def clear(migrator_database: Database) -> None:
    # The account is shared by every test, so each test here starts with none set.
    async with migrator_database.transaction() as session:
        await session.execute(text("TRUNCATE payment_bank_accounts"))


async def propose(
    database: Database,
    staff: Actor,
    clock: FakeClock,
    account: BankAccount = NEW,
    currency: Currency = Currency.UGX,
) -> bank_accounts.Proposal:
    async with database.actor_transaction(staff) as session:
        return await bank_accounts.propose(
            session,
            staff,
            currency,
            bank_name=account.bank_name,
            account_name=account.account_name,
            account_number=account.account_number,
            branch=account.branch,
            reason="The trust account moved banks",
            clock=clock,
        )


async def decide(
    database: Database, staff: Actor, proposal_id: str, decision: str, clock: FakeClock
) -> None:
    async with database.actor_transaction(staff) as session:
        await bank_accounts.decide(
            session,
            staff,
            proposal_id,
            decision,  # type: ignore[arg-type]
            "Checked against the bank's letter",
            clock,
        )


async def in_use(database: Database, actor: Actor, currency: Currency) -> BankAccount | None:
    async with database.actor_transaction(actor) as session:
        return await bank_accounts.account_in_use(session, currency)


async def test_new_details_are_used_only_after_a_second_person_approves(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear(migrator_database)
    maker, checker, client = new_staff(), new_staff(), new_client()
    first = await propose(app_database, maker, fake_clock, TRUST)
    assert await in_use(app_database, client, Currency.UGX) is None
    await decide(app_database, checker, first.id, "approved", fake_clock)
    assert await in_use(app_database, client, Currency.UGX) == TRUST

    fake_clock.advance(minutes=5)
    second = await propose(app_database, checker, fake_clock)
    # While the change waits, clients are still shown the account in use.
    assert await in_use(app_database, client, Currency.UGX) == TRUST
    async with app_database.actor_transaction(maker) as session:
        listed = {item.currency: item for item in await bank_accounts.list_accounts(session)}
    assert listed[Currency.UGX].in_use == TRUST
    waiting = listed[Currency.UGX].proposal
    assert waiting is not None and waiting.account == NEW
    assert listed[Currency.USD].in_use is None and listed[Currency.USD].proposal is None

    fake_clock.advance(minutes=5)
    await decide(app_database, maker, second.id, "approved", fake_clock)
    assert await in_use(app_database, client, Currency.UGX) == NEW
    assert await in_use(app_database, client, Currency.USD) is None

    async with app_database.actor_transaction(maker) as session:
        trail = await events_for_resource(session, bank_accounts.RESOURCE, second.id)
    assert [(e.action, e.category, e.actor_id) for e in reversed(trail)] == [
        ("payments.bank_account_proposed", "change", checker.id),
        ("payments.bank_account_approved", "approval", maker.id),
    ]
    # The details are on the row, which never changes. The trail names only the currency.
    assert all(e.details == {"currency": "UGX"} for e in trail)


async def test_nobody_approves_their_own_details_even_by_writing_the_row(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear(migrator_database)
    maker = new_staff()
    proposal = await propose(app_database, maker, fake_clock)
    for decision in ("approved", "rejected"):
        with pytest.raises(ApplicationError) as raised:
            await decide(app_database, maker, proposal.id, decision, fake_clock)
        assert raised.value.code == "forbidden"
    with pytest.raises(DBAPIError, match="ck_payment_bank_accounts_two_people"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE payment_bank_accounts SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'mine' WHERE id = :id"
                ),
                {"staff": maker.id, "id": proposal.id},
            )
    # Naming a colleague as the approver from your own transaction is refused as well.
    with pytest.raises(DBAPIError, match="decided once, by the acting member of staff"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE payment_bank_accounts SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'theirs' WHERE id = :id"
                ),
                {"staff": new_staff().id, "id": proposal.id},
            )
    # Nor can the details be changed under the approver's eyes.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text("UPDATE payment_bank_accounts SET account_number = '999999' WHERE id = :id"),
                {"id": proposal.id},
            )
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, new_staff(), proposal.id, "withdrawn", fake_clock)
    assert raised.value.code == "forbidden"
    await decide(app_database, maker, proposal.id, "withdrawn", fake_clock)
    assert await in_use(app_database, maker, Currency.UGX) is None
    async with app_database.actor_transaction(maker) as session:
        trail = await events_for_resource(session, bank_accounts.RESOURCE, proposal.id)
    assert trail[0].action == "payments.bank_account_withdrawn" and trail[0].category == "change"


async def test_one_change_waits_per_currency_and_a_decision_is_final(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear(migrator_database)
    maker, checker = new_staff(), new_staff()
    proposal = await propose(app_database, maker, fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, checker, fake_clock, TRUST)
    assert raised.value.code == "conflict"
    dollars = await propose(app_database, checker, fake_clock, currency=Currency.USD)
    await decide(app_database, maker, dollars.id, "rejected", fake_clock)
    await decide(app_database, checker, proposal.id, "rejected", fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, checker, proposal.id, "approved", fake_clock)
    assert raised.value.code == "not_found"
    for statement in (
        "UPDATE payment_bank_accounts SET status = 'approved' WHERE id = :id",
        "UPDATE payment_bank_accounts SET decision_reason = 'changed my mind' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="a decided bank account never changes"):
            async with app_database.actor_transaction(new_staff()) as session:
                await session.execute(text(statement), {"id": proposal.id})
    assert await in_use(app_database, maker, Currency.UGX) is None
    assert await in_use(app_database, maker, Currency.USD) is None
    # A rejected change frees the currency for the next proposal.
    await propose(app_database, maker, fake_clock)


async def test_two_approvals_racing_decide_once(
    app_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
    concurrency_barrier: AsyncBarrier,
) -> None:
    await clear(migrator_database)
    proposal = await propose(app_database, new_staff(), fake_clock)

    async def approve() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await concurrency_barrier.wait()
                await bank_accounts.decide(
                    session, staff, proposal.id, "approved", "Agreed", fake_clock
                )
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(approve(), approve())) == ["approved", "not_found"]
    async with app_database.actor_transaction(new_staff()) as session:
        trail = await events_for_resource(session, bank_accounts.RESOURCE, proposal.id)
    assert [event.action for event in trail].count("payments.bank_account_approved") == 1


async def test_two_proposals_racing_for_one_currency_leave_one_waiting(
    app_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
    concurrency_barrier: AsyncBarrier,
) -> None:
    await clear(migrator_database)

    async def put_forward(account: BankAccount) -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await concurrency_barrier.wait()
                await bank_accounts.propose(
                    session,
                    staff,
                    Currency.UGX,
                    bank_name=account.bank_name,
                    account_name=account.account_name,
                    account_number=account.account_number,
                    branch=account.branch,
                    reason="New account",
                    clock=fake_clock,
                )
        except ApplicationError as error:
            return error.code
        return "proposed"

    assert sorted(await asyncio.gather(put_forward(TRUST), put_forward(NEW))) == [
        "conflict",
        "proposed",
    ]


async def test_bad_details_and_missing_reasons_are_refused(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear(migrator_database)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as raised:
            await bank_accounts.propose(
                session,
                staff,
                Currency.UGX,
                bank_name=" ",
                account_name="Finvest",
                account_number="12-34",
                branch=None,
                reason="Why",
                clock=fake_clock,
            )
        assert [(problem.field_path, problem.code) for problem in raised.value.fields] == [
            (["bank_name"], "required"),
            (["account_number"], "invalid"),
        ]
        with pytest.raises(ApplicationError) as raised:
            await bank_accounts.propose(
                session,
                staff,
                Currency.UGX,
                bank_name="Bank",
                account_name="Finvest",
                account_number="1234567",
                branch=None,
                reason="  ",
                clock=fake_clock,
            )
        assert raised.value.fields[0].field_path == ["reason"]
        assert all(item.proposal is None for item in await bank_accounts.list_accounts(session))


async def test_only_staff_set_the_account_and_clients_see_only_the_approved_one(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
) -> None:
    await clear(migrator_database)
    client = new_client()
    waiting = await propose(app_database, new_staff(), fake_clock)
    with pytest.raises(ApplicationError):
        await propose(app_database, client, fake_clock, currency=Currency.USD)
    async with app_database.actor_transaction(client) as session:
        visible = await session.scalar(text("SELECT count(*) FROM payment_bank_accounts"))
    assert visible == 0
    insert = text(
        "INSERT INTO payment_bank_accounts (id, currency, bank_name, account_name, "
        "account_number, reason, proposed_by, proposed_at) VALUES (:id, 'USD', 'Bank', "
        "'Finvest', '1234567', 'why', :staff, now())"
    )
    values = {"id": push_id(), "staff": push_id()}
    with pytest.raises(DBAPIError, match="only staff change the bank account"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(insert, values)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(insert, values)
    # A member of staff cannot record a proposal in a colleague's name.
    with pytest.raises(DBAPIError, match="starts as a proposal by the acting member of staff"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(insert, values)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text("DELETE FROM payment_bank_accounts WHERE id = :id"), {"id": waiting.id}
            )

    await decide(app_database, new_staff(), waiting.id, "approved", fake_clock)
    await propose(app_database, new_staff(), fake_clock, TRUST)
    async with app_database.actor_transaction(client) as session:
        rows = (await session.execute(text("SELECT status FROM payment_bank_accounts"))).all()
    # The client reads the account in use, never what is waiting or who proposed it.
    assert [row.status for row in rows] == ["approved"]
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await bank_accounts.account_in_use(session, Currency.UGX) == NEW
