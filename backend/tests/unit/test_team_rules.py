from itertools import product

import pytest

from app.modules.identity.domain.permissions import ROLES
from app.modules.identity.domain.team import (
    CHANGE_KINDS,
    Change,
    Member,
    TeamRefusal,
    check_change,
    check_decision,
)

ME = "a" * 20
THEM = "b" * 20
OTHER = "c" * 20


def refusal(change: Change, member: Member | None, *, admins: int = 3) -> str | None:
    try:
        check_change(change, member, acting=ME, active_admins=admins)
    except TeamRefusal as error:
        return error.refusal
    return None


@pytest.mark.parametrize("kind", CHANGE_KINDS)
def test_nobody_proposes_a_change_to_their_own_account(kind: str) -> None:
    member = Member(ME, "admin", "active")
    assert refusal(Change(kind, ME, "support"), member) == "own_account"  # type: ignore[arg-type]


def test_an_invite_needs_no_member() -> None:
    assert refusal(Change("invite", THEM, "support"), None) is None


def test_a_role_change_to_the_same_role_is_refused() -> None:
    for role in ROLES:
        assert refusal(Change("change_role", THEM, role), Member(THEM, role, "active")) == (
            "same_role"
        )


@pytest.mark.parametrize("kind", ["change_role", "deactivate", "reset_sign_in"])
def test_nothing_but_reactivation_is_done_to_a_deactivated_member(kind: str) -> None:
    member = Member(THEM, "support", "disabled")
    assert refusal(Change(kind, THEM, "finance"), member) == "already_disabled"  # type: ignore[arg-type]
    assert refusal(Change("reactivate", THEM), member) is None


@pytest.mark.parametrize("status", ["invited", "active"])
def test_only_a_deactivated_member_is_reactivated(status: str) -> None:
    member = Member(THEM, "support", status)  # type: ignore[arg-type]
    assert refusal(Change("reactivate", THEM), member) == "not_disabled"


def test_the_last_active_admin_is_never_removed_or_demoted() -> None:
    admin = Member(THEM, "admin", "active")
    assert refusal(Change("deactivate", THEM), admin, admins=1) == "last_admin"
    for role in ROLES:
        if role != "admin":
            assert refusal(Change("change_role", THEM, role), admin, admins=1) == "last_admin"
    assert refusal(Change("deactivate", THEM), admin, admins=2) is None
    # A new set-up link keeps them an admin.
    assert refusal(Change("reset_sign_in", THEM), admin, admins=1) is None


def test_an_invited_admin_does_not_count_as_the_last_one() -> None:
    invited = Member(THEM, "admin", "invited")
    assert refusal(Change("deactivate", THEM), invited, admins=1) is None


def test_decisions_take_two_people_and_never_the_subject() -> None:
    for decision, deciding in product(("approved", "rejected", "withdrawn"), (ME, OTHER, THEM)):
        allowed = check_decision(
            decision,  # type: ignore[arg-type]
            proposed_by=ME,
            deciding=deciding,
            staff_id=THEM,
        )
        if deciding == THEM:
            assert not allowed
        elif decision == "withdrawn":
            assert allowed == (deciding == ME)
        else:
            assert allowed == (deciding == OTHER)
