"""Transfers between pots: two people, one entry, and what the database refuses."""

import asyncio
import secrets
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.ledger import closing
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.reconciliation import transfers
from app.modules.reconciliation.domain import transfers as rules
from app.modules.reconciliation.domain.statements import StatementSource
from app.modules.reconciliation.domain.transfers import TransferError, TransferStatus
from app.shared.actors import SYSTEM
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from tests.support.clock import FakeClock
from tests.support.ledger import client, key, staff, ugx

pytestmark = pytest.mark.integration

MTN = (StatementSource.IOTEC, "mtn")
BANK = (StatementSource.BANK, "trust")
DAY = date(2026, 11, 12)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 13, 9, 0, tzinfo=UTC))


def reference() -> str:
    return f"IOT-{secrets.token_hex(6)}"


async def propose(
    database: Database,
    clock: FakeClock,
    *,
    sent: int = 1_000_000,
    received: int = 990_000,
    provider_reference: str | None = None,
) -> transfers.TransferView:
    proposer = staff()
    async with database.actor_transaction(proposer) as session:
        return await transfers.propose(
            session,
            proposer,
            from_pot=MTN,
            to_pot=BANK,
            sent=ugx(sent),
            received=ugx(received),
            value_date=DAY,
            provider_reference=provider_reference or reference(),
            reason="Daily sweep of collected mobile money",
            idempotency_key=key(),
            clock=clock,
        )


async def fund_wallet(database: Database, clock: FakeClock, amount: int) -> None:
    """Money collected into the MTN wallet, owed to a test account, posted as the worker."""
    async with database.actor_transaction(SYSTEM) as session:
        wallet = await ledger.open_account(session, rules.find_pot(*MTN).ledger_account, clock)
        owed = await ledger.open_account(
            session, AccountSpec("test.owed", AccountKind.LIABILITY, Currency.UGX, "Owed"), clock
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.collected",
                effective_date=DAY,
                lines=(debit(wallet.id, ugx(amount)), credit(owed.id, ugx(amount))),
                idempotency_key=key(),
            ),
            clock,
        )


async def entry_lines(database: Database, entry_id: str) -> dict[str, int]:
    reader = staff()
    async with database.actor_transaction(reader) as session:
        rows = await session.execute(
            text(
                "SELECT account.code, line.amount_minor FROM ledger_lines AS line "
                "JOIN ledger_accounts AS account ON account.id = line.account_id "
                "WHERE line.entry_id = :id"
            ),
            {"id": entry_id},
        )
        return {row.code: int(row.amount_minor) for row in rows}


async def test_a_sweep_is_posted_only_when_a_second_person_approves(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    await fund_wallet(worker_database, fake_clock, 1_000_000)
    proposed = await propose(app_database, fake_clock)
    assert (proposed.status, proposed.ledger_entry_id) == (TransferStatus.PROPOSED, None)
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        assert proposed.id in [item.id for item in await transfers.waiting(session)]
        fees = await ledger.open_account(session, rules.fee_account(Currency.UGX), fake_clock)
        fees_before = (await ledger.get_balance(session, fees.id)).minor

    proposer = staff()
    async with app_database.actor_transaction(proposer) as session:
        assert (await transfers.get_transfer(session, proposed.id)).proposed_by != proposer.id
    async with app_database.actor_transaction(checker) as session:
        approved = await transfers.approve(
            session, checker, proposed.id, idempotency_key=key(), clock=fake_clock
        )
        assert (await ledger.get_balance(session, fees.id)).minor == fees_before + 10_000

    assert (approved.status, approved.decided_by) == (TransferStatus.APPROVED, checker.id)
    assert approved.ledger_entry_id is not None
    assert await entry_lines(app_database, approved.ledger_entry_id) == {
        "bank.trust": 990_000,
        "fees.provider.iotec": 10_000,
        "iotec.wallet": -1_000_000,
    }
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(TransferError) as twice:
            await transfers.approve(
                session, checker, proposed.id, idempotency_key=key(), clock=fake_clock
            )
        assert twice.value.code == "transfer_already_decided"
        actions = (
            await session.execute(
                text("SELECT action FROM audit_events WHERE resource_id = :id ORDER BY id"),
                {"id": proposed.id},
            )
        ).scalars()
        assert list(actions) == [
            "reconciliation.transfer_proposed",
            "reconciliation.transfer_approved",
        ]


async def test_the_proposer_cannot_approve_and_a_decline_posts_nothing(
    app_database: Database, fake_clock: FakeClock
) -> None:
    proposer = staff()
    async with app_database.actor_transaction(proposer) as session:
        proposed = await transfers.propose(
            session,
            proposer,
            from_pot=MTN,
            to_pot=BANK,
            sent=ugx(500),
            received=ugx(500),
            value_date=DAY,
            provider_reference=reference(),
            reason="Sweep",
            idempotency_key=key(),
            clock=fake_clock,
        )
        with pytest.raises(TransferError) as same:
            await transfers.approve(
                session, proposer, proposed.id, idempotency_key=key(), clock=fake_clock
            )
        assert same.value.code == "transfer_needs_second_person"

    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(ApplicationError):
            await transfers.decline(
                session, checker, proposed.id, " ", idempotency_key=key(), clock=fake_clock
            )
        declined = await transfers.decline(
            session,
            checker,
            proposed.id,
            "Not on the bank statement",
            idempotency_key=key(),
            clock=fake_clock,
        )
    assert (declined.status, declined.ledger_entry_id) == (TransferStatus.DECLINED, None)

    # Once declined, the same provider reference can be recorded again correctly.
    again = await propose(app_database, fake_clock, provider_reference=proposed.provider_reference)
    assert again.status is TransferStatus.PROPOSED


async def test_a_sweep_is_recorded_once_and_only_by_staff_for_a_day_that_has_come(
    app_database: Database, fake_clock: FakeClock
) -> None:
    shared = reference()
    await propose(app_database, fake_clock, provider_reference=shared)
    with pytest.raises(TransferError) as repeated:
        await propose(app_database, fake_clock, provider_reference=shared)
    assert repeated.value.code == "transfer_already_recorded"

    outsider = client()
    async with app_database.actor_transaction(outsider) as session:
        with pytest.raises(ApplicationError):
            await transfers.propose(
                session,
                outsider,
                from_pot=MTN,
                to_pot=BANK,
                sent=ugx(1),
                received=ugx(1),
                value_date=DAY,
                provider_reference=reference(),
                reason="x",
                idempotency_key=key(),
                clock=fake_clock,
            )
    proposer = staff()
    async with app_database.actor_transaction(proposer) as session:
        # Integration tests only close days in 2001, so close its first day if nothing is closed.
        closed_day = (
            await closing.closed_through(session)
            or (
                await closing.close_through(session, proposer, date(2001, 1, 1), fake_clock)
            ).closed_through
        )
        with pytest.raises(TransferError) as future:
            await transfers.propose(
                session,
                proposer,
                from_pot=MTN,
                to_pot=BANK,
                sent=ugx(1),
                received=ugx(1),
                value_date=date(2026, 11, 14),
                provider_reference=reference(),
                reason="x",
                idempotency_key=key(),
                clock=fake_clock,
            )
        assert future.value.code == "transfer_in_the_future"
        with pytest.raises(TransferError) as closed:
            await transfers.propose(
                session,
                proposer,
                from_pot=MTN,
                to_pot=BANK,
                sent=ugx(1),
                received=ugx(1),
                value_date=closed_day,
                provider_reference=reference(),
                reason="x",
                idempotency_key=key(),
                clock=fake_clock,
            )
        assert closed.value.code == "transfer_in_a_closed_day"


async def test_two_approvals_at_once_post_once(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    await fund_wallet(worker_database, fake_clock, 1_000_000)
    proposed = await propose(app_database, fake_clock)

    async def attempt() -> str:
        checker = staff()
        try:
            async with app_database.actor_transaction(checker) as session:
                await transfers.approve(
                    session, checker, proposed.id, idempotency_key=key(), clock=fake_clock
                )
        except TransferError as refused:
            return refused.code
        return "approved"

    assert sorted(await asyncio.gather(attempt(), attempt())) == [
        "approved",
        "transfer_already_decided",
    ]


async def test_the_database_refuses_one_person_twice_changed_terms_and_deletes(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    proposed = await propose(app_database, fake_clock)
    decline = text(
        "UPDATE reconciliation_pot_transfers SET status = 'declined', decided_by = :by, "
        "decided_at = now(), decision_reason = 'no' WHERE id = :id"
    )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(decline, {"id": proposed.id, "by": proposed.proposed_by})
    other = staff()
    with pytest.raises(DBAPIError, match="signed-in member of staff"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(decline, {"id": proposed.id, "by": staff().id})
    for change in (
        "UPDATE reconciliation_pot_transfers SET sent_minor = 5 WHERE id = :id",
        "DELETE FROM reconciliation_pot_transfers WHERE id = :id",
        "UPDATE reconciliation_pot_transfers SET status = 'approved', decided_by = "
        "finvest_actor_id(), decided_at = now() WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(other) as session:
                await session.execute(text(change), {"id": proposed.id})
    async with app_database.actor_transaction(other) as session:
        await session.execute(decline, {"id": proposed.id, "by": other.id})
    with pytest.raises(DBAPIError, match="approved or declined, once"):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(
                text(
                    "UPDATE reconciliation_pot_transfers SET status = 'proposed', "
                    "decided_by = NULL, decided_at = NULL WHERE id = :id"
                ),
                {"id": proposed.id},
            )
    read = text("SELECT count(*) FROM reconciliation_pot_transfers WHERE id = :id")
    async with app_database.actor_transaction(client()) as session:
        assert await session.scalar(read, {"id": proposed.id}) == 0
    async with worker_database.transaction() as session:
        assert await session.scalar(read, {"id": proposed.id}) == 1


async def test_a_sweep_larger_than_the_books_hold_is_not_posted(
    app_database: Database, fake_clock: FakeClock
) -> None:
    proposed = await propose(app_database, fake_clock, sent=10**15, received=10**15)
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(TransferError) as refused:
            await transfers.approve(
                session, checker, proposed.id, idempotency_key=key(), clock=fake_clock
            )
        assert refused.value.code == "transfer_more_than_the_pot_holds"
        assert (await transfers.get_transfer(session, proposed.id)).status is (
            TransferStatus.PROPOSED
        )
