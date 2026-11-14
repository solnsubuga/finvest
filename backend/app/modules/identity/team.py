"""The team, and the two-person changes to it.

Every function works inside the caller's transaction, acting as the member of staff who asked.
A proposal, its decision, what the decision changes and their audit events commit together or
not at all. Migration 0150 holds the rules PostgreSQL enforces: the approver is never the
proposer, nobody acts on their own account, and a role, an invite or a deactivation happens
only through a change approved in the same transaction.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity import staff_access
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_email
from app.modules.identity.domain.permissions import Role
from app.modules.identity.domain.team import (
    MAX_NAME,
    MAX_REASON,
    Change,
    ChangeKind,
    Decision,
    Member,
    StaffStatus,
    TeamRefusal,
    check_change,
    check_decision,
)
from app.modules.identity.passkeys import ENROLMENT_LIFETIME, issue_enrolment
from app.modules.notifications.service import queue_message
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "staff_change"
ENROL_PATH = "/enrol"
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class TeamMember:
    id: str
    name: str
    email: str
    role: Role
    status: StaffStatus
    has_passkey: bool
    has_authenticator: bool
    created_at: datetime
    last_signed_in_at: datetime | None
    # Where Cloudflare Access stands for them. None: nothing has asked for it yet.
    access: staff_access.AccessStatus | None = None


@dataclass(frozen=True, slots=True)
class StaffChange:
    id: str
    kind: ChangeKind
    staff_id: str
    # The name of the person it is about: the invited name for an invite.
    staff_name: str
    email: str | None
    role: Role | None
    # The person's role when the change was listed, for a role change.
    current_role: Role | None
    reason: str
    proposed_by: str
    proposed_by_name: str
    proposed_at: datetime
    status: str
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None


@dataclass(frozen=True, slots=True)
class Proposed:
    kind: ChangeKind
    reason: str
    staff_id: str | None = None
    email: str | None = None
    name: str | None = None
    role: Role | None = None


_MEMBERS = text(
    "SELECT s.id, s.name, s.email, s.role, s.status, s.created_at, "
    "EXISTS (SELECT FROM identity_passkeys p WHERE p.actor_kind = 'staff' "
    "AND p.actor_id = s.id AND p.removed_at IS NULL) AS has_passkey, "
    "EXISTS (SELECT FROM identity_authenticators a WHERE a.actor_kind = 'staff' "
    "AND a.actor_id = s.id AND a.confirmed_at IS NOT NULL AND a.removed_at IS NULL) "
    "AS has_authenticator, "
    "(SELECT max(created_at) FROM identity_sessions i WHERE i.actor_kind = 'staff' "
    "AND i.actor_id = s.id) AS last_signed_in_at "
    "FROM identity_staff s ORDER BY s.status = 'disabled', lower(s.name), s.id"
)
_CHANGES = (
    "SELECT c.id, c.kind, c.staff_id, coalesce(s.name, c.name) AS staff_name, c.email, c.role, "
    "s.role AS current_role, c.reason, c.proposed_by, p.name AS proposed_by_name, "
    "c.proposed_at, c.status, c.decided_by, c.decided_at, c.decision_reason "
    "FROM identity_staff_changes c "
    "JOIN identity_staff p ON p.id = c.proposed_by "
    "LEFT JOIN identity_staff s ON s.id = c.staff_id "
)
_OPEN = text(_CHANGES + "WHERE c.status = 'proposed' ORDER BY c.proposed_at, c.id")
_BY_ID = text(_CHANGES + "WHERE c.id = :id")
_LOCK = text(
    "SELECT id FROM identity_staff_changes WHERE id = :id AND status = 'proposed' FOR UPDATE"
)
_MEMBER = text("SELECT id, role, status FROM identity_staff WHERE id = :id")
# Locking every active admin serialises two approvals that each remove one, so both cannot pass
# the last-admin rule at once.
_ACTIVE_ADMINS = text(
    "SELECT id FROM identity_staff WHERE role = 'admin' AND status = 'active' "
    "ORDER BY id FOR UPDATE"
)
_EMAIL_TAKEN = text("SELECT true FROM identity_staff WHERE email = :email")
_INSERT = text(
    "INSERT INTO identity_staff_changes (id, kind, staff_id, email, name, role, reason, "
    "proposed_by, proposed_at) VALUES (:id, :kind, :staff_id, :email, :name, :role, :reason, "
    ":staff, :now)"
)
_DECIDE = text(
    "UPDATE identity_staff_changes SET status = :status, decided_by = :staff, decided_at = :now, "
    "decision_reason = :reason, "
    "decided_in = CASE WHEN :status = 'approved' THEN pg_current_xact_id() END WHERE id = :id"
)


async def list_team(session: AsyncSession) -> list[TeamMember]:
    """Everyone who has been added, active first, by name."""
    rows = (await session.execute(_MEMBERS)).all()
    access = await staff_access.statuses(session)
    return [
        TeamMember(
            id=row.id,
            name=row.name,
            email=row.email,
            role=row.role,
            status=row.status,
            has_passkey=row.has_passkey,
            has_authenticator=row.has_authenticator,
            created_at=row.created_at,
            last_signed_in_at=row.last_signed_in_at,
            access=access.get(row.id),
        )
        for row in rows
    ]


async def open_changes(session: AsyncSession) -> list[StaffChange]:
    """Every change waiting for a second admin, oldest first."""
    return [_change(row) for row in (await session.execute(_OPEN)).all()]


async def propose(
    session: AsyncSession, staff: Actor, proposed: Proposed, clock: Clock
) -> StaffChange:
    """Put a change to the team forward. Nothing changes until a different admin approves it."""
    acting = _staff_id(staff)
    reason = _text("reason", proposed.reason, MAX_REASON)
    email = name = None
    role = proposed.role
    if proposed.kind == "invite":
        try:
            email = normalize_email(proposed.email or "")
        except InvalidIdentifier:
            raise _invalid("email", "invalid") from None
        name = _text("name", proposed.name or "", MAX_NAME)
        if role is None:
            raise _invalid("role", "required")
        if await session.scalar(_EMAIL_TAKEN, {"email": email}):
            raise _refused("email", "taken", "conflict")
        staff_id = push_id(clock)
        member = None
    else:
        if proposed.staff_id is None:
            raise _invalid("staff_id", "required")
        staff_id = proposed.staff_id
        if proposed.kind == "change_role":
            if role is None:
                raise _invalid("role", "required")
        else:
            role = None
        member = await _member(session, staff_id)
    change = Change(proposed.kind, staff_id, role)
    await _check(session, change, member, acting)
    change_id = push_id(clock)
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": change_id,
                    "kind": proposed.kind,
                    "staff_id": staff_id,
                    "email": email,
                    "name": name,
                    "role": role,
                    "reason": reason,
                    "staff": acting,
                    "now": clock.now(),
                },
            )
    except IntegrityError:
        # A change to this person, or an invite to this address, is already waiting.
        raise ApplicationError("conflict") from None
    await _audit(
        session, staff, f"identity.team.{proposed.kind}_proposed", change_id, change, reason, clock
    )
    return await _by_id(session, change_id)


async def decide(
    session: AsyncSession,
    staff: Actor,
    change_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
    *,
    console_origin: str | None,
) -> StaffChange:
    """Approve or reject another admin's change, or withdraw your own.

    Approving makes the change at once: the person is added and sent a set-up link, their
    role changes, or they are deactivated and every session they hold ends.
    """
    acting = _staff_id(staff)
    reason = _text("reason", reason, MAX_REASON)
    if (await session.execute(_LOCK, {"id": change_id})).one_or_none() is None:
        raise ApplicationError("not_found")
    listed = await _by_id(session, change_id)
    if not check_decision(
        decision, proposed_by=listed.proposed_by, deciding=acting, staff_id=listed.staff_id
    ):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    change = Change(listed.kind, listed.staff_id, listed.role)
    if decision == "approved":
        member = None if listed.kind == "invite" else await _member(session, listed.staff_id)
        if listed.kind == "invite" and await session.scalar(_EMAIL_TAKEN, {"email": listed.email}):
            raise _refused("email", "taken", "conflict")
        await _check(session, change, member, acting)
    now = clock.now()
    await session.execute(
        _DECIDE,
        {"status": decision, "staff": acting, "now": now, "reason": reason, "id": change_id},
    )
    await _audit(
        session, staff, f"identity.team.change_{decision}", change_id, change, reason, clock
    )
    if decision == "approved":
        await _apply(session, staff, listed, clock, console_origin)
    return await _by_id(session, change_id)


async def _apply(
    session: AsyncSession,
    staff: Actor,
    change: StaffChange,
    clock: Clock,
    console_origin: str | None,
) -> None:
    now = clock.now()
    if change.kind == "invite":
        await session.execute(
            text(
                "INSERT INTO identity_staff (id, email, name, role, created_at, created_by) "
                "VALUES (:id, :email, :name, :role, :now, :created_by)"
            ),
            {
                "id": change.staff_id,
                "email": change.email,
                "name": change.staff_name,
                "role": change.role,
                "now": now,
                "created_by": change.proposed_by,
            },
        )
        await _send_link(session, staff, change, clock, console_origin)
    elif change.kind == "reset_sign_in":
        await _send_link(session, staff, change, clock, console_origin)
    elif change.kind == "change_role":
        await session.execute(
            text("UPDATE identity_staff SET role = :role WHERE id = :id"),
            {"role": change.role, "id": change.staff_id},
        )
    elif change.kind == "deactivate":
        await session.execute(
            text("UPDATE identity_staff SET status = 'disabled' WHERE id = :id"),
            {"id": change.staff_id},
        )
        # Access ends now, not when a session next expires, and no set-up link still works.
        await session.execute(
            text(
                "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'deactivated' "
                "WHERE actor_kind = 'staff' AND actor_id = :id AND revoked_at IS NULL"
            ),
            {"now": now, "id": change.staff_id},
        )
        await session.execute(
            text(
                "UPDATE identity_staff_enrolments SET consumed_at = :now "
                "WHERE staff_id = :id AND consumed_at IS NULL"
            ),
            {"now": now, "id": change.staff_id},
        )
    else:
        # Back to how they were: active if they had finished setting up, invited if not.
        await session.execute(
            text(
                "UPDATE identity_staff SET status = CASE WHEN password_hash IS NULL "
                "THEN 'invited' ELSE 'active' END WHERE id = :id"
            ),
            {"id": change.staff_id},
        )
    if change.kind != "change_role":
        # Cloudflare Access lets in whoever is invited or active and nobody deactivated. A new
        # set-up link checks it too, in case the address was taken off the list by hand.
        await staff_access.request_sync(
            session, change.staff_id, key=f"change:{change.id}", now=now
        )
    logger.info("team_change_applied", kind=change.kind, change_id=change.id)


async def request_access_sync(
    session: AsyncSession, staff: Actor, staff_id: str, clock: Clock
) -> staff_access.AccessStatus:
    """Ask for Cloudflare Access to be brought in line with the team again, such as after it
    failed. One admin may ask: it changes nothing the team did not already approve."""
    acting = _staff_id(staff)
    await _member(session, staff_id)
    now = clock.now()
    await staff_access.request_sync(session, staff_id, key=f"again:{push_id(clock)}", now=now)
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="identity.team.access_sync_requested",
            resource_type="staff",
            resource_id=staff_id,
            actor=AuditActor.staff(acting),
            reason=None,
            details={},
        ),
        clock,
    )
    status = await staff_access.status_of(session, staff_id)
    assert status is not None  # just written
    return status


async def _send_link(
    session: AsyncSession,
    staff: Actor,
    change: StaffChange,
    clock: Clock,
    console_origin: str | None,
) -> None:
    if not console_origin:
        # Without the console's address there is no link to send. Refuse before anything
        # commits, so the change stays open.
        raise ApplicationError("unavailable")
    token = await issue_enrolment(session, clock, change.staff_id, staff)
    email = change.email or await session.scalar(
        text("SELECT email FROM identity_staff WHERE id = :id"), {"id": change.staff_id}
    )
    # In the fragment, so the token is not sent to any server or written to a proxy log.
    link = f"{console_origin}{ENROL_PATH}#{token}"
    hours = int(ENROLMENT_LIFETIME / timedelta(hours=1))
    await queue_message(
        session,
        channel="email",
        recipient=str(email),
        template="staff_enrolment",
        variables={"name": change.staff_name, "link": link, "hours": hours},
        idempotency_key=f"staff-enrolment:{change.id}",
        now=clock.now(),
        deliver_within=ENROLMENT_LIFETIME,
    )


async def _check(session: AsyncSession, change: Change, member: Member | None, acting: str) -> None:
    admins = len((await session.execute(_ACTIVE_ADMINS)).all())
    try:
        check_change(change, member, acting=acting, active_admins=admins)
    except TeamRefusal as refusal:
        code = "forbidden" if refusal.refusal == "own_account" else "conflict"
        raise _refused("kind", refusal.refusal, code) from None


async def _member(session: AsyncSession, staff_id: str) -> Member:
    row = (await session.execute(_MEMBER, {"id": staff_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return Member(row.id, row.role, row.status)


async def _by_id(session: AsyncSession, change_id: str) -> StaffChange:
    row = (await session.execute(_BY_ID, {"id": change_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _change(row)


async def _audit(
    session: AsyncSession,
    staff: Actor,
    action: str,
    change_id: str,
    change: Change,
    reason: str,
    clock: Clock,
) -> None:
    details: dict[str, str] = {"kind": change.kind, "staff_id": change.staff_id}
    if change.role is not None:
        details["role"] = change.role
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "rejected")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=change_id,
            actor=AuditActor.staff(_staff_id(staff)),
            reason=reason,
            details=details,
        ),
        clock,
    )


def _change(row: Row[Any]) -> StaffChange:
    return StaffChange(
        id=row.id,
        kind=row.kind,
        staff_id=row.staff_id,
        staff_name=row.staff_name,
        email=row.email,
        role=row.role,
        current_role=row.current_role,
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_by_name=row.proposed_by_name,
        proposed_at=row.proposed_at,
        status=row.status,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
    )


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _text(field: str, value: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise _invalid(field, "required")
    return cleaned


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _refused(field: str, code: str, error: str) -> ApplicationError:
    return ApplicationError(
        "conflict" if error == "conflict" else "forbidden",
        fields=[FieldProblem(field_path=["body", field], code=code)],
    )
