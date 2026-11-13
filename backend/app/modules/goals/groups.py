"""Group goal commands and queries. Every function works inside the caller's transaction.

A group goal moves no money. Each member saves into a goal of their own, through
`service.save_to_goal`, and those savings are their own investments. The group keeps the shared
target, who is in it and, for each member, what their goal was worth when the worker last
looked. The policies and guards of migration 0112 hold whatever this code does.

What a member sees of the others: the name each chose to be shown by, when they joined, and the
group's combined value. Never another member's savings, amounts or investments one by one.
"""

import secrets
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.goals import service
from app.modules.goals.domain.goals import GoalCategory
from app.modules.goals.domain.groups import (
    MAX_MEMBERS,
    GroupProgress,
    GroupStatus,
    check_display_name,
    clean_display_name,
    group_progress,
    invite_expires,
    invite_hash,
    invite_open,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

CREATE_OPERATION = "goals.create_group"
JOIN_OPERATION = "goals.join_group"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# Bytes of randomness in an invite code: 128 bits, so a code cannot be guessed.
INVITE_BYTES = 16
REFRESH_BATCH = 200
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Group:
    """A group goal as staff and the worker see it."""

    id: str
    owner_client_id: str
    name: str
    category: GoalCategory
    target: Money
    target_date: date | None
    status: GroupStatus
    invite_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class Member:
    goal_id: str
    client_id: str
    display_name: str
    joined_at: datetime
    left_at: datetime | None
    value: Money
    value_on: date | None


@dataclass(frozen=True, slots=True)
class ClientMember:
    """Another member as a member sees them: a name and when they joined. Nothing they saved."""

    display_name: str
    joined_at: datetime
    is_you: bool
    is_owner: bool


@dataclass(frozen=True, slots=True)
class ClientGroup:
    """What a member sees of a group goal. Every figure is after tax."""

    id: str
    name: str
    category: GoalCategory
    target: Money
    target_date: date | None
    status: GroupStatus
    created_at: datetime
    is_owner: bool
    # The member's own goal in the group, where they save.
    own_goal_id: str
    # The member's own goal counted as it is now, everyone else's as of the worker's last look.
    progress: GroupProgress
    # The oldest of the other members' figures, or None when there are no others yet.
    others_as_of: date | None
    members: list[ClientMember]
    # Only for the one who set the group up. The code itself is shown once, when made.
    invite_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ClientInvite:
    """What someone holding an invite sees before they join."""

    group_id: str
    name: str
    category: GoalCategory
    target: Money
    target_date: date | None
    owner_name: str
    members: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NewInvite:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StaffGroup:
    group: Group
    progress: GroupProgress
    members: list[Member]


_COLUMNS = (
    "id, owner_client_id, name, category, currency, target_minor, target_date, status, "
    "invite_expires_at, created_at, updated_at, closed_at"
)
_MEMBER_COLUMNS = "goal_id, client_id, display_name, joined_at, left_at, value_minor, value_on"
_BY_ID = text(f"SELECT {_COLUMNS} FROM goal_groups WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} FROM goal_groups WHERE id = :id FOR UPDATE")  # noqa: S608
_FOR_STAFF = text(
    f"SELECT {_COLUMNS} FROM goal_groups "  # noqa: S608
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)
_MEMBERS = text(
    f"SELECT {_MEMBER_COLUMNS} FROM goal_group_members "  # noqa: S608
    "WHERE group_id = :group_id ORDER BY joined_at, goal_id"
)
_CURRENT = text(
    "SELECT m.group_id, m.goal_id FROM goal_group_members m "
    "JOIN goal_groups g ON g.id = m.group_id "
    "WHERE m.client_id = :client_id AND m.left_at IS NULL "
    "ORDER BY g.status, m.joined_at DESC, m.group_id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO goal_groups (id, owner_client_id, name, category, currency, target_minor, "
    "target_date, created_at, updated_at) VALUES (:id, :owner, :name, :category, :currency, "
    ":target_minor, :target_date, :now, :now)"
)
_INSERT_MEMBER = text(
    "INSERT INTO goal_group_members (goal_id, group_id, client_id, display_name, joined_with, "
    "joined_at) VALUES (:goal_id, :group_id, :client_id, :display_name, :joined_with, :now)"
)
_UPDATE = text(
    "UPDATE goal_groups SET name = :name, category = :category, target_minor = :target_minor, "
    "target_date = :target_date, updated_at = :now WHERE id = :id"
)
_SET_INVITE = text(
    "UPDATE goal_groups SET invite_hash = :hash, invite_expires_at = :expires_at, "
    "updated_at = :now WHERE id = :id"
)
_CLOSE = text(
    "UPDATE goal_groups SET status = 'closed', closed_at = :now, updated_at = :now, "
    "invite_hash = NULL, invite_expires_at = NULL WHERE id = :id"
)
_LEAVE = text(
    "UPDATE goal_group_members SET left_at = :now "
    "WHERE goal_id = :goal_id AND group_id = :group_id AND left_at IS NULL"
)
_RENAME = text(
    "UPDATE goal_group_members SET display_name = :display_name "
    "WHERE goal_id = :goal_id AND client_id = :client_id AND left_at IS NULL"
)
_INVITE = text(
    "SELECT id, name, category, currency, target_minor, target_date, status, "
    "invite_expires_at, owner_name, members FROM finvest_goal_group_invite(:hash)"
)
_TO_REFRESH = text(
    "SELECT m.goal_id FROM goal_group_members m JOIN goal_groups g ON g.id = m.group_id "
    "WHERE m.left_at IS NULL AND g.status = 'active' "
    "AND (m.value_on IS NULL OR m.value_on < :today) "
    "ORDER BY m.goal_id LIMIT :limit"
)
_SET_VALUE = text(
    "UPDATE goal_group_members SET value_minor = :value_minor, value_on = :today "
    "WHERE goal_id = :goal_id AND left_at IS NULL"
)
_REFUSED = "42501"


# The client's commands --------------------------------------------------------------------------


async def create_group(
    session: AsyncSession,
    client: Actor,
    *,
    idempotency_key: str,
    name: str,
    category: GoalCategory,
    target: Money,
    target_date: date | None,
    display_name: str,
    own_target: Money,
    clock: Clock,
) -> ClientGroup:
    """Set up a group goal, with the client as its first member.

    The client's own part is a goal of their own, with `own_target` as its target and the
    group's date, which they save into as into any goal. Nothing is paid and nothing moves. A
    retry with the same key returns the same group.
    """
    client_id = _client_id(client)

    async def command() -> Receipt:
        shown_as = _checked_display_name(display_name)
        _same_currency(target, own_target)
        # Checks the name, target and date as for any goal, and holds the client's goal lock.
        goal_id = await service.open_goal(
            session,
            client_id,
            name=name,
            category=category,
            target=own_target,
            target_date=target_date,
            clock=clock,
        )
        group_id = push_id()
        now = clock.now()
        await session.execute(
            _INSERT,
            {
                "id": group_id,
                "owner": client_id,
                "name": " ".join(name.split()),
                "category": category.value,
                "currency": target.currency.value,
                "target_minor": target.minor,
                "target_date": target_date,
                "now": now,
            },
        )
        await session.execute(
            _INSERT_MEMBER,
            {
                "goal_id": goal_id,
                "group_id": group_id,
                "client_id": client_id,
                "display_name": shown_as,
                "joined_with": None,
                "now": now,
            },
        )
        logger.info("goal_group_created", group_id=group_id, category=category.value)
        return Receipt(201, {"group_id": group_id})

    outcome = await run_once(
        session,
        actor=client,
        operation=CREATE_OPERATION,
        key=idempotency_key,
        request={
            "name": name,
            "category": category.value,
            "target_minor": target.minor,
            "currency": target.currency.value,
            "target_date": target_date.isoformat() if target_date else None,
            "display_name": display_name,
            "own_target_minor": own_target.minor,
        },
        now=clock.now(),
        command=command,
    )
    return await client_group(session, client, str(outcome.receipt.response["group_id"]), clock)


async def update_group(
    session: AsyncSession,
    client: Actor,
    group_id: str,
    *,
    name: str,
    category: GoalCategory,
    target: Money,
    target_date: date | None,
    clock: Clock,
) -> ClientGroup:
    """Change the group's name, target or date. Only its owner may. No member's own goal
    changes: each member changes theirs, if they want to."""
    group = await _owned(session, client, group_id)
    if target.currency is not group.target.currency:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["currency"], code="unchanged")]
        )
    cleaned = service.check_goal_details(name, target, target_date, clock)
    await session.execute(
        _UPDATE,
        {
            "id": group.id,
            "name": cleaned,
            "category": category.value,
            "target_minor": target.minor,
            "target_date": target_date,
            "now": clock.now(),
        },
    )
    return await client_group(session, client, group.id, clock)


async def new_invite(
    session: AsyncSession, client: Actor, group_id: str, clock: Clock
) -> NewInvite:
    """A new invite code for the group, replacing any earlier one, which stops working.

    The code is returned once and only its hash is kept, so it cannot be shown again.
    """
    group = await _owned(session, client, group_id)
    members = await _members(session, group)
    if sum(1 for m in members if m.left_at is None) >= MAX_MEMBERS:
        raise ApplicationError("conflict")
    code = secrets.token_urlsafe(INVITE_BYTES)
    now = clock.now()
    expires_at = invite_expires(now)
    await session.execute(
        _SET_INVITE,
        {"id": group.id, "hash": invite_hash(code), "expires_at": expires_at, "now": now},
    )
    logger.info("goal_group_invite_made", group_id=group.id)
    return NewInvite(code=code, expires_at=expires_at)


async def revoke_invite(
    session: AsyncSession, client: Actor, group_id: str, clock: Clock
) -> ClientGroup:
    group = await _owned(session, client, group_id)
    await session.execute(
        _SET_INVITE, {"id": group.id, "hash": None, "expires_at": None, "now": clock.now()}
    )
    return await client_group(session, client, group.id, clock)


async def preview_invite(
    session: AsyncSession, client: Actor, code: str, clock: Clock
) -> ClientInvite:
    """The group an invite is for, before joining. A code that is wrong, replaced or out of
    date, or for a closed group, is not found: the answer never says which."""
    _client_id(client)
    row = await _invite_row(session, code, clock)
    return ClientInvite(
        group_id=row.id,
        name=row.name,
        category=GoalCategory(row.category),
        target=Money(row.target_minor, Currency(row.currency)),
        target_date=row.target_date,
        owner_name=row.owner_name or "",
        members=int(row.members),
        expires_at=row.invite_expires_at,
    )


async def join_group(
    session: AsyncSession,
    client: Actor,
    code: str,
    *,
    idempotency_key: str,
    display_name: str,
    own_target: Money,
    clock: Clock,
) -> ClientGroup:
    """Join a group goal with its invite.

    The client's part is a new goal of their own, named after the group, with `own_target` as
    its target and the group's date. A retry with the same key returns the same group. A client
    already in the group, or a full group, is a conflict.
    """
    client_id = _client_id(client)

    async def command() -> Receipt:
        shown_as = _checked_display_name(display_name)
        row = await _invite_row(session, code, clock)
        currency = Currency(row.currency)
        if own_target.currency is not currency:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["currency"], code="not_group_currency")],
            )
        if int(row.members) >= MAX_MEMBERS:
            raise ApplicationError("conflict")
        goal_id = await service.open_goal(
            session,
            client_id,
            name=row.name,
            category=GoalCategory(row.category),
            target=own_target,
            target_date=row.target_date,
            clock=clock,
        )
        try:
            # A savepoint, so a refusal leaves the transaction usable for the error response.
            async with session.begin_nested():
                await session.execute(
                    _INSERT_MEMBER,
                    {
                        "goal_id": goal_id,
                        "group_id": row.id,
                        "client_id": client_id,
                        "display_name": shown_as,
                        "joined_with": invite_hash(code),
                        "now": clock.now(),
                    },
                )
        except IntegrityError:
            # Already a member: one place in a group at a time.
            raise ApplicationError("conflict") from None
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != _REFUSED:
                raise
            # Full, closed, or the invite replaced in between.
            raise ApplicationError("conflict") from None
        logger.info("goal_group_joined", group_id=row.id)
        return Receipt(201, {"group_id": row.id})

    outcome = await run_once(
        session,
        actor=client,
        operation=JOIN_OPERATION,
        key=idempotency_key,
        request={
            "code": invite_hash(code).hex(),
            "display_name": display_name,
            "own_target_minor": own_target.minor,
            "currency": own_target.currency.value,
        },
        now=clock.now(),
        command=command,
    )
    return await client_group(session, client, str(outcome.receipt.response["group_id"]), clock)


async def rename_self(
    session: AsyncSession, client: Actor, group_id: str, *, display_name: str, clock: Clock
) -> ClientGroup:
    """Change the name the other members see this client by."""
    client_id = _client_id(client)
    group = await _visible(session, client, group_id)
    if group.status is not GroupStatus.ACTIVE:
        raise ApplicationError("conflict")
    own = await _own_membership(session, client, group)
    await session.execute(
        _RENAME,
        {
            "goal_id": own.goal_id,
            "client_id": client_id,
            "display_name": _checked_display_name(display_name),
        },
    )
    return await client_group(session, client, group_id, clock)


async def leave_group(session: AsyncSession, client: Actor, group_id: str, clock: Clock) -> None:
    """Leave a group goal. The client's own goal and its savings carry on as their own.

    The one who set the group up closes it instead.
    """
    group = await _visible(session, client, group_id)
    if group.owner_client_id == client.id:
        raise ApplicationError("conflict")
    own = await _own_membership(session, client, group)
    await session.execute(
        _LEAVE, {"goal_id": own.goal_id, "group_id": group.id, "now": clock.now()}
    )
    logger.info("goal_group_left", group_id=group.id)


async def remove_member(
    session: AsyncSession, client: Actor, group_id: str, member_goal_id: str, clock: Clock
) -> ClientGroup:
    """Take a member out of the group. Only the owner may, and never themselves. The member's
    own goal and its savings carry on as theirs. The current invite is withdrawn too, so the
    member cannot rejoin with the link they had."""
    group = await _owned(session, client, group_id)
    members = {m.goal_id: m for m in await _members(session, group) if m.left_at is None}
    member = members.get(member_goal_id)
    if member is None:
        raise ApplicationError("not_found")
    if member.client_id == group.owner_client_id:
        raise ApplicationError("conflict")
    now = clock.now()
    await session.execute(_LEAVE, {"goal_id": member.goal_id, "group_id": group.id, "now": now})
    # The removed member may still hold the link, so it stops working.
    await session.execute(
        _SET_INVITE, {"id": group.id, "hash": None, "expires_at": None, "now": now}
    )
    logger.info("goal_group_member_removed", group_id=group.id)
    return await client_group(session, client, group.id, clock)


async def close_group(
    session: AsyncSession, client: Actor, group_id: str, clock: Clock
) -> ClientGroup:
    """Stop a group goal. Final. Every member's goal carries on as their own, still open, and
    nobody's money moves. Closing a closed group again changes nothing."""
    group = await _visible(session, client, group_id, lock=True)
    if group.owner_client_id != client.id:
        raise ApplicationError("not_found")
    if group.status is GroupStatus.ACTIVE:
        await session.execute(_CLOSE, {"id": group.id, "now": clock.now()})
        logger.info("goal_group_closed", group_id=group.id)
    return await client_group(session, client, group.id, clock)


# Queries ----------------------------------------------------------------------------------------


async def client_groups(
    session: AsyncSession, client: Actor, clock: Clock, *, limit: int = MAX_PAGE_SIZE
) -> list[ClientGroup]:
    """The groups the client is in now, open ones first."""
    client_id = _client_id(client)
    rows = await session.execute(
        _CURRENT, {"client_id": client_id, "limit": max(1, min(limit, MAX_PAGE_SIZE))}
    )
    return [await client_group(session, client, row.group_id, clock) for row in rows.all()]


async def client_group(
    session: AsyncSession, client: Actor, group_id: str, clock: Clock
) -> ClientGroup:
    client_id = _client_id(client)
    group = await _visible(session, client, group_id)
    members = await _members(session, group)
    current = [m for m in members if m.left_at is None]
    own = next((m for m in current if m.client_id == client_id), None)
    if own is None:
        raise ApplicationError("not_found")
    others = [m for m in current if m.client_id != client_id]
    own_value = await service.client_goal_value(session, client, own.goal_id, clock)
    value = sum((m.value for m in others), own_value)
    known = [m.value_on for m in others if m.value_on is not None]
    return ClientGroup(
        id=group.id,
        name=group.name,
        category=group.category,
        target=group.target,
        target_date=group.target_date,
        status=group.status,
        created_at=group.created_at,
        is_owner=group.owner_client_id == client_id,
        own_goal_id=own.goal_id,
        progress=_progress(group, value, clock),
        # A member the worker has not looked at yet counts as nothing so far.
        others_as_of=min(known) if known else None,
        members=[
            ClientMember(
                display_name=m.display_name,
                joined_at=m.joined_at,
                is_you=m.client_id == client_id,
                is_owner=m.client_id == group.owner_client_id,
            )
            for m in current
        ],
        invite_expires_at=(
            group.invite_expires_at
            if group.owner_client_id == client_id
            and invite_open(group.invite_expires_at, clock.now())
            else None
        ),
    )


async def list_groups(
    session: AsyncSession,
    staff: Actor,
    *,
    status: GroupStatus | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[Group]:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(
        _FOR_STAFF,
        {
            "status": status.value if status else None,
            "limit": max(1, min(limit, MAX_PAGE_SIZE)),
        },
    )
    return [_group(row) for row in rows.all()]


async def staff_group(
    session: AsyncSession, staff: Actor, group_id: str, clock: Clock
) -> StaffGroup:
    """A group with every member, past ones included, and each one's figure as of the worker's
    last look."""
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    row = (await session.execute(_BY_ID, {"id": group_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    group = _group(row)
    members = await _members(session, group)
    zero = Money.zero(group.target.currency)
    value = sum((m.value for m in members if m.left_at is None), zero)
    return StaffGroup(group=group, progress=_progress(group, value, clock), members=members)


# The worker -------------------------------------------------------------------------------------


async def refresh_values(session: AsyncSession, clock: Clock) -> int:
    """Record what each current member's goal is worth today, for the group to show. For the
    worker, as the system. Returns how many it recorded; a second run on the same day records
    none, so running it again changes nothing."""
    today = business_date(clock.now())
    done = 0
    while True:
        goal_ids = list(
            (await session.execute(_TO_REFRESH, {"today": today, "limit": REFRESH_BATCH}))
            .scalars()
            .all()
        )
        if not goal_ids:
            return done
        for goal_id in goal_ids:
            goal = await service.get_goal(session, goal_id)
            value = await service.goal_value(session, goal)
            await session.execute(
                _SET_VALUE, {"goal_id": goal_id, "value_minor": value.minor, "today": today}
            )
        done += len(goal_ids)


# Internals --------------------------------------------------------------------------------------


def _client_id(client: Actor) -> str:
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    return client.id


def _checked_display_name(name: str) -> str:
    refusal = check_display_name(name)
    if refusal is not None:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["display_name"], code=refusal.value)],
        )
    return clean_display_name(name)


def _same_currency(target: Money, own_target: Money) -> None:
    if own_target.currency is not target.currency:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["own_target_minor"], code="not_group_currency")],
        )


async def _invite_row(session: AsyncSession, code: str, clock: Clock) -> Row[Any]:
    row = (await session.execute(_INVITE, {"hash": invite_hash(code)})).one_or_none()
    if (
        row is None
        or row.status != GroupStatus.ACTIVE.value
        or not invite_open(row.invite_expires_at, clock.now())
    ):
        raise ApplicationError("not_found")
    return row


async def _visible(
    session: AsyncSession, client: Actor, group_id: str, *, lock: bool = False
) -> Group:
    """The group, if the client is in it now or set it up. Otherwise not found."""
    client_id = _client_id(client)
    row = (await session.execute(_BY_ID_LOCKED if lock else _BY_ID, {"id": group_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    group = _group(row)
    if group.owner_client_id != client_id and not any(
        m.client_id == client_id and m.left_at is None for m in await _members(session, group)
    ):
        raise ApplicationError("not_found")
    return group


async def _owned(session: AsyncSession, client: Actor, group_id: str) -> Group:
    """The client's own active group, locked. A member who did not set it up gets not found
    for a group they cannot change, and a closed group is a conflict."""
    group = await _visible(session, client, group_id, lock=True)
    if group.owner_client_id != client.id:
        raise ApplicationError("not_found")
    if group.status is not GroupStatus.ACTIVE:
        raise ApplicationError("conflict")
    return group


async def _own_membership(session: AsyncSession, client: Actor, group: Group) -> Member:
    client_id = _client_id(client)
    for member in await _members(session, group):
        if member.client_id == client_id and member.left_at is None:
            return member
    raise ApplicationError("not_found")


async def _members(session: AsyncSession, group: Group) -> list[Member]:
    rows = (await session.execute(_MEMBERS, {"group_id": group.id})).all()
    return [_member(row, group.target.currency) for row in rows]


def _progress(group: Group, value: Money, clock: Clock) -> GroupProgress:
    return group_progress(
        target=group.target,
        value=value,
        set_up_on=business_date(group.created_at),
        target_date=group.target_date,
        today=business_date(clock.now()),
    )


def _group(row: Row[Any]) -> Group:
    return Group(
        id=row.id,
        owner_client_id=row.owner_client_id,
        name=row.name,
        category=GoalCategory(row.category),
        target=Money(row.target_minor, Currency(row.currency)),
        target_date=row.target_date,
        status=GroupStatus(row.status),
        invite_expires_at=row.invite_expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        closed_at=row.closed_at,
    )


def _member(row: Row[Any], currency: Currency) -> Member:
    return Member(
        goal_id=row.goal_id,
        client_id=row.client_id,
        display_name=row.display_name,
        joined_at=row.joined_at,
        left_at=row.left_at,
        value=Money(row.value_minor, currency),
        value_on=row.value_on,
    )
