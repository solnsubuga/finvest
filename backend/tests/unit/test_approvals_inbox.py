"""What the approvals inbox shows each reader, and what it lets them do."""

from datetime import UTC, datetime, timedelta

from app.modules.approvals.domain.inbox import SOURCES, Waiting, inbox, visible_kinds
from app.modules.identity.domain.permissions import PERMISSIONS, Role, permissions_for

T0 = datetime(2026, 11, 13, 9, tzinfo=UTC)


def waiting(kind: str, proposer: str = "maker", minutes: int = 0, id: str = "x") -> Waiting:
    return Waiting(kind, id, "subject", proposer, T0 + timedelta(minutes=minutes))  # type: ignore[arg-type]


def test_every_source_names_real_permissions() -> None:
    for source in SOURCES.values():
        assert source.view in PERMISSIONS and source.decide in PERMISSIONS


def test_admin_sees_every_kind_and_support_sees_what_touches_clients() -> None:
    assert visible_kinds(permissions_for("admin")) == set(SOURCES)
    assert visible_kinds(permissions_for("support")) == {
        "client_lock",
        "account_closure",
        "suppression_lift",
        "complaint_outcome",
        "client_pin_reset",
        "client_limits",
        "client_statement",
    }


def test_support_may_decide_a_lift_but_not_a_lock_or_a_complaint_outcome() -> None:
    items = [waiting("client_lock", id="a"), waiting("suppression_lift", id="b")]
    items.append(waiting("complaint_outcome", id="c"))
    shown = {i.kind: i.can_decide for i in inbox(items, "me", permissions_for("support"))}
    assert shown == {"client_lock": False, "suppression_lift": True, "complaint_outcome": False}


def test_nobody_decides_a_team_change_about_themselves() -> None:
    about_me = Waiting("team_change", "x", "change_role", "other", T0, about_staff="me")
    [item] = inbox([about_me], "me", permissions_for("admin"))
    assert (item.yours, item.can_decide) == (False, False)
    [item] = inbox([about_me], "third", permissions_for("admin"))
    assert item.can_decide is True
    # Only admins change the team, though others see it.
    assert inbox([about_me], "third", permissions_for("operations")) == []


def test_the_proposer_can_never_decide_their_own_item() -> None:
    [item] = inbox([waiting("rate_change", proposer="me")], "me", permissions_for("admin"))
    assert (item.yours, item.can_decide) == (True, False)


def test_someone_else_decides_only_with_the_deciding_permission() -> None:
    # Operations see bank transfer matches and hold bank_transfers.change.
    [item] = inbox([waiting("bank_transfer_match")], "me", permissions_for("operations"))
    assert (item.yours, item.can_decide) == (False, True)
    # Operations see the migration but cannot sign it off.
    [item] = inbox([waiting("migration_sign_off")], "me", permissions_for("operations"))
    assert (item.yours, item.can_decide) == (False, False)


def test_a_payout_account_is_decided_only_by_those_who_change_payout_accounts() -> None:
    # Finance see the review queue but cannot approve a payout account; compliance can.
    [item] = inbox([waiting("payout_account")], "me", permissions_for("finance"))
    assert item.can_decide is False
    [item] = inbox([waiting("payout_account")], "me", permissions_for("compliance"))
    assert item.can_decide is True


def test_finance_see_a_checked_payout_account_but_cannot_approve_it() -> None:
    [item] = inbox([waiting("payout_account")], "me", permissions_for("finance"))
    assert (item.yours, item.can_decide) == (False, False)
    for role in ("operations", "compliance"):
        [item] = inbox([waiting("payout_account")], "me", permissions_for(role))
        assert item.can_decide is True
    assert inbox([waiting("payout_account")], "me", permissions_for("support")) == []


def test_a_payout_resolution_is_decided_by_those_who_change_reconciliation() -> None:
    for role in ("admin", "finance"):
        [item] = inbox([waiting("payout_resolution")], "me", permissions_for(role))
        assert item.can_decide is True
    # Operations sees reconciliation but does not change it.
    [item] = inbox([waiting("payout_resolution")], "me", permissions_for("operations"))
    assert item.can_decide is False
    assert inbox([waiting("payout_resolution")], "me", permissions_for("compliance")) == []
    assert inbox([waiting("payout_resolution")], "me", permissions_for("support")) == []


def test_a_payout_release_is_decided_by_those_who_check_payout_accounts() -> None:
    for role in ("admin", "operations", "compliance"):
        [item] = inbox([waiting("payout_release")], "me", permissions_for(role))
        assert item.can_decide is True
    # Finance sees the review queue but does not check payout accounts.
    [seen] = inbox([waiting("payout_release")], "me", permissions_for("finance"))
    assert seen.can_decide is False
    assert inbox([waiting("payout_release")], "me", permissions_for("support")) == []
    [mine] = inbox([waiting("payout_release", proposer="me")], "me", permissions_for("admin"))
    assert (mine.yours, mine.can_decide) == (True, False)


def test_items_the_reader_cannot_see_are_left_out() -> None:
    items = [waiting("wording_change"), waiting("dealing_order", id="y")]
    assert inbox(items, "me", permissions_for("compliance")) == []
    assert inbox(items, "me", permissions_for("support")) == []
    assert [i.kind for i in inbox(items, "me", permissions_for("finance"))] == [
        "dealing_order",
        "wording_change",
    ]


def test_oldest_first_then_by_kind_and_id() -> None:
    items = [
        waiting("rate_change", minutes=5, id="c"),
        waiting("pot_transfer", minutes=0, id="b"),
        waiting("day_close", minutes=0, id="a"),
    ]
    shown = inbox(items, "me", permissions_for("admin"))
    assert [i.id for i in shown] == ["a", "b", "c"]


def test_buffer_changes_are_seen_with_the_desk_and_decided_by_who_changes_orders() -> None:
    for kind in ("buffer_rules", "buffer_capital"):
        assert SOURCES[kind].view == "dealing_desk.view"
        assert SOURCES[kind].decide == "dealing_orders.change"
    items = [waiting("buffer_rules", id="a"), waiting("buffer_capital", id="b")]
    for role in ("admin", "finance"):
        shown = {i.kind: i.can_decide for i in inbox(items, "me", permissions_for(role))}
        assert shown == {"buffer_rules": True, "buffer_capital": True}
    assert inbox(items, "me", permissions_for("support")) == []


def test_a_deposit_reassignment_is_decided_by_operations_and_finance_only() -> None:
    deciders: tuple[Role, ...] = ("operations", "finance", "admin")
    for role in deciders:
        [item] = inbox([waiting("deposit_reassignment")], "me", permissions_for(role))
        assert item.can_decide is True
    others: tuple[Role, ...] = ("compliance", "support")
    for other in others:
        assert inbox([waiting("deposit_reassignment")], "me", permissions_for(other)) == []
    [mine] = inbox([waiting("deposit_reassignment", proposer="me")], "me", permissions_for("admin"))
    assert (mine.yours, mine.can_decide) == (True, False)


def test_a_bank_account_change_is_decided_by_finance_or_admin_and_never_its_proposer() -> None:
    for role in ("admin", "finance"):
        [item] = inbox([waiting("bank_account_change")], "me", permissions_for(role))
        assert item.can_decide is True
    others: tuple[Role, ...] = ("operations", "compliance", "support")
    for role in others:
        assert inbox([waiting("bank_account_change")], "me", permissions_for(role)) == []
    [mine] = inbox([waiting("bank_account_change", proposer="me")], "me", permissions_for("admin"))
    assert (mine.yours, mine.can_decide) == (True, False)
