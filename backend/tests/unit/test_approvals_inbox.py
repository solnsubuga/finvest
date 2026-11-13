"""What the approvals inbox shows each reader, and what it lets them do."""

from datetime import UTC, datetime, timedelta

from app.modules.approvals.domain.inbox import SOURCES, Waiting, inbox, visible_kinds
from app.modules.identity.domain.permissions import PERMISSIONS, permissions_for

T0 = datetime(2026, 11, 13, 9, tzinfo=UTC)


def waiting(kind: str, proposer: str = "maker", minutes: int = 0, id: str = "x") -> Waiting:
    return Waiting(kind, id, "subject", proposer, T0 + timedelta(minutes=minutes))  # type: ignore[arg-type]


def test_every_source_names_real_permissions() -> None:
    for source in SOURCES.values():
        assert source.view in PERMISSIONS and source.decide in PERMISSIONS


def test_admin_sees_every_kind_and_support_sees_none_of_these() -> None:
    assert visible_kinds(permissions_for("admin")) == set(SOURCES)
    assert visible_kinds(permissions_for("support")) == set()


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


def test_finance_see_a_checked_payout_account_but_cannot_approve_it() -> None:
    [item] = inbox([waiting("payout_account")], "me", permissions_for("finance"))
    assert (item.yours, item.can_decide) == (False, False)
    for role in ("operations", "compliance"):
        [item] = inbox([waiting("payout_account")], "me", permissions_for(role))
        assert item.can_decide is True
    assert inbox([waiting("payout_account")], "me", permissions_for("support")) == []


def test_items_the_reader_cannot_see_are_left_out() -> None:
    items = [waiting("wording_change"), waiting("dealing_order")]
    assert inbox(items, "me", permissions_for("compliance")) == []
    assert [i.kind for i in inbox(items, "me", permissions_for("finance"))] == ["dealing_order"]


def test_oldest_first_then_by_kind_and_id() -> None:
    items = [
        waiting("rate_change", minutes=5, id="c"),
        waiting("pot_transfer", minutes=0, id="b"),
        waiting("day_close", minutes=0, id="a"),
    ]
    shown = inbox(items, "me", permissions_for("admin"))
    assert [i.id for i in shown] == ["a", "b", "c"]
