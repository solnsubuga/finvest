"""Payout destinations: the steps, who may take them, and what the database refuses."""

import asyncio
from datetime import date, timedelta

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

pytestmark = pytest.mark.integration

ID_NAME = "Nakato Sarah Grace"


def client() -> Actor:
    return Actor("client", push_id())


def staff() -> Actor:
    return Actor("staff", push_id())


def cid(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def bank_account() -> BankAccount:
    return BankAccount("stanbic", "9030012345678")


async def in_review(database: Database, owner: Actor, clock: FakeClock) -> str:
    async with database.actor_transaction(owner) as session:
        view = await service.add_destination(session, owner, bank_account(), "Sarah Nakato", clock)
        await service.confirm_code(session, owner, view.id, clock)
        await service.submit_proof(session, owner, view.id, ProofKind.BANK_LETTER, "doc-1", clock)
    return view.id


async def approved(database: Database, owner: Actor, clock: FakeClock) -> str:
    destination_id = await in_review(database, owner, clock)
    checker = staff()
    async with database.actor_transaction(checker) as session:
        await service.approve(
            session,
            checker,
            destination_id,
            id_name=ID_NAME,
            proof_name="NAKATO SARAH GRACE",
            statement_date=None,
            reason="Letter matches the ID",
            clock=clock,
        )
    return destination_id


async def test_a_destination_walks_every_step_and_then_waits_24_hours(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
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
        done = await service.approve(
            session,
            checker,
            added.id,
            id_name=ID_NAME,
            proof_name="Sarah Nakato",
            statement_date=fake_clock.now().date() - timedelta(days=30),
            reason="Statement shows the account in her name",
            clock=fake_clock,
        )
        events = await audit.events_for_resource(session, "payout_destination", added.id)
        assert added.id not in [item.id for item in await service.review_queue(session, limit=100)]

    assert done.status is DestinationStatus.APPROVED
    assert done.name_match is NameMatch.PARTIAL
    assert done.approved_at == fake_clock.now()
    assert done.payable_from == fake_clock.now() + timedelta(hours=24)
    assert why_not_payable(done.state, fake_clock.now()) is NotPayable.SAFETY_WAIT
    assert why_not_payable(done.state, fake_clock.now() + timedelta(hours=24)) is None
    assert [event.action for event in events] == [
        "payouts.destination_approved",
        "payouts.destination_proof_submitted",
        "payouts.destination_code_confirmed",
        "payouts.destination_added",
    ]
    approval = events[0]
    assert (approval.category, approval.actor_id) == ("approval", checker.id)
    assert approval.details == {"name_match": "partial", "proof_kind": "bank_statement"}
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
        async with app_database.actor_transaction(checker) as session:
            await service.approve(
                session,
                checker,
                destination_id,
                id_name=ID_NAME,
                proof_name=proof_name,
                statement_date=None,
                reason="Looks fine",
                clock=fake_clock,
            )
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
    owner, checker = client(), staff()
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
            async with app_database.actor_transaction(checker) as session:
                await service.approve(
                    session,
                    checker,
                    view.id,
                    id_name=ID_NAME,
                    proof_name=ID_NAME,
                    statement_date=statement_date,
                    reason="Checked",
                    clock=fake_clock,
                )
        assert refused.value.code == code


async def test_a_rejected_destination_can_be_proved_again(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, checker = client(), staff()
    destination_id = await in_review(app_database, owner, fake_clock)
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
            await service.approve(
                session,
                owner,
                destination_id,
                id_name=ID_NAME,
                proof_name=ID_NAME,
                statement_date=None,
                reason="It is mine",
                clock=fake_clock,
            )
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
    first = await approved(app_database, owner, fake_clock)
    async with app_database.actor_transaction(owner) as session:
        with pytest.raises(ApplicationError) as duplicate:
            await service.add_destination(session, owner, bank_account(), "Sarah", fake_clock)
        assert duplicate.value.code == "conflict"
        await service.remove(session, owner, first, fake_clock)
        again = await service.add_destination(session, owner, bank_account(), "Sarah", fake_clock)
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

    async def approve_it() -> str:
        checker = staff()
        try:
            async with app_database.actor_transaction(checker) as session:
                await service.approve(
                    session,
                    checker,
                    destination_id,
                    id_name=ID_NAME,
                    proof_name=ID_NAME,
                    statement_date=None,
                    reason="Checked",
                    clock=fake_clock,
                )
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
    assert len([e for e in events if e.category == "approval"]) == 1


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
