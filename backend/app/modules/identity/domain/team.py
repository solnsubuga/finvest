"""The rules for changing the team. Pure: no HTTP or persistence imports.

Every change to who works here, and what they may do, is proposed by one admin and approved by
a different one. These rules are checked when a change is proposed and again when it is
approved, because the team may have changed in between.
"""

from dataclasses import dataclass
from typing import Literal, get_args

from app.modules.identity.domain.permissions import Role

ChangeKind = Literal["invite", "change_role", "deactivate", "reactivate", "reset_sign_in"]
CHANGE_KINDS: tuple[ChangeKind, ...] = get_args(ChangeKind)
StaffStatus = Literal["invited", "active", "disabled"]
Decision = Literal["approved", "rejected", "withdrawn"]

MAX_NAME = 120
MAX_REASON = 500

# Why a change is refused. Each is a field code under `kind` in the error envelope.
Refusal = Literal[
    # Nobody proposes or decides a change to their own account.
    "own_account",
    # The person already has that role.
    "same_role",
    # The change would leave nobody able to run the team.
    "last_admin",
    # Already deactivated, or not deactivated.
    "already_disabled",
    "not_disabled",
]


class TeamRefusal(ValueError):
    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal)
        self.refusal: Refusal = refusal


@dataclass(frozen=True, slots=True)
class Member:
    """What the rules need to know about the person a change is about."""

    id: str
    role: Role
    status: StaffStatus


@dataclass(frozen=True, slots=True)
class Change:
    kind: ChangeKind
    staff_id: str
    role: Role | None = None


def check_change(change: Change, member: Member | None, *, acting: str, active_admins: int) -> None:
    """Refuse a change that cannot be made to `member` as the team stands.

    `member` is None only for an invite. `acting` is whoever proposes or decides it, and
    `active_admins` counts the active admins now, including `member` if they are one.
    """
    if change.staff_id == acting:
        raise TeamRefusal("own_account")
    if change.kind == "invite" or member is None:
        return
    if change.kind == "reactivate":
        if member.status != "disabled":
            raise TeamRefusal("not_disabled")
        return
    if member.status == "disabled":
        raise TeamRefusal("already_disabled")
    if change.kind == "change_role" and change.role == member.role:
        raise TeamRefusal("same_role")
    leaves_admin = change.kind == "deactivate" or (
        change.kind == "change_role" and change.role != "admin"
    )
    if leaves_admin and member.role == "admin" and member.status == "active" and active_admins <= 1:
        raise TeamRefusal("last_admin")


def check_decision(decision: Decision, *, proposed_by: str, deciding: str, staff_id: str) -> bool:
    """True when `deciding` may record `decision`. Only the proposer withdraws, and only
    someone else approves or rejects. Nobody decides a change to their own account."""
    if deciding == staff_id:
        return False
    return (deciding == proposed_by) == (decision == "withdrawn")
