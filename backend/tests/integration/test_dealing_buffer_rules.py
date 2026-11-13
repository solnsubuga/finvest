"""Buffer rules and their two-person changes, against real PostgreSQL.

Approved versions are never removed, so each test proposes for its own far-off date, and none
leaves a proposal waiting: only one change per currency may wait at a time.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.dealing import buffer
from app.modules.dealing.domain.buffer_changes import SEED_RULES, ProposalStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

M = 1_000_000
NOW = FakeClock(datetime(2026, 11, 19, 9, tzinfo=UTC))
_days = iter(range(1, 5000))


def later() -> date:
    """An effective date no other test uses."""
    return date(2031, 1, 1) + timedelta(days=next(_days))


async def propose(
    database: Database, staff: Actor, effective_from: date, *, target: int = 160 * M
) -> buffer.RulesProposalView:
    async with database.actor_transaction(staff) as session:
        return await buffer.propose_rules(
            session,
            staff,
            effective_from=effective_from,
            target=ugx(target),
            floor=ugx(100 * M),
            from_buffer_up_to=ugx(5 * M),
            pay_within_working_days=2,
            reason="Withdrawals are rising",
            clock=NOW,
        )


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[str]]:
    return error.value.code, [problem.code for problem in error.value.fields]


async def test_the_seeded_shilling_rules_are_in_force(app_database: Database) -> None:
    async with app_database.actor_transaction(new_staff()) as session:
        rules = await buffer.in_force(session, Currency.UGX, date(2026, 11, 19))
        with pytest.raises(ApplicationError) as none:
            await buffer.in_force(session, Currency.UGX, date(2025, 12, 31))
    target, floor, up_to, days = SEED_RULES[Currency.UGX]
    assert (rules.target, rules.floor, rules.from_buffer_up_to, rules.pay_within_working_days) == (
        ugx(target),
        ugx(floor),
        ugx(up_to),
        days,
    )
    assert none.value.code == "not_found"


async def test_a_second_person_approves_a_change_and_it_takes_effect_on_its_date(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    effective_from = later()
    proposal = await propose(app_database, daniel, effective_from, target=170 * M)
    async with app_database.actor_transaction(grace) as session:
        approved = await buffer.approve_rules(session, grace, proposal.id, NOW)
        before = await buffer.in_force(session, Currency.UGX, effective_from - timedelta(days=1))
        after = await buffer.in_force(session, Currency.UGX, effective_from)
        events = await events_for_resource(session, buffer.PROPOSAL_RESOURCE, proposal.id)
    assert (approved.status, approved.decided_by) == (ProposalStatus.APPROVED, grace.id)
    assert before.target != ugx(170 * M)
    assert (after.effective_from, after.target) == (effective_from, ugx(170 * M))
    assert [event.action for event in reversed(events)] == [
        "dealing.buffer_rules.proposed",
        "dealing.buffer_rules.approved",
    ]


async def test_nobody_approves_or_sends_back_their_own_change(app_database: Database) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel, later())
    async with app_database.actor_transaction(daniel) as session:
        with pytest.raises(ApplicationError) as approving:
            await buffer.approve_rules(session, daniel, proposal.id, NOW)
        with pytest.raises(ApplicationError) as sending_back:
            await buffer.send_back_rules(session, daniel, proposal.id, "No", NOW)
        withdrawn = await buffer.withdraw_rules(session, daniel, proposal.id, NOW)
    assert approving.value.code == sending_back.value.code == "forbidden"
    assert withdrawn.status is ProposalStatus.WITHDRAWN


async def test_a_change_sent_back_needs_a_note_and_changes_nothing(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    effective_from = later()
    proposal = await propose(app_database, daniel, effective_from, target=180 * M)
    async with app_database.actor_transaction(grace) as session:
        with pytest.raises(ApplicationError) as no_note:
            await buffer.send_back_rules(session, grace, proposal.id, " ", NOW)
        sent_back = await buffer.send_back_rules(session, grace, proposal.id, "Too high", NOW)
        in_force = await buffer.in_force(session, Currency.UGX, effective_from)
        with pytest.raises(ApplicationError) as again:
            await buffer.approve_rules(session, grace, proposal.id, NOW)
    assert refused(no_note) == ("validation_failed", ["reason_required"])
    assert (sent_back.status, sent_back.decision_note) == (ProposalStatus.SENT_BACK, "Too high")
    assert in_force.target != ugx(180 * M)
    assert again.value.code == "conflict"


async def test_a_change_starts_on_a_later_day(app_database: Database) -> None:
    with pytest.raises(ApplicationError) as error:
        await propose(app_database, new_staff(), date(2026, 11, 19))
    assert refused(error) == ("validation_failed", ["effective_date_not_in_future"])


async def test_a_change_that_changes_nothing_is_refused(app_database: Database) -> None:
    target, *_ = SEED_RULES[Currency.UGX]
    with pytest.raises(ApplicationError) as error:
        await propose(app_database, new_staff(), date(2026, 12, 1), target=target)
    assert refused(error) == ("validation_failed", ["nothing_changes"])


async def test_rules_that_contradict_themselves_are_refused(app_database: Database) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as error:
            await buffer.propose_rules(
                session,
                staff,
                effective_from=later(),
                target=ugx(90 * M),
                floor=ugx(100 * M),
                from_buffer_up_to=ugx(5 * M),
                pay_within_working_days=2,
                reason="Typo",
                clock=NOW,
            )
    assert refused(error) == ("validation_failed", ["inconsistent"])


async def test_one_change_per_currency_waits_at_a_time(app_database: Database) -> None:
    grace = new_staff()
    barrier = AsyncBarrier(2)

    async def attempt(day: date) -> str | buffer.RulesProposalView:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                return await buffer.propose_rules(
                    session,
                    staff,
                    effective_from=day,
                    target=ugx(175 * M),
                    floor=ugx(100 * M),
                    from_buffer_up_to=ugx(5 * M),
                    pay_within_working_days=2,
                    reason="Two at once",
                    clock=NOW,
                )
        except ApplicationError as error:
            return error.code

    outcomes = await asyncio.gather(attempt(later()), attempt(later()))
    assert sorted(type(outcome).__name__ for outcome in outcomes) == ["RulesProposalView", "str"]
    assert "conflict" in outcomes
    waiting = next(o for o in outcomes if isinstance(o, buffer.RulesProposalView))
    async with app_database.actor_transaction(grace) as session:
        await buffer.send_back_rules(session, grace, waiting.id, "Test over", NOW)


async def test_the_database_refuses_rules_without_an_approved_proposal(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    proposal = await propose(app_database, daniel, later())
    for actor in (daniel, grace):
        with pytest.raises(DBAPIError, match="must match their approved proposal"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(
                    text(
                        "INSERT INTO dealing_buffer_rules (id, currency, effective_from, "
                        "target_minor, floor_minor, from_buffer_up_to_minor, "
                        "pay_within_working_days, proposal_id, created_at) "
                        "SELECT :id, currency, effective_from, target_minor, floor_minor, "
                        "from_buffer_up_to_minor, pay_within_working_days, id, now() "
                        "FROM dealing_buffer_rule_proposals WHERE id = :proposal_id"
                    ),
                    {"id": actor.id, "proposal_id": proposal.id},
                )
    with pytest.raises(DBAPIError, match="need an approved proposal"):
        async with app_database.actor_transaction(grace) as session:
            await session.execute(
                text(
                    "INSERT INTO dealing_buffer_rules (id, currency, effective_from, "
                    "target_minor, floor_minor, from_buffer_up_to_minor, "
                    "pay_within_working_days, created_at) "
                    "VALUES (:id, 'UGX', DATE '2040-01-01', 3, 2, 1, 2, now())"
                ),
                {"id": grace.id},
            )
    async with app_database.actor_transaction(daniel) as session:
        await buffer.withdraw_rules(session, daniel, proposal.id, NOW)


async def test_the_database_refuses_a_self_approval_and_any_change_to_a_version(
    app_database: Database,
) -> None:
    daniel = new_staff()
    proposal = await propose(app_database, daniel, later())
    with pytest.raises(DBAPIError, match="second_person"):
        async with app_database.actor_transaction(daniel) as session:
            await session.execute(
                text(
                    "UPDATE dealing_buffer_rule_proposals SET status = 'approved', "
                    "decided_by = :me, decided_at = now() WHERE id = :id"
                ),
                {"me": daniel.id, "id": proposal.id},
            )
    for statement in (
        "UPDATE dealing_buffer_rules SET target_minor = 1",
        "DELETE FROM dealing_buffer_rules",
    ):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(daniel) as session:
                await session.execute(text(statement))
    async with app_database.actor_transaction(daniel) as session:
        await buffer.withdraw_rules(session, daniel, proposal.id, NOW)


async def test_the_worker_reads_the_rules_and_clients_cannot(
    app_database: Database, worker_database: Database
) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rules = await buffer.in_force(session, Currency.UGX, date(2026, 11, 19))
    assert rules.currency is Currency.UGX
    client = Actor("client", new_staff().id)
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError):
            await buffer.in_force(session, Currency.UGX, date(2026, 11, 19))
        with pytest.raises(ApplicationError) as proposing:
            await buffer.propose_rules(
                session,
                client,
                effective_from=later(),
                target=Money(1, Currency.UGX),
                floor=Money(1, Currency.UGX),
                from_buffer_up_to=Money(1, Currency.UGX),
                pay_within_working_days=1,
                reason="No",
                clock=NOW,
            )
    assert proposing.value.code == "forbidden"
