"""Corrections need two people, and PostgreSQL holds them to it."""

import asyncio
import json
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.ledger import corrections
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.corrections import CorrectionProposal
from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    InvalidEntry,
    Line,
    credit,
    debit,
)
from app.modules.ledger.domain.errors import (
    AlreadyReversed,
    CorrectionAlreadyDecided,
    InsufficientFunds,
    SecondPersonRequired,
    UnknownCorrection,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TEST_SOURCE
from tests.support.ledger import DAY, Books, client, deposit, key, open_books, payout, staff, ugx

pytestmark = pytest.mark.integration


class Mistake:
    """A deposit of 1,000 credited to the wrong client, who has since been paid 400 of it."""

    def __init__(self, books: Books, receivable: str, deposit_entry: str) -> None:
        self.books = books
        self.receivable = receivable
        self.deposit_entry = deposit_entry

    def lines(self) -> tuple[Line, ...]:
        # Take back the 600 still held, book the 400 already paid out as owed by the client,
        # and bring the collections account down by the 1,000 that was never theirs.
        return (
            debit(self.books.holding, ugx(600)),
            debit(self.receivable, ugx(400)),
            credit(self.books.cash, ugx(1_000)),
        )


async def make_mistake(database: Database, clock: FakeClock) -> Mistake:
    async with database.actor_transaction(staff()) as session:
        books = await open_books(session, clock)
        # Other clients' money, so the collections account can fund the payout.
        other = await open_books(session, clock)
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.deposit",
                effective_date=DAY,
                lines=(debit(books.cash, ugx(5_000)), credit(other.holding, ugx(5_000))),
                idempotency_key=key(),
                source_type=TEST_SOURCE,
                source_id=push_id(),
            ),
            clock,
        )
        wrong = await ledger.post_entry(session, deposit(books, 1_000), clock)
        await ledger.post_entry(session, payout(books, 400), clock)
        receivable = await ledger.open_account(
            session,
            AccountSpec(
                "test.receivables.deposit_correction",
                AccountKind.ASSET,
                Currency.UGX,
                "Owed by client after a deposit correction",
                owner_client_id=books.owner.id,
                reference=push_id(),
            ),
            clock,
        )
    return Mistake(books, receivable.id, wrong.id)


async def propose(
    database: Database, clock: FakeClock, mistake: Mistake, maker: Actor
) -> CorrectionProposal:
    async with database.actor_transaction(maker) as session:
        return await corrections.propose_correction(
            session,
            maker,
            clock,
            corrects_entry_id=mistake.deposit_entry,
            entry_type="test.deposit_correction",
            effective_date=DAY,
            lines=mistake.lines(),
            reason="Deposit belonged to another client",
        )


async def state(database: Database, mistake: Mistake) -> tuple[int, int, int]:
    """Collections, the client's holding and what the client owes."""
    async with database.actor_transaction(staff()) as session:
        return (
            (await ledger.get_balance(session, mistake.books.cash)).minor,
            (await ledger.get_balance(session, mistake.books.holding)).minor,
            (await ledger.get_balance(session, mistake.receivable)).minor,
        )


BEFORE = (5_600, 600, 0)
AFTER = (4_600, 0, 400)


async def test_a_mistaken_credit_that_has_moved_on_is_corrected_by_two_people(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    maker, checker = staff(), staff()
    assert await state(app_database, mistake) == BEFORE

    # The plain reversal, which only the worker may post, is refused: 400 of the money has gone.
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(InsufficientFunds):
            await ledger.reverse_entry(
                session,
                mistake.deposit_entry,
                fake_clock,
                effective_date=DAY,
                idempotency_key=key(),
            )

    proposal = await propose(app_database, fake_clock, mistake, maker)
    assert await state(app_database, mistake) == BEFORE  # proposing posts nothing
    async with app_database.actor_transaction(checker) as session:
        assert [waiting.id for waiting in await corrections.pending_corrections(session)].count(
            proposal.id
        ) == 1
        assert await corrections.get_decision(session, proposal.id) is None

    fake_clock.advance(hours=1)
    async with app_database.actor_transaction(checker) as session:
        posted = await corrections.approve_correction(
            session, checker, proposal.id, fake_clock, reason="Checked against the statement"
        )
    assert await state(app_database, mistake) == AFTER
    assert posted.entry_type == "test.deposit_correction"
    assert (posted.source_type, posted.source_id) == ("ledger.correction", proposal.id)

    async with app_database.actor_transaction(checker) as session:
        decision = await corrections.get_decision(session, proposal.id)
        assert decision is not None
        assert (decision.outcome, decision.decided_by, decision.entry_id) == (
            "approved",
            checker.id,
            posted.id,
        )
        assert decision.decided_at == fake_clock.now()
        waiting = await corrections.pending_corrections(session, limit=200)
        assert proposal.id not in [item.id for item in waiting]
        # The original entry is untouched, and the trail names both people and their reasons.
        original = await ledger.get_entry(session, mistake.deposit_entry)
        assert [line.amount.minor for line in original.lines] == [1_000, -1_000]
        events = await audit.events_for_resource(session, "ledger_correction", proposal.id)
        assert await ledger.find_balance_drift(session) == []
    assert [(event.action, event.actor_id, event.reason) for event in reversed(events)] == [
        ("ledger.correction_proposed", maker.id, "Deposit belonged to another client"),
        ("ledger.correction_approved", checker.id, "Checked against the statement"),
    ]


async def test_the_proposer_cannot_decide_their_own_correction(
    app_database: Database, fake_clock: FakeClock
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    maker = staff()
    proposal = await propose(app_database, fake_clock, mistake, maker)
    async with app_database.actor_transaction(maker) as session:
        with pytest.raises(SecondPersonRequired):
            await corrections.approve_correction(session, maker, proposal.id, fake_clock)
        with pytest.raises(SecondPersonRequired):
            await corrections.reject_correction(session, maker, proposal.id, "No", fake_clock)
    # The database refuses it too, whatever the service does.
    for outcome in ("approved", "rejected"):
        with pytest.raises(DBAPIError, match="decided by a second person"):
            async with app_database.actor_transaction(maker) as session:
                await session.execute(
                    text(
                        "INSERT INTO ledger_correction_decisions (proposal_id, outcome, "
                        "decided_by, decided_at, reason) VALUES (:id, :outcome, :me, now(), 'x')"
                    ),
                    {"id": proposal.id, "outcome": outcome, "me": maker.id},
                )
    assert await state(app_database, mistake) == BEFORE
    async with app_database.actor_transaction(maker) as session:
        assert await corrections.get_decision(session, proposal.id) is None


async def test_a_rejected_correction_posts_nothing_and_is_closed(
    app_database: Database, fake_clock: FakeClock
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    maker, checker = staff(), staff()
    proposal = await propose(app_database, fake_clock, mistake, maker)
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(InvalidEntry):
            await corrections.reject_correction(session, checker, proposal.id, "  ", fake_clock)
        decision = await corrections.reject_correction(
            session, checker, proposal.id, "The statement shows it was theirs", fake_clock
        )
    assert (decision.outcome, decision.entry_id) == ("rejected", None)
    another = staff()
    async with app_database.actor_transaction(another) as session:
        with pytest.raises(CorrectionAlreadyDecided):
            await corrections.approve_correction(session, another, proposal.id, fake_clock)
        with pytest.raises(CorrectionAlreadyDecided):
            await corrections.reject_correction(session, another, proposal.id, "Again", fake_clock)
        events = await audit.events_for_resource(session, "ledger_correction", proposal.id)
    assert [event.action for event in events] == [
        "ledger.correction_rejected",
        "ledger.correction_proposed",
    ]
    assert await state(app_database, mistake) == BEFORE


async def test_two_checkers_deciding_at_once_decide_once(
    app_database: Database, fake_clock: FakeClock
) -> None:
    for second_action in ("approve", "reject"):
        mistake = await make_mistake(app_database, fake_clock)
        proposal = await propose(app_database, fake_clock, mistake, staff())
        barrier = AsyncBarrier(2)

        async def decide(action: str, proposal_id: str = proposal.id) -> str:
            checker = staff()
            async with app_database.actor_transaction(checker) as session:
                await barrier.wait()  # noqa: B023 - one barrier per round, awaited within it
                try:
                    if action == "approve":
                        await corrections.approve_correction(
                            session, checker, proposal_id, fake_clock
                        )
                    else:
                        await corrections.reject_correction(
                            session, checker, proposal_id, "Not ours to move", fake_clock
                        )
                except CorrectionAlreadyDecided:
                    return "lost"
                return action

        results = await asyncio.gather(decide("approve"), decide(second_action))
        assert results.count("lost") == 1
        winner = next(result for result in results if result != "lost")
        expected = AFTER if winner == "approve" else BEFORE
        assert await state(app_database, mistake) == expected
        async with app_database.actor_transaction(staff()) as session:
            decision = await corrections.get_decision(session, proposal.id)
            events = await audit.events_for_resource(session, "ledger_correction", proposal.id)
        assert decision is not None
        assert decision.outcome == ("approved" if winner == "approve" else "rejected")
        assert len(events) == 2  # the proposal and one decision; the loser left no trace


async def test_an_approval_that_no_longer_fits_the_books_changes_nothing(
    app_database: Database, fake_clock: FakeClock
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    proposal = await propose(app_database, fake_clock, mistake, staff())
    # The client is paid a further 300 before anyone approves, so 600 can no longer be taken.
    async with app_database.actor_transaction(staff()) as session:
        await ledger.post_entry(session, payout(mistake.books, 300), fake_clock)
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(InsufficientFunds):
            await corrections.approve_correction(session, checker, proposal.id, fake_clock)
        assert await corrections.get_decision(session, proposal.id) is None
        # Still usable, and the stale proposal can be closed properly.
        await corrections.reject_correction(
            session, checker, proposal.id, "Balance changed, propose again", fake_clock
        )
    assert await state(app_database, mistake) == (5_300, 300, 0)


async def test_a_reversal_can_also_be_made_to_need_two_people(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    async with app_database.actor_transaction(staff()) as session:
        books = await open_books(session, fake_clock)
        wrong = await ledger.post_entry(session, deposit(books, 700), fake_clock)
    maker, checker = staff(), staff()
    async with app_database.actor_transaction(maker) as session:
        with pytest.raises(InvalidEntry):
            await corrections.propose_correction(
                session,
                maker,
                fake_clock,
                corrects_entry_id=wrong.id,
                effective_date=DAY,
                reason="Both lines and reverse",
                lines=wrong.lines,
                reverse=True,
            )
        with pytest.raises(InvalidEntry):
            await corrections.propose_correction(
                session,
                maker,
                fake_clock,
                corrects_entry_id=wrong.id,
                effective_date=DAY,
                reason="Neither",
            )
        proposal = await corrections.propose_correction(
            session,
            maker,
            fake_clock,
            corrects_entry_id=wrong.id,
            effective_date=DAY,
            reason="Provider says the payment never arrived",
            reverse=True,
        )
    async with app_database.actor_transaction(checker) as session:
        undo = await corrections.approve_correction(session, checker, proposal.id, fake_clock)
        assert undo.reverses_entry_id == wrong.id
        assert (await ledger.get_balance(session, books.holding)).is_zero
        assert (await ledger.get_balance(session, books.cash)).is_zero
    # Not even the worker can reverse it a second time.
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(AlreadyReversed):
            await ledger.reverse_entry(
                session, wrong.id, fake_clock, effective_date=DAY, idempotency_key=key()
            )
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(AlreadyReversed):
            await corrections.propose_correction(
                session,
                checker,
                fake_clock,
                corrects_entry_id=undo.id,
                effective_date=DAY,
                reason="Undo the undo",
                reverse=True,
            )


async def test_only_staff_can_propose_or_decide_and_only_as_themselves(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    maker = staff()
    proposal = await propose(app_database, fake_clock, mistake, maker)
    impostor = staff()
    # A staff transaction writing in a colleague's name.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(impostor) as session:
            await corrections.propose_correction(
                session,
                maker,
                fake_clock,
                corrects_entry_id=mistake.deposit_entry,
                effective_date=DAY,
                lines=mistake.lines(),
                reason="Not me",
            )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(maker) as session:
            await corrections.approve_correction(session, impostor, proposal.id, fake_clock)
    # A client cannot see proposals, and the domain refuses a client or the system outright.
    someone = client()
    async with app_database.actor_transaction(someone) as session:
        with pytest.raises(UnknownCorrection):
            await corrections.get_correction(session, proposal.id)
        assert await corrections.pending_corrections(session) == []
        for actor in (someone, SYSTEM):
            with pytest.raises(InvalidEntry):
                await corrections.approve_correction(session, actor, proposal.id, fake_clock)
    # The worker can read for reports but holds no INSERT.
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert (await corrections.get_correction(session, proposal.id)).id == proposal.id
    for table in ("ledger_correction_proposals", "ledger_correction_decisions"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(f"INSERT INTO {table} DEFAULT VALUES"))
    assert await state(app_database, mistake) == BEFORE


RAW_ENTRY = text(
    "INSERT INTO ledger_journal_entries (id, entry_type, currency, effective_date, posted_at, "
    "idempotency_key, request_digest, source_type, source_id) VALUES (:id, :entry_type, 'UGX', "
    ":day, now(), :key, repeat('0', 64), 'ledger.correction', :proposal)"
)
RAW_LINE = text(
    "INSERT INTO ledger_lines (entry_id, line_no, account_id, currency, amount_minor) "
    "VALUES (:entry, :line_no, :account, 'UGX', :amount)"
)
RAW_APPROVAL = text(
    "INSERT INTO ledger_correction_decisions (proposal_id, outcome, decided_by, decided_at, "
    "reason, entry_id) VALUES (:proposal, 'approved', :me, now(), 'ok', :entry)"
)


async def raw_correction(
    database: Database,
    checker: Actor,
    proposal: CorrectionProposal,
    lines: list[tuple[str, int]],
    *,
    entry_type: str | None = None,
    approve: bool = True,
    late_lines: tuple[tuple[str, int], ...] = (),
) -> None:
    """Post a correction with plain SQL, as a careless or hostile caller could."""
    entry = push_id()
    async with database.actor_transaction(checker) as session:
        await session.execute(
            RAW_ENTRY,
            {
                "id": entry,
                "entry_type": entry_type or proposal.entry_type,
                "day": proposal.effective_date,
                "key": key(),
                "proposal": proposal.id,
            },
        )
        for line_no, (account, amount) in enumerate(lines, start=1):
            await session.execute(
                RAW_LINE, {"entry": entry, "line_no": line_no, "account": account, "amount": amount}
            )
        if approve:
            await session.execute(
                RAW_APPROVAL, {"proposal": proposal.id, "me": checker.id, "entry": entry}
            )
        if late_lines:
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            for line_no, (account, amount) in enumerate(late_lines, start=len(lines) + 1):
                await session.execute(
                    RAW_LINE,
                    {"entry": entry, "line_no": line_no, "account": account, "amount": amount},
                )


async def test_the_database_only_commits_the_correction_that_was_approved(
    app_database: Database, fake_clock: FakeClock
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    proposal = await propose(app_database, fake_clock, mistake, staff())
    checker = staff()
    books = mistake.books
    proposed = [(line.account_id, line.amount.minor) for line in proposal.lines]

    # No approval at all.
    with pytest.raises(DBAPIError, match="needs an approved proposal"):
        await raw_correction(app_database, checker, proposal, proposed, approve=False)
    # Approved, but more is taken from the client than was proposed.
    with pytest.raises(DBAPIError, match="exactly what was proposed"):
        await raw_correction(
            app_database,
            checker,
            proposal,
            [(books.holding, 500), (mistake.receivable, 500), (books.cash, -1_000)],
        )
    # Approved, but under another entry type.
    with pytest.raises(DBAPIError, match="exactly what was proposed"):
        await raw_correction(app_database, checker, proposal, proposed, entry_type="test.other")
    # Approved as proposed, then a balanced pair of extra lines after an early check.
    async with app_database.actor_transaction(staff()) as session:
        other = await open_books(session, fake_clock)
        await ledger.post_entry(session, deposit(other, 100), fake_clock)
    with pytest.raises(DBAPIError, match="exactly what was proposed"):
        await raw_correction(
            app_database,
            checker,
            proposal,
            proposed,
            late_lines=((other.holding, 50), (other.cash, -50)),
        )
    assert await state(app_database, mistake) == BEFORE
    async with app_database.actor_transaction(checker) as session:
        assert await corrections.get_decision(session, proposal.id) is None

    # An approval cannot be pointed at an ordinary entry either.
    async with app_database.actor_transaction(checker) as session:
        ordinary = await ledger.post_entry(session, deposit(other, 10), fake_clock)
    with pytest.raises(DBAPIError, match="must point at a correction entry"):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                RAW_APPROVAL, {"proposal": proposal.id, "me": checker.id, "entry": ordinary.id}
            )

    # Exactly as proposed is accepted, even by hand.
    await raw_correction(app_database, checker, proposal, proposed)
    assert await state(app_database, mistake) == AFTER


async def test_a_proposal_that_repeats_a_line_matches_no_entry(
    app_database: Database, fake_clock: FakeClock
) -> None:
    """Written with plain SQL, a proposal can say [A +100, B -100, B -100]. An entry of
    {A +100, B -100} is not that proposal, and no entry can be."""
    mistake = await make_mistake(app_database, fake_clock)
    books = mistake.books
    maker, checker = staff(), staff()
    forged = replace(
        await propose(app_database, fake_clock, mistake, maker),
        id=push_id(),
        lines=(debit(books.holding, ugx(100)), credit(books.cash, ugx(100))),
    )
    async with app_database.actor_transaction(maker) as session:
        await session.execute(
            text(
                "INSERT INTO ledger_correction_proposals (id, corrects_entry_id, reverses, "
                "entry_type, currency, effective_date, lines, reason, proposed_by, proposed_at) "
                "VALUES (:id, :corrects, false, :entry_type, 'UGX', :day, "
                "CAST(:lines AS jsonb), 'Looks smaller than it is', :me, now())"
            ),
            {
                "id": forged.id,
                "corrects": forged.corrects_entry_id,
                "entry_type": forged.entry_type,
                "day": forged.effective_date,
                "lines": json.dumps(
                    [
                        {"account_id": books.holding, "amount_minor": 100},
                        {"account_id": books.cash, "amount_minor": -100},
                        {"account_id": books.cash, "amount_minor": -100},
                    ]
                ),
                "me": maker.id,
            },
        )
    with pytest.raises(DBAPIError, match="exactly what was proposed"):
        await raw_correction(
            app_database, checker, forged, [(books.holding, 100), (books.cash, -100)]
        )
    assert await state(app_database, mistake) == BEFORE


async def test_the_service_cannot_post_a_correction_entry_without_an_approval(
    app_database: Database, fake_clock: FakeClock
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    proposal = await propose(app_database, fake_clock, mistake, staff())
    with pytest.raises(DBAPIError, match="needs an approved proposal"):
        async with app_database.actor_transaction(staff()) as session:
            await ledger.post_entry(session, proposal.draft(), fake_clock)
    assert await state(app_database, mistake) == BEFORE


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_correction_proposals SET reason = 'rewritten'",
        "UPDATE ledger_correction_proposals SET lines = '[]'::jsonb",
        "DELETE FROM ledger_correction_proposals",
        "UPDATE ledger_correction_decisions SET outcome = 'approved'",
        "DELETE FROM ledger_correction_decisions",
        "TRUNCATE ledger_correction_decisions",
        "TRUNCATE ledger_correction_proposals, ledger_correction_decisions",
    ],
)
async def test_proposals_and_decisions_are_never_rewritten(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
    statement: str,
) -> None:
    mistake = await make_mistake(app_database, fake_clock)
    maker, checker = staff(), staff()
    proposal = await propose(app_database, fake_clock, mistake, maker)
    async with app_database.actor_transaction(checker) as session:
        await corrections.reject_correction(session, checker, proposal.id, "No", fake_clock)
    for database, actor in ((app_database, checker), (worker_database, SYSTEM)):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with database.actor_transaction(actor) as session:
                await session.execute(text(statement))
    with pytest.raises(DBAPIError, match="is insert-only"):
        async with migrator_database.transaction() as session:
            await session.execute(text(statement))
    async with app_database.actor_transaction(checker) as session:
        saved = await corrections.get_correction(session, proposal.id)
        decision = await corrections.get_decision(session, proposal.id)
    assert saved.reason == "Deposit belonged to another client"
    assert json.dumps([line.amount.minor for line in saved.lines]) == "[600, 400, -1000]"
    assert decision is not None and decision.outcome == "rejected"
