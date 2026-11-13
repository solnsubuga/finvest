"""Group goals against real PostgreSQL: setting one up, inviting, joining, leaving, the combined
value the worker records, and the fences the database holds."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.goals import groups
from app.modules.goals import service as goals
from app.modules.goals.domain.goals import GoalCategory, GoalStatus, Standing
from app.modules.goals.domain.groups import MAX_MEMBERS, GroupStatus, invite_hash
from app.modules.goals.domain.plans import add_months
from app.modules.investments import service as investments
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Money
from tests.support.clock import FakeClock
from tests.support.investments import PRESENT, approved_client, pay, today
from tests.support.ledger import client as new_client
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

# The database refuses an investment that is not created now, so these run at the real time.
DAY = today()
NOW = PRESENT
YEAR_ON = add_months(DAY, 12)


async def new_group(
    database: Database,
    owner: Actor,
    *,
    target: Money | None = None,
    own_target: Money | None = None,
    display_name: str = "Grace",
    idempotency_key: str | None = None,
) -> groups.ClientGroup:
    async with database.actor_transaction(owner) as session:
        return await groups.create_group(
            session,
            owner,
            idempotency_key=idempotency_key or key(),
            name="Land in Mukono",
            category=GoalCategory.LAND_OR_HOME,
            target=target or ugx(10_000_000),
            target_date=YEAR_ON,
            display_name=display_name,
            own_target=own_target or ugx(5_000_000),
            clock=NOW,
        )


async def invite(database: Database, owner: Actor, group_id: str) -> str:
    async with database.actor_transaction(owner) as session:
        return (await groups.new_invite(session, owner, group_id, NOW)).code


async def join(
    database: Database,
    member: Actor,
    code: str,
    *,
    display_name: str = "Daniel",
    clock: FakeClock = NOW,
) -> groups.ClientGroup:
    async with database.actor_transaction(member) as session:
        return await groups.join_group(
            session,
            member,
            code,
            idempotency_key=key(),
            display_name=display_name,
            own_target=ugx(5_000_000),
            clock=clock,
        )


async def read(database: Database, client: Actor, group_id: str) -> groups.ClientGroup:
    async with database.actor_transaction(client) as session:
        return await groups.client_group(session, client, group_id, NOW)


async def refused(call: object) -> str:
    with pytest.raises(ApplicationError) as error:
        await call  # type: ignore[misc]
    return error.value.code


async def started_saving(
    app_database: Database, worker_database: Database, client: Actor, goal_id: str, amount: Money
) -> None:
    async with app_database.actor_transaction(client) as session:
        saving = await goals.save_to_goal(
            session, client, goal_id, idempotency_key=key(), amount=amount, clock=NOW
        )
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, saving.id)
    await pay(worker_database, investment, amount, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, saving.id, confirmed_on=DAY, clock=NOW)


# Setting up and joining -------------------------------------------------------------------------


async def test_the_one_who_sets_a_group_up_is_its_first_member_with_a_goal_of_their_own(
    app_database: Database,
) -> None:
    owner = new_client()
    group = await new_group(app_database, owner)
    assert group.is_owner
    assert group.status is GroupStatus.ACTIVE
    assert [(m.display_name, m.is_you, m.is_owner) for m in group.members] == [
        ("Grace", True, True)
    ]
    assert group.progress.value == ugx(0)
    assert group.progress.standing is Standing.ON_TRACK
    own = await _goal(app_database, owner, group.own_goal_id)
    assert (own.name, own.target, own.target_date) == ("Land in Mukono", ugx(5_000_000), YEAR_ON)


async def test_a_retry_returns_the_same_group(app_database: Database) -> None:
    owner = new_client()
    same = key()
    first = await new_group(app_database, owner, idempotency_key=same)
    second = await new_group(app_database, owner, idempotency_key=same)
    assert first.id == second.id
    async with app_database.actor_transaction(owner) as session:
        assert [g.id for g in await groups.client_groups(session, owner, NOW)] == [first.id]


async def test_a_bad_name_to_be_shown_by_is_refused(app_database: Database) -> None:
    assert await refused(new_group(app_database, new_client(), display_name="   ")) == (
        "validation_failed"
    )
    assert await refused(new_group(app_database, new_client(), display_name="x" * 31)) == (
        "validation_failed"
    )


async def test_a_client_joins_with_the_invite_and_sees_names_but_no_ones_savings(
    app_database: Database,
) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    code = await invite(app_database, owner, group.id)

    async with app_database.actor_transaction(member) as session:
        preview = await groups.preview_invite(session, member, code, NOW)
    assert (preview.name, preview.owner_name, preview.members) == ("Land in Mukono", "Grace", 1)

    joined = await join(app_database, member, code)
    assert not joined.is_owner
    assert [(m.display_name, m.is_you, m.is_owner) for m in joined.members] == [
        ("Grace", False, True),
        ("Daniel", True, False),
    ]
    assert joined.invite_expires_at is None
    assert (await read(app_database, owner, group.id)).invite_expires_at is not None
    own = await _goal(app_database, member, joined.own_goal_id)
    assert own.target == ugx(5_000_000)


async def test_an_invite_that_is_wrong_replaced_or_out_of_date_is_not_found(
    app_database: Database,
) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    old = await invite(app_database, owner, group.id)
    new = await invite(app_database, owner, group.id)
    assert await refused(join(app_database, member, old)) == "not_found"
    assert await refused(join(app_database, member, "A" * 22)) == "not_found"
    later = FakeClock(NOW.now() + timedelta(days=8))
    assert await refused(join(app_database, member, new, clock=later)) == "not_found"
    async with app_database.actor_transaction(owner) as session:
        await groups.revoke_invite(session, owner, group.id, NOW)
    assert await refused(join(app_database, member, new)) == "not_found"


async def test_a_client_is_in_a_group_once(app_database: Database) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    code = await invite(app_database, owner, group.id)
    await join(app_database, member, code)
    assert await refused(join(app_database, member, code)) == "conflict"
    assert await refused(join(app_database, owner, code)) == "conflict"


async def test_a_group_is_full_at_ten_even_when_joined_at_once(app_database: Database) -> None:
    owner = new_client()
    group = await new_group(app_database, owner)
    code = await invite(app_database, owner, group.id)
    for _ in range(MAX_MEMBERS - 3):
        await join(app_database, new_client(), code)
    # Two places are left and four ask at the same moment.
    outcomes = await asyncio.gather(
        *(join(app_database, new_client(), code) for _ in range(4)), return_exceptions=True
    )
    assert sum(1 for o in outcomes if isinstance(o, groups.ClientGroup)) == 2
    assert {o.code for o in outcomes if isinstance(o, ApplicationError)} == {"conflict"}
    assert len((await read(app_database, owner, group.id)).members) == MAX_MEMBERS
    async with app_database.actor_transaction(owner) as session:
        with pytest.raises(ApplicationError) as error:
            await groups.new_invite(session, owner, group.id, NOW)
    assert error.value.code == "conflict"


# What members may do ----------------------------------------------------------------------------


async def test_only_the_one_who_set_it_up_changes_invites_removes_or_closes(
    app_database: Database,
) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    joined = await join(app_database, member, await invite(app_database, owner, group.id))
    async with app_database.actor_transaction(member) as session:
        for call in (
            groups.new_invite(session, member, group.id, NOW),
            groups.close_group(session, member, group.id, NOW),
            groups.remove_member(session, member, group.id, joined.own_goal_id, NOW),
            groups.update_group(
                session,
                member,
                group.id,
                name="Mine now",
                category=GoalCategory.OTHER,
                target=ugx(1),
                target_date=None,
                clock=NOW,
            ),
        ):
            assert await refused(call) == "not_found"


async def test_the_owner_changes_the_group_and_no_members_goal_changes(
    app_database: Database,
) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    joined = await join(app_database, member, await invite(app_database, owner, group.id))
    async with app_database.actor_transaction(owner) as session:
        changed = await groups.update_group(
            session,
            owner,
            group.id,
            name="Land in Wakiso",
            category=GoalCategory.LAND_OR_HOME,
            target=ugx(12_000_000),
            target_date=None,
            clock=NOW,
        )
    assert (changed.name, changed.target, changed.target_date) == (
        "Land in Wakiso",
        ugx(12_000_000),
        None,
    )
    assert changed.progress.standing is Standing.NO_DATE
    mine = await _goal(app_database, member, joined.own_goal_id)
    assert (mine.name, mine.target_date) == ("Land in Mukono", YEAR_ON)


async def test_a_member_renames_themself_and_leaves_and_keeps_their_goal(
    app_database: Database,
) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    joined = await join(app_database, member, await invite(app_database, owner, group.id))
    async with app_database.actor_transaction(member) as session:
        renamed = await groups.rename_self(
            session, member, group.id, display_name="  Dan   O. ", clock=NOW
        )
    assert [m.display_name for m in renamed.members] == ["Grace", "Dan O."]
    async with app_database.actor_transaction(member) as session:
        await groups.leave_group(session, member, group.id, NOW)
    assert await refused(read(app_database, member, group.id)) == "not_found"
    assert [m.display_name for m in (await read(app_database, owner, group.id)).members] == [
        "Grace"
    ]
    assert (await _goal(app_database, member, joined.own_goal_id)).status is GoalStatus.ACTIVE


async def test_the_owner_removes_a_member_but_never_leaves(app_database: Database) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    code = await invite(app_database, owner, group.id)
    joined = await join(app_database, member, code)
    async with app_database.actor_transaction(owner) as session:
        assert await refused(groups.leave_group(session, owner, group.id, NOW)) == "conflict"
    async with app_database.actor_transaction(owner) as session:
        assert (
            await refused(groups.remove_member(session, owner, group.id, group.own_goal_id, NOW))
            == "conflict"
        )
    # The owner sees a member id for the others only; the member sees none at all.
    seen = await read(app_database, owner, group.id)
    assert [m.member_id for m in seen.members] == [None, joined.own_goal_id]
    assert [m.member_id for m in joined.members] == [None, None]
    async with app_database.actor_transaction(owner) as session:
        removed = await groups.remove_member(session, owner, group.id, joined.own_goal_id, NOW)
    assert len(removed.members) == 1
    assert removed.invite_expires_at is None
    assert await refused(read(app_database, member, group.id)) == "not_found"
    # The link they had no longer works.
    assert await refused(join(app_database, member, code)) == "not_found"


async def test_closing_a_members_goal_leaves_the_group_and_the_owners_is_refused(
    app_database: Database,
) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    joined = await join(app_database, member, await invite(app_database, owner, group.id))
    async with app_database.actor_transaction(member) as session:
        await goals.close_goal(session, member, joined.own_goal_id, NOW)
    assert len((await read(app_database, owner, group.id)).members) == 1
    async with app_database.actor_transaction(owner) as session:
        assert await refused(goals.close_goal(session, owner, group.own_goal_id, NOW)) == (
            "conflict"
        )


async def test_closing_a_group_keeps_every_goal_and_takes_nobody_new(
    app_database: Database,
) -> None:
    owner, member, late = new_client(), new_client(), new_client()
    group = await new_group(app_database, owner)
    code = await invite(app_database, owner, group.id)
    joined = await join(app_database, member, code)
    async with app_database.actor_transaction(owner) as session:
        closed = await groups.close_group(session, owner, group.id, NOW)
        again = await groups.close_group(session, owner, group.id, NOW)
    assert closed.status is again.status is GroupStatus.CLOSED
    assert closed.invite_expires_at is None
    assert await refused(join(app_database, late, code)) == "not_found"
    for client, goal_id in ((owner, group.own_goal_id), (member, joined.own_goal_id)):
        assert (await _goal(app_database, client, goal_id)).status is GoalStatus.ACTIVE
    # Once the group is closed, the owner may close their own goal too.
    async with app_database.actor_transaction(owner) as session:
        await goals.close_goal(session, owner, group.own_goal_id, NOW)


async def test_an_outsider_never_reaches_a_group(app_database: Database) -> None:
    owner, outsider = new_client(), new_client()
    group = await new_group(app_database, owner)
    assert await refused(read(app_database, outsider, group.id)) == "not_found"
    async with app_database.actor_transaction(outsider) as session:
        rows = await session.execute(text("SELECT count(*) FROM goal_groups"))
        assert rows.scalar_one() == 0
        rows = await session.execute(text("SELECT count(*) FROM goal_group_members"))
        assert rows.scalar_one() == 0


# The combined value -----------------------------------------------------------------------------


async def test_the_group_counts_each_members_running_investments_as_the_worker_records_them(
    app_database: Database, worker_database: Database
) -> None:
    owner = await approved_client(app_database, NOW)
    member = await approved_client(app_database, NOW)
    group = await new_group(app_database, owner)
    joined = await join(app_database, member, await invite(app_database, owner, group.id))
    await started_saving(app_database, worker_database, owner, group.own_goal_id, ugx(1_000_000))
    await started_saving(app_database, worker_database, member, joined.own_goal_id, ugx(500_000))

    # Each sees their own part now, and the other's only once the worker has recorded it.
    assert (await read(app_database, owner, group.id)).progress.value == ugx(1_000_000)
    assert (await read(app_database, member, group.id)).others_as_of is None

    async with worker_database.actor_transaction(SYSTEM) as session:
        # Members of other tests' groups are recorded too: the database is shared.
        assert await groups.refresh_values(session, NOW) >= 2
    async with worker_database.actor_transaction(SYSTEM) as session:
        # The same day again records nothing more.
        assert await groups.refresh_values(session, NOW) == 0

    for client in (owner, member):
        seen = await read(app_database, client, group.id)
        assert seen.progress.value == ugx(1_500_000)
        assert seen.progress.percent == 15
        assert seen.others_as_of == DAY

    async with app_database.actor_transaction(new_staff()) as session:
        found = await groups.staff_group(session, new_staff(), group.id, NOW)
    assert sorted(m.value.minor for m in found.members) == [500_000, 1_000_000]
    assert found.progress.value == ugx(1_500_000)


async def test_a_member_who_left_is_no_longer_counted(
    app_database: Database, worker_database: Database
) -> None:
    owner = await approved_client(app_database, NOW)
    member = await approved_client(app_database, NOW)
    group = await new_group(app_database, owner)
    joined = await join(app_database, member, await invite(app_database, owner, group.id))
    await started_saving(app_database, worker_database, member, joined.own_goal_id, ugx(500_000))
    async with worker_database.actor_transaction(SYSTEM) as session:
        await groups.refresh_values(session, NOW)
    assert (await read(app_database, owner, group.id)).progress.value == ugx(500_000)
    async with app_database.actor_transaction(member) as session:
        await groups.leave_group(session, member, group.id, NOW)
    assert (await read(app_database, owner, group.id)).progress.value == ugx(0)


# What the database enforces ---------------------------------------------------------------------


async def test_a_client_never_writes_the_figure_the_group_is_shown(
    app_database: Database,
) -> None:
    owner = new_client()
    group = await new_group(app_database, owner)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(owner) as session:
            await session.execute(
                text("UPDATE goal_group_members SET value_minor = 99 WHERE goal_id = :id"),
                {"id": group.own_goal_id},
            )


async def test_a_member_never_renames_another(app_database: Database) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    joined = await join(app_database, member, await invite(app_database, owner, group.id))
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(owner) as session:
            await session.execute(
                text("UPDATE goal_group_members SET display_name = 'X' WHERE goal_id = :id"),
                {"id": joined.own_goal_id},
            )


async def test_joining_without_the_invite_is_refused(app_database: Database) -> None:
    owner, member = new_client(), new_client()
    group = await new_group(app_database, owner)
    own = await _new_goal(app_database, member)
    for joined_with in (None, invite_hash("A" * 22)):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(member) as session:
                await session.execute(
                    text(
                        "INSERT INTO goal_group_members (goal_id, group_id, client_id, "
                        "display_name, joined_with, joined_at) "
                        "VALUES (:goal_id, :group_id, :client_id, 'Me', :joined_with, now())"
                    ),
                    {
                        "goal_id": own,
                        "group_id": group.id,
                        "client_id": member.id,
                        "joined_with": joined_with,
                    },
                )


async def test_the_owner_never_leaves_an_active_group_and_nothing_is_deleted(
    app_database: Database, migrator_database: Database
) -> None:
    owner = new_client()
    group = await new_group(app_database, owner)
    for statement in (
        "UPDATE goal_group_members SET left_at = now() WHERE goal_id = :goal_id",
        "DELETE FROM goal_group_members WHERE goal_id = :goal_id",
        "DELETE FROM goal_groups WHERE id = :group_id",
        "UPDATE goal_groups SET owner_client_id = :goal_id WHERE id = :group_id",
    ):
        with pytest.raises(DBAPIError):
            async with migrator_database.transaction() as session:
                await session.execute(
                    text(statement), {"goal_id": group.own_goal_id, "group_id": group.id}
                )


async def test_a_closed_group_never_changes(
    app_database: Database, migrator_database: Database
) -> None:
    owner = new_client()
    group = await new_group(app_database, owner)
    async with app_database.actor_transaction(owner) as session:
        await groups.close_group(session, owner, group.id, NOW)
    with pytest.raises(DBAPIError):
        async with migrator_database.transaction() as session:
            await session.execute(
                text("UPDATE goal_groups SET status = 'active', closed_at = NULL WHERE id = :id"),
                {"id": group.id},
            )


async def test_staff_read_groups_and_change_none(app_database: Database) -> None:
    owner, staff = new_client(), new_staff()
    group = await new_group(app_database, owner)
    async with app_database.actor_transaction(staff) as session:
        assert group.id in [g.id for g in await groups.list_groups(session, staff)]
    async with app_database.actor_transaction(staff) as session:
        changed = await session.execute(
            text("UPDATE goal_groups SET name = 'Staff' WHERE id = :id"), {"id": group.id}
        )
        # No policy lets staff change a group, so no row is reached.
        assert changed.rowcount == 0  # type: ignore[attr-defined]
    assert (await read(app_database, owner, group.id)).name == "Land in Mukono"


# Helpers ----------------------------------------------------------------------------------------


async def _goal(database: Database, client: Actor, goal_id: str) -> goals.ClientGoal:
    async with database.actor_transaction(client) as session:
        return await goals.client_goal(session, client, goal_id, NOW)


async def _new_goal(database: Database, client: Actor) -> str:
    async with database.actor_transaction(client) as session:
        made = await goals.create_goal(
            session,
            client,
            idempotency_key=key(),
            name="Mine",
            category=GoalCategory.OTHER,
            target=ugx(1_000_000),
            target_date=YEAR_ON,
            clock=NOW,
        )
    return made.id
