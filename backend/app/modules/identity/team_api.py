"""Routes for the console's Team and permissions screen.

Seeing the team is `team.view`. Proposing or deciding a change is `team.change`, which only
admins hold. Every change needs two admins: one proposes, a different one approves.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.modules.identity import team
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.domain.permissions import ROLES, Permission, Role, permissions_for
from app.modules.identity.domain.team import MAX_NAME, MAX_REASON, ChangeKind, StaffStatus
from app.modules.identity.service import StaffSession
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

router = APIRouter(prefix="/staff/team", tags=["staff team"], responses=_REFUSALS)
routers = [router]

TeamViewer = Annotated[StaffSession, Depends(require_permission("team.view"))]
TeamChanger = Annotated[StaffSession, Depends(require_permission("team.change"))]
# Deciding needs a passkey or app code from the last few minutes.
TeamDecider = Annotated[StaffSession, Depends(require_confirmed("team.change"))]
Reason = Annotated[str, Field(min_length=1, max_length=MAX_REASON)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _console_origin(request: Request) -> str | None:
    settings: Settings = request.app.state.settings
    return settings.staff_allowed_origins[0] if settings.staff_allowed_origins else None


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
ConsoleOrigin = Annotated[str | None, Depends(_console_origin)]


class TeamMemberView(Contract):
    id: PushId
    name: str
    email: str
    role: Role
    status: StaffStatus
    has_passkey: bool
    has_authenticator: bool
    created_at: datetime
    last_signed_in_at: datetime | None


class ChangeView(Contract):
    id: PushId
    kind: ChangeKind
    staff_id: PushId
    staff_name: str
    # Set for an invite.
    email: str | None
    # The new role, for an invite or a role change.
    role: Role | None
    # The role the person holds now. None for an invite.
    current_role: Role | None
    reason: str
    proposed_by: PushId
    proposed_by_name: str
    proposed_at: datetime
    status: Literal["proposed", "approved", "rejected", "withdrawn"]
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None
    # The proposer can withdraw it. Any other admin can approve or reject it, unless it is
    # about them.
    yours: bool
    about_you: bool


class RoleView(Contract):
    role: Role
    permissions: list[Permission]


class TeamView(Contract):
    members: list[TeamMemberView]
    changes: list[ChangeView]
    roles: list[RoleView]
    # Whether this person may propose and decide changes.
    can_change: bool


class ProposeChange(Contract):
    kind: ChangeKind
    reason: Reason
    # The person the change is about. Not sent for an invite.
    staff_id: PushId | None = None
    # For an invite.
    email: Annotated[str, Field(max_length=254)] | None = None
    name: Annotated[str, Field(max_length=MAX_NAME)] | None = None
    # For an invite or a role change.
    role: Role | None = None


class DecideChange(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


def _view(change: team.StaffChange, staff: StaffSession) -> ChangeView:
    return ChangeView(
        id=change.id,
        kind=change.kind,
        staff_id=change.staff_id,
        staff_name=change.staff_name,
        email=change.email,
        role=change.role,
        current_role=change.current_role,
        reason=change.reason,
        proposed_by=change.proposed_by,
        proposed_by_name=change.proposed_by_name,
        proposed_at=change.proposed_at,
        status=change.status,  # type: ignore[arg-type]
        decided_by=change.decided_by,
        decided_at=change.decided_at,
        decision_reason=change.decision_reason,
        yours=change.proposed_by == staff.staff_id,
        about_you=change.staff_id == staff.staff_id,
    )


@router.get("")
async def get_team(staff: TeamViewer, database: Db) -> TeamView:
    """Everyone on the team, what each role may do, and the changes waiting for a second admin."""
    async with database.actor_transaction(staff.actor) as session:
        members = await team.list_team(session)
        changes = await team.open_changes(session)
    return TeamView(
        members=[
            TeamMemberView(
                id=member.id,
                name=member.name,
                email=member.email,
                role=member.role,
                status=member.status,
                has_passkey=member.has_passkey,
                has_authenticator=member.has_authenticator,
                created_at=member.created_at,
                last_signed_in_at=member.last_signed_in_at,
            )
            for member in members
        ],
        changes=[_view(change, staff) for change in changes],
        roles=[RoleView(role=role, permissions=permissions_for(role)) for role in ROLES],
        can_change="team.change" in staff.permissions,
    )


@router.post("/changes", status_code=201)
async def propose_change(
    body: ProposeChange, staff: TeamChanger, database: Db, clock: Time
) -> ChangeView:
    """Put a change to the team forward. A different admin must approve it."""
    proposed = team.Proposed(
        kind=body.kind,
        reason=body.reason,
        staff_id=body.staff_id,
        email=body.email,
        name=body.name,
        role=body.role,
    )
    async with database.actor_transaction(staff.actor) as session:
        change = await team.propose(session, staff.actor, proposed, clock)
    return _view(change, staff)


@router.post("/changes/{change_id}/decision")
async def decide_change(
    change_id: PushId,
    body: DecideChange,
    staff: TeamDecider,
    database: Db,
    clock: Time,
    origin: ConsoleOrigin,
) -> ChangeView:
    """Approve or reject another admin's change, or withdraw your own. Approving applies it."""
    async with database.actor_transaction(staff.actor) as session:
        change = await team.decide(
            session,
            staff.actor,
            change_id,
            body.decision,
            body.reason,
            clock,
            console_origin=origin,
        )
    return _view(change, staff)
