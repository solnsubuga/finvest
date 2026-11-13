"""Trial imports, the old system's totals and two-person sign-offs against real PostgreSQL."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.migration import service as migration
from app.modules.migration.domain.finance import ControlTotals
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff
from tests.unit.test_migration_sheet import clients, investments
from tests.unit.test_migration_trial import client, investment

pytestmark = pytest.mark.integration

AS_OF = date(2026, 11, 13)
NOW = FakeClock(datetime(2026, 11, 13, 9, tzinfo=UTC))
# Tests share one database and nothing here can be deleted, so every test states its own
# interest figure. A sign-off from one test then never fits the figures of another.
_shillings = iter(range(100, 10_000))


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def totals(interest: int, *, client_count: int = 1) -> ControlTotals:
    return ControlTotals(
        clients=client_count,
        verified_identities=1,
        running_investments=1,
        pending_orders=0,
        invested=(ugx(1_000_000),),
        interest_before_tax=(ugx(interest),),
    )


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


class Case:
    """One client with one investment whose stated interest is `extra` above the formula."""

    def __init__(self, extra: int | None = None) -> None:
        self.extra = next(_shillings) if extra is None else extra
        self.ref = f"INV-{push_id()[-8:].replace('_', 'x')}"
        self.stated = 11_643 + self.extra
        self.key = f"investment:{self.ref.upper()}"
        self.figures = (self.stated, 11_643, 1_746, 1_746)

    async def trial(self, database: Database, staff: Actor, **changes: str) -> migration.TrialView:
        row = investment(self.ref, "1", interest_before_tax=str(self.stated), **changes)
        async with database.actor_transaction(staff) as session:
            return await migration.run_trial_import(
                session,
                staff,
                NOW,
                clients_csv=clients(client("1")),
                investments_csv=investments(row),
                as_of=AS_OF,
            )


async def enter(
    database: Database, staff: Actor, old: ControlTotals, *, confirm: bool = True
) -> migration.TotalsView:
    """Enter the old system's totals and, unless told not to, have a second person confirm."""
    async with database.actor_transaction(staff) as session:
        entry = await migration.enter_control_totals(session, staff, NOW, as_of=AS_OF, totals=old)
    if confirm:
        await confirm_totals(database, new_staff(), entry.id)
    return entry


async def confirm_totals(database: Database, staff: Actor, totals_id: str) -> migration.TotalsView:
    async with database.actor_transaction(staff) as session:
        return await migration.confirm_control_totals(session, staff, NOW, totals_id)


async def look(database: Database) -> migration.Overview:
    async with database.actor_transaction(new_staff()) as session:
        return await migration.overview(session)


async def propose(database: Database, staff: Actor, key: str) -> migration.SignOffView:
    async with database.actor_transaction(staff) as session:
        return await migration.propose_sign_off(
            session, staff, NOW, key=key, reason="  Finvest 1.0 counted the start date  "
        )


async def decide(
    database: Database, staff: Actor, sign_off_id: str, *, approve: bool = True
) -> migration.SignOffView:
    async with database.actor_transaction(staff) as session:
        return await migration.decide_sign_off(
            session, staff, NOW, sign_off_id, approve=approve, reason=None if approve else "No"
        )


async def test_a_trial_keeps_its_result_and_nothing_from_the_spreadsheet(
    app_database: Database, migrator_database: Database
) -> None:
    case, staff = Case(), new_staff()
    view = await case.trial(app_database, staff)

    assert (view.error_count, view.warning_count) == (0, 0)
    assert view.new_totals == {
        "total:clients": 1,
        "total:verified_identities": 1,
        "total:running_investments": 1,
        "total:pending_orders": 0,
        "total:invested:UGX": 1_000_000,
        "total:interest_before_tax:UGX": 11_643,
    }
    assert view.differing == {case.key: case.figures}
    assert view.ran_by == staff.id

    async with migrator_database.transaction() as session:
        stored = (
            await session.execute(
                text("SELECT CAST(t AS text) FROM migration_trials AS t WHERE id = :id"),
                {"id": view.id},
            )
        ).scalar_one()
    # The sample phone is +256772000001. Seven of its digits, because the row also holds a
    # SHA-256 in hex, where a short run of digits such as "772" turns up by chance.
    for personal in ("Nakato", "7720000", "example.com", "CM0000000001"):
        assert personal not in stored

    async with app_database.actor_transaction(staff) as session:
        (event,) = await events_for_resource(session, "migration_trial", view.id)
    assert event.action == "migration.trial_run"
    assert event.details["differing_count"] == 1


async def test_problems_are_kept_with_errors_first(app_database: Database) -> None:
    case, staff = Case(), new_staff()
    await case.trial(app_database, staff, legacy_client_ref="404", principal="50,000,000")
    state = await look(app_database)

    assert state.trial is not None
    assert (state.trial.error_count, state.trial.warning_count) == (1, 1)
    assert [(p.sheet.value, p.row, p.column, p.code) for p in state.trial.problems] == [
        ("investments", 2, "legacy_client_ref", "client_unknown"),
        ("clients", 2, None, "client_without_investments"),
    ]
    assert "import_has_errors" in state.blockers


async def test_the_overview_compares_the_latest_trial_with_the_totals_finance_entered(
    app_database: Database,
) -> None:
    case, staff = Case(), new_staff()
    await case.trial(app_database, staff)
    before = await look(app_database)
    assert before.trials_run >= 1

    await enter(app_database, new_staff(), totals(case.stated, client_count=2))
    state = await look(app_database)
    assert [(line.key, line.old, line.new) for line in state.lines] == [
        ("total:clients", 2, 1),
        ("total:verified_identities", 1, 1),
        ("total:running_investments", 1, 1),
        ("total:pending_orders", 0, 0),
        ("total:invested:UGX", 1_000_000, 1_000_000),
        ("total:interest_before_tax:UGX", case.stated, 11_643),
    ]
    assert state.differing == {
        "total:clients": (2, 1),
        "total:interest_before_tax:UGX": (case.stated, 11_643),
        case.key: case.figures,
    }
    assert state.blockers == ("difference_not_signed_off",)

    # A correction is a new entry, and the latest one is used.
    await enter(app_database, new_staff(), totals(case.stated))
    assert "total:clients" not in (await look(app_database)).differing


async def test_a_sign_off_is_proposed_by_one_person_and_approved_by_another(
    app_database: Database,
) -> None:
    case, proposer, approver = Case(), new_staff(), new_staff()
    await case.trial(app_database, proposer)
    await enter(app_database, proposer, totals(11_643))

    proposed = await propose(app_database, proposer, case.key)
    assert (proposed.figures, proposed.state) == (case.figures, "proposed")
    assert proposed.reason == "Finvest 1.0 counted the start date"
    assert (await look(app_database)).blockers == ("difference_not_signed_off",)

    with pytest.raises(ApplicationError) as error:
        await decide(app_database, proposer, proposed.id)
    assert error.value.code == "forbidden"

    approved = await decide(app_database, approver, proposed.id)
    assert (approved.state, approved.decided_by) == ("approved", approver.id)
    state = await look(app_database)
    assert state.blockers == ()
    assert state.sign_offs[case.key].state == "approved"

    with pytest.raises(ApplicationError) as error:
        await decide(app_database, new_staff(), proposed.id)
    assert error.value.code == "conflict"

    async with app_database.actor_transaction(approver) as session:
        events = await events_for_resource(session, "migration_sign_off", proposed.id)
    assert sorted(event.action for event in events) == [
        "migration.sign_off_approved",
        "migration.sign_off_proposed",
    ]


async def test_a_sign_off_stops_counting_when_the_figures_change(app_database: Database) -> None:
    case, proposer = Case(), new_staff()
    await case.trial(app_database, proposer)
    await enter(app_database, proposer, totals(11_643))
    proposed = await propose(app_database, proposer, case.key)
    await decide(app_database, new_staff(), proposed.id)
    assert (await look(app_database)).blockers == ()

    # The file is exported again and the same investment is now out by ten million more.
    bigger = Case(case.extra + 10_000_000)
    await Case.trial(replace_ref(bigger, case), app_database, proposer)
    state = await look(app_database)
    assert state.differing[case.key][0] == case.stated + 10_000_000
    assert case.key not in state.sign_offs
    assert state.blockers == ("difference_not_signed_off",)


async def test_a_rejected_sign_off_stays_with_its_reason_until_proposed_again(
    app_database: Database,
) -> None:
    case, proposer = Case(), new_staff()
    await case.trial(app_database, proposer)
    proposed = await propose(app_database, proposer, case.key)
    rejected = await decide(app_database, new_staff(), proposed.id, approve=False)
    assert (rejected.state, rejected.decision_reason) == ("rejected", "No")

    # The proposer can read why, and it still blocks the switch.
    state = await look(app_database)
    shown = state.sign_offs[case.key]
    assert (shown.id, shown.state, shown.decision_reason) == (proposed.id, "rejected", "No")
    assert "difference_not_signed_off" in state.blockers

    # Proposing again replaces it, and the new one has no decision yet.
    again = await propose(app_database, proposer, case.key)
    shown = (await look(app_database)).sign_offs[case.key]
    assert (shown.id, shown.state, shown.decision_reason) == (again.id, "proposed", None)
    with pytest.raises(ApplicationError) as error:
        await propose(app_database, proposer, case.key)
    assert error.value.code == "conflict"


def replace_ref(new: Case, old: Case) -> Case:
    new.ref, new.key = old.ref, old.key
    return new


async def test_an_approval_is_refused_when_the_figures_moved_after_the_proposal(
    app_database: Database,
) -> None:
    case, proposer = Case(), new_staff()
    await case.trial(app_database, proposer)
    proposed = await propose(app_database, proposer, case.key)
    await Case.trial(replace_ref(Case(case.extra + 5_000), case), app_database, proposer)

    with pytest.raises(ApplicationError) as error:
        await decide(app_database, new_staff(), proposed.id)
    assert error.value.code == "conflict"
    # It can still be rejected, and then proposed afresh for the new figures.
    rejected = await decide(app_database, new_staff(), proposed.id, approve=False)
    assert rejected.state == "rejected"
    again = await propose(app_database, proposer, case.key)
    assert again.figures[0] == case.stated + 5_000


async def test_what_cannot_be_proposed(app_database: Database) -> None:
    case, staff = Case(), new_staff()
    await case.trial(app_database, staff)

    with pytest.raises(ApplicationError) as error:
        await propose(app_database, staff, "investment:NOT-IN-THE-FILE")
    assert refused(error) == ("validation_failed", [("key", "does_not_differ")])

    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as error:
            await migration.propose_sign_off(session, staff, NOW, key=case.key, reason="   ")
    assert refused(error) == ("validation_failed", [("reason", "invalid_reason")])

    await propose(app_database, staff, case.key)
    with pytest.raises(ApplicationError) as error:
        await propose(app_database, new_staff(), case.key)
    assert error.value.code == "conflict"


async def test_a_trial_cannot_be_as_of_a_day_that_has_not_happened(app_database: Database) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as error:
            await migration.run_trial_import(
                session,
                staff,
                NOW,
                clients_csv=clients(),
                investments_csv=investments(),
                as_of=AS_OF + timedelta(days=1),
            )
    assert refused(error) == ("validation_failed", [("as_of", "in_the_future")])


async def test_two_approvals_at_once_decide_a_sign_off_once(app_database: Database) -> None:
    case, proposer = Case(), new_staff()
    await case.trial(app_database, proposer)
    proposed = await propose(app_database, proposer, case.key)
    barrier = AsyncBarrier(2)

    async def approve() -> str:
        staff = new_staff()
        await barrier.wait()
        try:
            await decide(app_database, staff, proposed.id)
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(approve(), approve())) == ["approved", "conflict"]


# What PostgreSQL holds whatever the service does -----------------------------------------------

_SIGN_OFF = text(
    "INSERT INTO migration_sign_offs (id, key, figures, reason, proposed_by, proposed_at) "
    "VALUES (:id, 'total:clients', ARRAY[2, 1]::bigint[], 'Explained', :by, now())"
)
_DECISION = text(
    "INSERT INTO migration_sign_off_decisions (sign_off_id, outcome, decided_by, decided_at) "
    "VALUES (:id, 'approved', :by, now())"
)


async def test_the_database_refuses_a_proposer_approving_their_own_sign_off(
    app_database: Database,
) -> None:
    proposer, sign_off_id = new_staff(), push_id()
    async with app_database.actor_transaction(proposer) as session:
        await session.execute(_SIGN_OFF, {"id": sign_off_id, "by": proposer.id})
    with pytest.raises(DBAPIError, match="second person"):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(_DECISION, {"id": sign_off_id, "by": proposer.id})

    # Nor can anyone write a decision in another person's name.
    other = new_staff()
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(_DECISION, {"id": sign_off_id, "by": other.id})

    async with app_database.actor_transaction(other) as session:
        await session.execute(_DECISION, {"id": sign_off_id, "by": other.id})
    with pytest.raises(DBAPIError, match="pk_migration_sign_off_decisions"):
        async with app_database.actor_transaction(new_staff()) as session:
            third = (await session.execute(text("SELECT finvest_actor_id()"))).scalar_one()
            await session.execute(_DECISION, {"id": sign_off_id, "by": third})


@pytest.mark.parametrize(
    "table",
    [
        "migration_trials",
        "migration_control_totals",
        "migration_sign_offs",
        "migration_sign_off_decisions",
    ],
)
async def test_nothing_is_changed_or_removed_afterwards(
    application_database: Database, migrator_database: Database, table: str
) -> None:
    actor = SYSTEM if application_database.engine.url.username == "finvest_worker" else new_staff()
    for statement in (f"DELETE FROM {table}", f"TRUNCATE {table}"):  # noqa: S608
        with pytest.raises(DBAPIError, match="permission denied"):
            async with application_database.actor_transaction(actor) as session:
                await session.execute(text(statement))
    # Not even the table owner.
    with pytest.raises(DBAPIError, match="insert-only"):
        async with migrator_database.transaction() as session:
            await session.execute(text(f"TRUNCATE {table} CASCADE"))


async def test_only_staff_read_and_write_the_checks(
    app_database: Database, worker_database: Database
) -> None:
    case, staff = Case(), new_staff()
    await case.trial(app_database, staff)

    async with app_database.actor_transaction(new_client()) as session:
        assert (await session.execute(text("SELECT count(*) FROM migration_trials"))).scalar() == 0
        with pytest.raises(ApplicationError) as error:
            await migration.propose_sign_off(
                session, new_client(), NOW, key=case.key, reason="Mine"
            )
        assert error.value.code == "forbidden"

    # The worker reads the checks for the switch and writes none of them.
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert (await session.execute(text("SELECT count(*) FROM migration_trials"))).scalar()
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(_SIGN_OFF, {"id": push_id(), "by": push_id()})


async def test_totals_count_only_once_a_second_person_confirms_them(
    app_database: Database,
) -> None:
    case, finance = Case(), new_staff()
    await case.trial(app_database, finance)
    entry = await enter(app_database, finance, totals(case.stated), confirm=False)

    state = await look(app_database)
    assert state.totals == entry
    assert state.lines == ()
    assert "totals_not_confirmed" in state.blockers

    with pytest.raises(ApplicationError) as error:
        await confirm_totals(app_database, finance, entry.id)
    assert error.value.code == "forbidden"
    with pytest.raises(ApplicationError) as error:
        await confirm_totals(app_database, new_staff(), push_id())
    assert error.value.code == "not_found"

    second = new_staff()
    confirmed = await confirm_totals(app_database, second, entry.id)
    assert confirmed.confirmed_by == second.id
    state = await look(app_database)
    assert "totals_not_confirmed" not in state.blockers
    # The old system's interest total is the stated figure, so the total line differs.
    assert state.differing["total:interest_before_tax:UGX"] == (case.stated, 11_643)

    with pytest.raises(ApplicationError) as error:
        await confirm_totals(app_database, new_staff(), entry.id)
    assert error.value.code == "conflict"
    async with app_database.actor_transaction(second) as session:
        events = await events_for_resource(session, "migration_control_totals", entry.id)
    assert sorted(event.action for event in events) == [
        "migration.control_totals_confirmed",
        "migration.control_totals_entered",
    ]


async def test_one_person_cannot_make_a_difference_on_the_totals_disappear(
    app_database: Database,
) -> None:
    case, finance = Case(), new_staff()
    await case.trial(app_database, finance)
    await enter(app_database, finance, totals(case.stated))
    assert "total:interest_before_tax:UGX" in (await look(app_database)).differing

    # The same person enters the new system's own figure as what the old system says.
    replaced = await enter(app_database, finance, totals(11_643), confirm=False)
    state = await look(app_database)
    assert state.lines == ()
    assert "totals_not_confirmed" in state.blockers

    # The entry it replaced can no longer be confirmed, and nor can they confirm their own.
    with pytest.raises(ApplicationError) as error:
        await confirm_totals(app_database, finance, replaced.id)
    assert error.value.code == "forbidden"


async def test_only_the_latest_entry_of_totals_can_be_confirmed(app_database: Database) -> None:
    case, finance = Case(), new_staff()
    await case.trial(app_database, finance)
    first = await enter(app_database, finance, totals(case.stated), confirm=False)
    await enter(app_database, new_staff(), totals(11_643), confirm=False)

    with pytest.raises(ApplicationError) as error:
        await confirm_totals(app_database, new_staff(), first.id)
    assert error.value.code == "conflict"


async def test_two_people_confirming_at_once_leave_one_confirmation(
    app_database: Database,
) -> None:
    case, finance = Case(), new_staff()
    await case.trial(app_database, finance)
    entry = await enter(app_database, finance, totals(case.stated), confirm=False)
    barrier = AsyncBarrier(2)

    async def confirm() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await barrier.wait()
                await migration.confirm_control_totals(session, staff, NOW, entry.id)
        except ApplicationError as error:
            return error.code
        return "confirmed"

    assert sorted(await asyncio.gather(confirm(), confirm())) == ["confirmed", "conflict"]


async def test_the_database_holds_a_confirmation_of_totals_to_a_second_person(
    app_database: Database,
) -> None:
    case, finance, other = Case(), new_staff(), new_staff()
    await case.trial(app_database, finance)
    entry = await enter(app_database, finance, totals(case.stated), confirm=False)
    insert = text(
        "INSERT INTO migration_control_totals_confirmations (totals_id, confirmed_by, "
        "confirmed_at) VALUES (:id, :by, now())"
    )

    with pytest.raises(DBAPIError, match="confirmed by a second person"):
        async with app_database.actor_transaction(finance) as session:
            await session.execute(insert, {"id": entry.id, "by": finance.id})
    # Nobody confirms in another person's name.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(finance) as session:
            await session.execute(insert, {"id": entry.id, "by": other.id})
    await confirm_totals(app_database, other, entry.id)
    with pytest.raises(DBAPIError, match="pk_migration_control_totals_confirmations"):
        third = new_staff()
        async with app_database.actor_transaction(third) as session:
            await session.execute(insert, {"id": entry.id, "by": third.id})
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(
                text("DELETE FROM migration_control_totals_confirmations WHERE totals_id = :id"),
                {"id": entry.id},
            )


async def test_the_history_lists_each_days_trial_for_a_parallel_run(app_database: Database) -> None:
    # Days before AS_OF, which every other test uses, so their trials are not in the range.
    first, second = AS_OF - timedelta(days=2), AS_OF - timedelta(days=1)
    staff, checker = new_staff(), new_staff()

    async def trial_on(day: date, stated: int) -> None:
        row = investment("HIST-1", "1", interest_before_tax=str(stated))
        clock = FakeClock(datetime.combine(second, datetime.min.time(), UTC) + timedelta(hours=9))
        async with app_database.actor_transaction(staff) as session:
            await migration.run_trial_import(
                session,
                staff,
                clock,
                clients_csv=clients(client("1")),
                investments_csv=investments(row),
                as_of=day,
            )

    await trial_on(first, 11_000)
    await trial_on(second, 11_000)
    async with app_database.actor_transaction(staff) as session:
        entry = await migration.enter_control_totals(
            session, staff, NOW, as_of=second, totals=totals(11_000)
        )
    await confirm_totals(app_database, checker, entry.id)

    async with app_database.actor_transaction(staff) as session:
        history = await migration.trial_history(session, date_from=first, date_to=second)
        earlier = await migration.trial_history(
            session, date_from=first - timedelta(days=30), date_to=first - timedelta(days=1)
        )
    assert [(t.as_of, t.totals_confirmed, t.totals_differing) for t in history] == [
        (first, False, None),
        (second, True, 1),
    ]
    assert [(t.error_count, t.investments_differing) for t in history] == [(0, 1), (0, 1)]
    assert history[0].clients_sha256 == history[1].clients_sha256
    assert history[0].investments_sha256 == history[1].investments_sha256
    assert earlier == ()

    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await migration.trial_history(session, date_from=second, date_to=first)
    assert refused(error) == ("validation_failed", [("date_from", "after_date_to")])
