"""What the review queue shows each reader, in what order."""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.identity.domain.permissions import PERMISSIONS, permissions_for
from app.modules.review_queue.domain.queue import GROUPS, VIEW, QueueItem, queue, visible_kinds
from app.shared.money import Currency, Money

T0 = datetime(2026, 11, 14, 9, tzinfo=UTC)


def item(kind: str, minutes: int = 0, id: str = "x") -> QueueItem:
    return QueueItem(
        kind,  # type: ignore[arg-type]
        id,
        "client",
        Money(1_000, Currency.UGX),
        "reason",
        T0 + timedelta(minutes=minutes),
    )


def test_every_kind_has_a_group_and_a_real_permission() -> None:
    assert set(GROUPS) == set(VIEW)
    assert all(permission in PERMISSIONS for permission in VIEW.values())


def test_each_role_sees_the_work_its_screens_do() -> None:
    assert visible_kinds(permissions_for("admin")) == list(VIEW)
    assert visible_kinds(permissions_for("operations")) == [
        "bank_transfer",
        "identity_check",
        "payout_account",
        "held_payout",
        "payout_held_up",
        "payout_unanswered",
        "investment_transfer",
        "organisation",
    ]
    # Finance cannot open identity checks or decide company applications.
    assert "identity_check" not in visible_kinds(permissions_for("finance"))
    assert "organisation" not in visible_kinds(permissions_for("finance"))
    # Compliance does not see money movement or reconciliation.
    assert visible_kinds(permissions_for("compliance")) == [
        "identity_check",
        "payout_account",
        "held_payout",
        "investment_transfer",
        "organisation",
    ]
    # Support has no review queue at all.
    assert visible_kinds(permissions_for("support")) == []


def test_the_longest_waiting_comes_first_and_the_oldest_is_its_time() -> None:
    shown = queue(
        [item("held_payout", 30, "b"), item("identity_check", 5, "a"), item("bank_transfer", 60)],
        permissions_for("admin"),
        4,
    )
    assert [each.id for each in shown.items] == ["a", "b", "x"]
    assert shown.oldest == T0 + timedelta(minutes=5)
    assert shown.cleared_automatically_today == 4


def test_a_kind_the_reader_may_not_see_is_dropped_even_if_it_was_read() -> None:
    shown = queue([item("bank_transfer"), item("identity_check")], permissions_for("compliance"), 0)
    assert [each.kind for each in shown.items] == ["identity_check"]


def test_an_empty_queue_has_no_oldest_item() -> None:
    shown = queue([], permissions_for("operations"), 0)
    assert (shown.items, shown.oldest) == ([], None)


def test_a_count_is_never_negative() -> None:
    with pytest.raises(ValueError):
        queue([], permissions_for("admin"), -1)
