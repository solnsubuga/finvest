"""Payout destinations: the steps, who may take them, and what the database refuses."""

import asyncio
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.payouts import destinations as service
from app.modules.payouts.domain.destinations import (
    BankAccount,
    DestinationError,
    DestinationStatus,
    MobileMoneyNumber,
    NameMatch,
    NotPayable,
    ProofKind,
    RejectionReason,
    why_not_payable,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.kyc import VERIFIED_NAME, verify

pytestmark = pytest.mark.integration

# Owners already taken through an identity check in this run.
_verified: set[str] = set()


@pytest.fixture
def fake_clock() -> FakeClock:
    # An approval is stamped against the database clock, so these tests start at the real time.
    return FakeClock(datetime.now(UTC))


def client() -> Actor:
    return Actor("client", push_id())


def staff() -> Actor:
    return Actor("staff", push_id())


def cid(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def bank_account(number: str = "9030012345678") -> BankAccount:
    return BankAccount("stanbic", number)


def fresh_number() -> str:
    return str(secrets.randbelow(10**12) + 10**12)


async def in_review(
    database: Database, owner: Actor, clock: FakeClock, account: BankAccount | None = None
) -> str:
    # A fresh account each time: an account can be approved for one client only.
    account = account or bank_account(fresh_number())
    if cid(owner) not in _verified:
        # A check compares the proof with the name on the client's verified ID.
        await verify(database, owner, clock)
        _verified.add(cid(owner))
    async with database.actor_transaction(owner) as session:
        view = await service.add_destination(session, owner, account, "Sarah Nakato", clock)
        await service.confirm_code(session, owner, view.id, clock)
        await service.submit_proof(session, owner, view.id, ProofKind.BANK_LETTER, "doc-1", clock)
    return view.id


async def checked(
    database: Database,
    destination_id: str,
    clock: FakeClock,
    *,
    proof_name: str = VERIFIED_NAME,
    statement_date: date | None = None,
) -> Actor:
    """The first person's check. Returns who made it."""
    checker = staff()
    async with database.actor_transaction(checker) as session:
        await service.check(
            session,
            checker,
            destination_id,
            proof_name=proof_name,
            statement_date=statement_date,
            reason="Letter matches the ID",
            clock=clock,
        )
    return checker


async def approve(
    database: Database, approver: Actor, destination_id: str, clock: FakeClock
) -> service.DestinationView:
    async with database.actor_transaction(approver) as session:
        return await service.approve(
            session, approver, destination_id, reason="Second check", clock=clock
        )


async def approved(
    database: Database, owner: Actor, clock: FakeClock, account: BankAccount | None = None
) -> str:
    destination_id = await in_review(database, owner, clock, account)
    await checked(database, destination_id, clock)
    await approve(database, staff(), destination_id, clock)
    return destination_id


async def test_a_destination_walks_every_step_and_then_waits_24_hours(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker, approver = client(), staff(), staff()
    await verify(app_database, owner, fake_clock)
    async with app_database.actor_transaction(owner) as session:
        added = await service.add_destination(
            session, owner, bank_account(), " Sarah Nakato ", fake_clock
        )
        assert added.status is DestinationStatus.AWAITING_CODE
        assert added.masked_account == "•••• 5678"
        assert added.holder_name == "Sarah Nakato"
        confirmed = await service.confirm_code(session, owner, added.id, fake_clock)
        assert confirmed.status is DestinationStatus.AWAITING_PROOF
        submitted = await service.submit_proof(
            session, owner, added.id, ProofKind.BANK_STATEMENT, "doc-9", fake_clock
        )
        assert submitted.status is DestinationStatus.IN_REVIEW

    async with app_database.actor_transaction(checker) as session:
        assert added.id in [item.id for item in await service.review_queue(session, limit=100)]
        statement_date = fake_clock.now().date() - timedelta(days=30)
        recorded = await service.check(
            session,
            checker,
            added.id,
            proof_name="Amara Grace Nakato",
            statement_date=statement_date,
            reason="Statement shows the account in her name",
            clock=fake_clock,
        )
    assert recorded.status is DestinationStatus.IN_REVIEW
    assert (recorded.checked_by, recorded.proof_name, recorded.statement_date) == (
        checker.id,
        "Amara Grace Nakato",
        statement_date,
    )
    async with app_database.actor_transaction(approver) as session:
        # Still in the queue: it waits for the second person.
        assert added.id in [item.id for item in await service.review_queue(session, limit=100)]
        done = await service.approve(
            session, approver, added.id, reason="Agree with the check", clock=fake_clock
        )
        events = await audit.events_for_resource(session, "payout_destination", added.id)
        assert added.id not in [item.id for item in await service.review_queue(session, limit=100)]

    assert done.status is DestinationStatus.APPROVED
    assert (done.checked_by, done.approved_by) == (checker.id, approver.id)
    assert done.name_match is NameMatch.PARTIAL
    assert done.approved_at == fake_clock.now()
    assert done.payable_from == fake_clock.now() + timedelta(hours=24)
    assert why_not_payable(done.state, fake_clock.now()) is NotPayable.SAFETY_WAIT
    assert why_not_payable(done.state, fake_clock.now() + timedelta(hours=24)) is None
    assert [event.action for event in events] == [
        "payouts.destination_approved",
        "payouts.destination_checked",
        "payouts.destination_proof_submitted",
        "payouts.destination_code_confirmed",
        "payouts.destination_added",
    ]
    approval, check = events[0], events[1]
    assert (approval.category, approval.actor_id) == ("approval", approver.id)
    assert approval.details == {
        "name_match": "partial",
        "proof_kind": "bank_statement",
        "checked_by": checker.id,
    }
    assert (check.category, check.actor_id) == ("approval", checker.id)
    assert check.details == {"name_match": "partial", "proof_kind": "bank_statement"}
    assert "9030012345678" not in str([event.details for event in events])


async def test_a_mobile_money_number_is_saved_with_its_network(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    async with app_database.actor_transaction(owner) as session:
        number = MobileMoneyNumber.parse("0772123456")
        view = await service.add_destination(session, owner, number, "Sarah Nakato", fake_clock)
    assert (view.network, view.bank_code, view.masked_account) == ("mtn", None, "+256 77• ••• 456")


@pytest.mark.parametrize("proof_name", ["Nakato Peter", "Peter Okello", "Nakato", ""])
async def test_a_different_name_is_never_approved(
    app_database: Database, fake_clock: FakeClock, proof_name: str
) -> None:
    owner, checker = client(), staff()
    destination_id = await in_review(app_database, owner, fake_clock)
    with pytest.raises(DestinationError) as refused:
        await checked(app_database, destination_id, fake_clock, proof_name=proof_name)
    assert refused.value.code == "name_different"
    async with app_database.actor_transaction(checker) as session:
        assert (await service.get_destination(session, destination_id)).status is (
            DestinationStatus.IN_REVIEW
        )


async def test_the_database_refuses_an_approval_with_a_different_name(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
    destination_id = await in_review(app_database, owner, fake_clock)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE payout_destinations SET status = 'approved', approved_at = now(), "
                    "checked_by = :staff, proof_name = 'Someone Else', name_match = 'different' "
                    "WHERE id = :id"
                ),
                {"id": destination_id, "staff": checker.id},
            )


async def test_a_statement_must_be_recent(app_database: Database, fake_clock: FakeClock) -> None:
    owner = client()
    await verify(app_database, owner, fake_clock)
    async with app_database.actor_transaction(owner) as session:
        view = await service.add_destination(session, owner, bank_account(), "Sarah", fake_clock)
        await service.confirm_code(session, owner, view.id, fake_clock)
        await service.submit_proof(
            session, owner, view.id, ProofKind.BANK_STATEMENT, "doc-1", fake_clock
        )
    for statement_date, code in (
        (None, "statement_date_required"),
        (date(2020, 1, 1), "statement_too_old"),
    ):
        with pytest.raises(DestinationError) as refused:
            await checked(app_database, view.id, fake_clock, statement_date=statement_date)
        assert refused.value.code == code


async def test_a_rejected_destination_can_be_proved_again(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
    destination_id = await in_review(app_database, owner, fake_clock)
    await checked(app_database, destination_id, fake_clock)
    async with app_database.actor_transaction(checker) as session:
        rejected = await service.reject(
            session,
            checker,
            destination_id,
            RejectionReason.PROOF_UNREADABLE,
            "Photo is blurred",
            fake_clock,
        )
    assert rejected.status is DestinationStatus.REJECTED
    assert rejected.rejection_reason is RejectionReason.PROOF_UNREADABLE
    async with app_database.actor_transaction(owner) as session:
        again = await service.submit_proof(
            session, owner, destination_id, ProofKind.BANK_CARD, "doc-2", fake_clock
        )
    assert (again.status, again.rejection_reason, again.proof_document_id) == (
        DestinationStatus.IN_REVIEW,
        None,
        "doc-2",
    )
    # The new proof is checked afresh: the check of the old one is gone and cannot be approved.
    assert (again.checked_by, again.checked_at, again.proof_name, again.name_match) == (
        None,
        None,
        None,
        None,
    )
    with pytest.raises(DestinationError) as unchecked:
        await approve(app_database, staff(), destination_id, fake_clock)
    assert unchecked.value.code == "destination_not_checked"


async def told(database: Database, destination_id: str) -> list[tuple[str, str, str]]:
    """What the client was told about an account, oldest first."""
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT event, body, sms FROM notifications WHERE link_kind = "
                "'payout_destination' AND link_id = :id ORDER BY created_at, id"
            ),
            {"id": destination_id},
        )
        return [(row.event, row.body, row.sms) for row in rows]


async def test_the_client_is_told_each_refusal_and_the_approval(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
    destination_id = await in_review(app_database, owner, fake_clock)
    await checked(app_database, destination_id, fake_clock)
    async with app_database.actor_transaction(checker) as session:
        await service.reject(
            session,
            checker,
            destination_id,
            RejectionReason.PROOF_UNREADABLE,
            "Blurred",
            fake_clock,
        )
    async with app_database.actor_transaction(owner) as session:
        await service.submit_proof(
            session, owner, destination_id, ProofKind.BANK_CARD, "doc-2", fake_clock
        )
    async with app_database.actor_transaction(checker) as session:
        await service.reject(
            session, checker, destination_id, RejectionReason.PROOF_TOO_OLD, "Old", fake_clock
        )
    async with app_database.actor_transaction(owner) as session:
        await service.submit_proof(
            session, owner, destination_id, ProofKind.BANK_LETTER, "doc-3", fake_clock
        )
    await checked(app_database, destination_id, fake_clock)
    await approve(app_database, staff(), destination_id, fake_clock)

    # Each decision is told once, naming the bank and never the account number.
    assert await told(worker_database, destination_id) == [
        (
            "payout_destination_refused",
            "We could not approve your Stanbic Bank account. See why in the app.",
            "Finvest: we could not approve your Stanbic Bank account for withdrawals. "
            "See why in the app.",
        ),
        (
            "payout_destination_refused",
            "We could not approve your Stanbic Bank account. See why in the app.",
            "Finvest: we could not approve your Stanbic Bank account for withdrawals. "
            "See why in the app.",
        ),
        (
            "payout_destination_approved",
            "We approved your Stanbic Bank account. You can withdraw to it after the 24 hour wait.",
            "Finvest: we approved your Stanbic Bank account for withdrawals after the 24 hour "
            "wait.",
        ),
    ]


# --- Who may do what --------------------------------------------------------------------------


async def test_a_client_sees_only_their_own_destinations(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, other = client(), client()
    destination_id = await in_review(app_database, owner, fake_clock)
    async with app_database.actor_transaction(other) as session:
        assert await service.destinations_for_client(session, cid(owner)) == []
        assert await service.review_queue(session) == []
        with pytest.raises(ApplicationError) as hidden:
            await service.get_destination(session, destination_id)
        assert hidden.value.code == "not_found"
        with pytest.raises(ApplicationError):
            await service.remove(session, other, destination_id, fake_clock)
    async with app_database.transaction() as session:
        assert await service.destinations_for_client(session, cid(owner)) == []
    async with app_database.actor_transaction(owner) as session:
        assert [d.id for d in await service.destinations_for_client(session, cid(owner))] == [
            destination_id
        ]


async def test_a_client_cannot_approve_their_own_destination(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    destination_id = await in_review(app_database, owner, fake_clock)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(owner) as session:
            await service.check(
                session,
                owner,
                destination_id,
                proof_name=VERIFIED_NAME,
                statement_date=None,
                reason="It is mine",
                clock=fake_clock,
            )
    await checked(app_database, destination_id, fake_clock)
    with pytest.raises(DBAPIError):
        await approve(app_database, owner, destination_id, fake_clock)
    async with app_database.actor_transaction(owner) as session:
        assert (await service.get_destination(session, destination_id)).status is (
            DestinationStatus.IN_REVIEW
        )


async def test_a_client_cannot_add_a_destination_for_someone_else_or_already_approved(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, victim = client(), client()
    insert = (
        "INSERT INTO payout_destinations (id, client_id, kind, bank_code, account_number, "
        "holder_name, status, approved_at, checked_by, proof_kind, proof_name, name_match, "
        "created_at, updated_at) VALUES (:id, :client_id, 'bank', 'stanbic', '9030012345678', "
        "'X', :status, :approved_at, :checked_by, :proof_kind, :proof_name, :name_match, "
        "now(), now())"
    )
    blank = {
        "status": "awaiting_code",
        "approved_at": None,
        "checked_by": None,
        "proof_kind": None,
        "proof_name": None,
        "name_match": None,
    }
    forged = {
        "status": "approved",
        "approved_at": fake_clock.now(),
        "checked_by": push_id(),
        "proof_kind": "bank_letter",
        "proof_name": "X",
        "name_match": "exact",
    }
    for values in ({**blank, "client_id": victim.id}, {**forged, "client_id": owner.id}):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(owner) as session:
                await session.execute(text(insert), {"id": push_id(), **values})
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(text(insert), {"id": push_id(), **blank, "client_id": owner.id})


async def test_staff_cannot_take_a_clients_steps_or_sign_for_a_colleague(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
    async with app_database.actor_transaction(owner) as session:
        view = await service.add_destination(session, owner, bank_account(), "Sarah", fake_clock)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await service.confirm_code(session, checker, view.id, fake_clock)

    destination_id = await in_review(app_database, client(), fake_clock)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE payout_destinations SET status = 'approved', approved_at = now(), "
                    "checked_by = :other, proof_name = 'N', name_match = 'exact' WHERE id = :id"
                ),
                {"id": destination_id, "other": push_id()},
            )


async def test_the_account_of_a_destination_never_changes(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    destination_id = await approved(app_database, owner, fake_clock)
    changes = [
        "account_number = '9030099999999'",
        "bank_code = 'absa'",
        "holder_name = 'Someone Else'",
        f"client_id = '{push_id()}'",
    ]
    for database, actor in (
        (app_database, owner),
        (app_database, staff()),
        (worker_database, SYSTEM),
    ):
        for change in changes:
            with pytest.raises(DBAPIError):
                async with database.actor_transaction(actor) as session:
                    await session.execute(
                        text(f"UPDATE payout_destinations SET {change} WHERE id = :id"),  # noqa: S608
                        {"id": destination_id},
                    )


async def test_destinations_are_never_deleted_and_removed_is_final(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    destination_id = await approved(app_database, owner, fake_clock)
    async with app_database.actor_transaction(owner) as session:
        await service.remove(session, owner, destination_id, fake_clock)
        assert await service.destinations_for_client(session, cid(owner)) == []
    with pytest.raises(DestinationError):
        async with app_database.actor_transaction(owner) as session:
            await service.remove(session, owner, destination_id, fake_clock)
    for statement in (
        "UPDATE payout_destinations SET status = 'approved' WHERE id = :id",
        "DELETE FROM payout_destinations WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with migrator_database.transaction() as session:
                await session.execute(text(statement), {"id": destination_id})
    with pytest.raises(DBAPIError):
        async with migrator_database.transaction() as session:
            await session.execute(text("TRUNCATE payout_destinations"))


async def test_a_removed_account_can_be_added_again_from_the_first_step(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    account = bank_account(fresh_number())
    first = await approved(app_database, owner, fake_clock, account)
    async with app_database.actor_transaction(owner) as session:
        with pytest.raises(ApplicationError) as duplicate:
            await service.add_destination(session, owner, account, "Sarah", fake_clock)
        assert duplicate.value.code == "conflict"
        await service.remove(session, owner, first, fake_clock)
        again = await service.add_destination(session, owner, account, "Sarah", fake_clock)
    assert again.id != first
    assert again.status is DestinationStatus.AWAITING_CODE


# --- The worker -------------------------------------------------------------------------------


async def test_only_the_worker_records_the_first_payout_and_only_once(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    destination_id = await approved(app_database, owner, fake_clock)
    first = fake_clock.now() + timedelta(days=2)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await service.record_first_payout(session, destination_id, first)
        await service.record_first_payout(session, destination_id, first + timedelta(days=3))
        assert (await service.get_destination(session, destination_id)).first_paid_at == first
    for actor in (owner, staff()):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(
                    text("UPDATE payout_destinations SET first_paid_at = NULL WHERE id = :id"),
                    {"id": destination_id},
                )
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE payout_destinations SET status = 'removed' WHERE id = :id"),
                {"id": destination_id},
            )


# --- Races, in separate transactions ----------------------------------------------------------


async def test_two_staff_checking_at_once_leave_one_decision(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    destination_id = await in_review(app_database, owner, fake_clock)
    await checked(app_database, destination_id, fake_clock)

    async def approve_it() -> str:
        try:
            await approve(app_database, staff(), destination_id, fake_clock)
        except DestinationError as refused:
            return refused.code
        return "approved"

    async def reject_it() -> str:
        checker = staff()
        try:
            async with app_database.actor_transaction(checker) as session:
                await service.reject(
                    session,
                    checker,
                    destination_id,
                    RejectionReason.PROOF_UNREADABLE,
                    "Blurred",
                    fake_clock,
                )
        except DestinationError as refused:
            return refused.code
        return "rejected"

    results = await asyncio.gather(approve_it(), reject_it(), approve_it(), reject_it())
    assert sorted(results).count("destination_not_in_review") == 3
    async with app_database.actor_transaction(staff()) as session:
        events = await audit.events_for_resource(session, "payout_destination", destination_id)
    decisions = [e for e in events if e.action != "payouts.destination_checked"]
    assert len([e for e in decisions if e.category == "approval"]) == 1


async def test_adding_the_same_account_twice_at_once_saves_it_once(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()

    async def add() -> str:
        try:
            async with app_database.actor_transaction(owner) as session:
                await service.add_destination(session, owner, bank_account(), "Sarah", fake_clock)
        except ApplicationError as refused:
            return refused.code
        return "added"

    results = await asyncio.gather(*(add() for _ in range(5)))
    assert sorted(results) == ["added", "conflict", "conflict", "conflict", "conflict"]
    async with app_database.actor_transaction(owner) as session:
        assert len(await service.destinations_for_client(session, cid(owner))) == 1


# --- Guards added after review ----------------------------------------------------------------


async def test_an_account_approved_for_one_client_is_not_approved_for_another(
    app_database: Database, fake_clock: FakeClock
) -> None:
    first, second = client(), client()
    account = bank_account(fresh_number())
    await approved(app_database, first, fake_clock, account)
    # The second client can add it and is told nothing about the first.
    contested = await in_review(app_database, second, fake_clock, account)
    checker = await checked(app_database, contested, fake_clock)
    with pytest.raises(DestinationError) as refused:
        await approve(app_database, staff(), contested, fake_clock)
    assert refused.value.code == "account_in_use"
    async with app_database.actor_transaction(checker) as session:
        assert (await service.get_destination(session, contested)).status is (
            DestinationStatus.IN_REVIEW
        )


async def test_a_rejection_for_suspected_fraud_is_final(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
    destination_id = await in_review(app_database, owner, fake_clock)
    async with app_database.actor_transaction(checker) as session:
        await service.reject(
            session,
            checker,
            destination_id,
            RejectionReason.SUSPECTED_FRAUD,
            "Edited statement",
            fake_clock,
        )
    with pytest.raises(DestinationError) as refused:
        async with app_database.actor_transaction(owner) as session:
            await service.submit_proof(
                session, owner, destination_id, ProofKind.BANK_CARD, "doc-2", fake_clock
            )
    assert refused.value.code == "destination_rejected_final"
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(owner) as session:
            await session.execute(
                text(
                    "UPDATE payout_destinations SET status = 'in_review', "
                    "rejection_reason = NULL WHERE id = :id"
                ),
                {"id": destination_id},
            )
    async with app_database.actor_transaction(owner) as session:
        await service.remove(session, owner, destination_id, fake_clock)


@pytest.mark.parametrize("skew", [timedelta(hours=-25), timedelta(hours=25), timedelta(minutes=6)])
async def test_an_approval_is_stamped_with_the_current_time(
    app_database: Database, fake_clock: FakeClock, skew: timedelta
) -> None:
    owner, checker = client(), staff()
    destination_id = await in_review(app_database, owner, fake_clock)
    wrong = FakeClock(fake_clock.now() + skew)
    with pytest.raises(DBAPIError):
        await checked(app_database, destination_id, wrong)
    await checked(app_database, destination_id, fake_clock)
    with pytest.raises(DBAPIError):
        await approve(app_database, checker, destination_id, wrong)


async def test_staff_must_say_why_they_remove_a_destination(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
    destination_id = await approved(app_database, owner, fake_clock)
    async with app_database.actor_transaction(checker) as session:
        for reason in (None, "", "   "):
            with pytest.raises(ApplicationError) as refused:
                await service.remove(session, checker, destination_id, fake_clock, reason=reason)
            assert refused.value.code == "validation_failed"
        await service.remove(
            session, checker, destination_id, fake_clock, reason="Client reported a lost phone"
        )
        events = await audit.events_for_resource(session, "payout_destination", destination_id)
    assert (events[0].action, events[0].category, events[0].reason) == (
        "payouts.destination_removed",
        "operator_control",
        "Client reported a lost phone",
    )


# --- Two people -------------------------------------------------------------------------------


async def test_whoever_checked_an_account_cannot_approve_it(
    app_database: Database, fake_clock: FakeClock
) -> None:
    destination_id = await in_review(app_database, client(), fake_clock)
    checker = await checked(app_database, destination_id, fake_clock)
    with pytest.raises(DestinationError) as refused:
        await approve(app_database, checker, destination_id, fake_clock)
    assert refused.value.code == "checked_by_you"
    # Nor around the service: the database refuses it, and an approval signed for someone else.
    for approver_id in (checker.id, push_id()):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(checker) as session:
                await session.execute(
                    text(
                        "UPDATE payout_destinations SET status = 'approved', approved_at = now(), "
                        "approved_by = :approver WHERE id = :id"
                    ),
                    {"id": destination_id, "approver": approver_id},
                )
    assert (await approve(app_database, staff(), destination_id, fake_clock)).status is (
        DestinationStatus.APPROVED
    )


async def test_an_account_is_never_approved_without_a_check_or_with_a_changed_one(
    app_database: Database, fake_clock: FakeClock
) -> None:
    destination_id = await in_review(app_database, client(), fake_clock)
    approver = staff()
    with pytest.raises(DestinationError) as unchecked:
        await approve(app_database, approver, destination_id, fake_clock)
    assert unchecked.value.code == "destination_not_checked"
    straight_to_approved = (
        "UPDATE payout_destinations SET status = 'approved', approved_at = now(), "
        "approved_by = :me, checked_by = :other, checked_at = now(), proof_name = 'N', "
        "name_match = 'exact' WHERE id = :id"
    )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(approver) as session:
            await session.execute(
                text(straight_to_approved),
                {"id": destination_id, "me": approver.id, "other": push_id()},
            )
    await checked(app_database, destination_id, fake_clock)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(approver) as session:
            await session.execute(
                text(
                    "UPDATE payout_destinations SET status = 'approved', approved_at = now(), "
                    "approved_by = :me, proof_name = 'Someone Else' WHERE id = :id"
                ),
                {"id": destination_id, "me": approver.id},
            )


async def test_a_client_cannot_carry_an_old_check_onto_new_proof(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    destination_id = await in_review(app_database, owner, fake_clock)
    await checked(app_database, destination_id, fake_clock)
    rejecter = staff()
    async with app_database.actor_transaction(rejecter) as session:
        await service.reject(
            session,
            rejecter,
            destination_id,
            RejectionReason.PROOF_WRONG_DOCUMENT,
            "Wrong document",
            fake_clock,
        )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(owner) as session:
            await session.execute(
                text(
                    "UPDATE payout_destinations SET status = 'in_review', rejection_reason = NULL, "
                    "checked_by = NULL, proof_document_id = 'doc-3' WHERE id = :id"
                ),
                {"id": destination_id},
            )


async def test_a_client_without_a_verified_id_cannot_have_an_account_checked(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    async with app_database.actor_transaction(owner) as session:
        view = await service.add_destination(session, owner, bank_account(), "Sarah", fake_clock)
        await service.confirm_code(session, owner, view.id, fake_clock)
        await service.submit_proof(
            session, owner, view.id, ProofKind.BANK_LETTER, "doc-1", fake_clock
        )
    with pytest.raises(DestinationError) as refused:
        await checked(app_database, view.id, fake_clock)
    assert refused.value.code == "client_not_verified"
