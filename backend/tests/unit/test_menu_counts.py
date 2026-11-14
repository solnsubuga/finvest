"""Which console menu entries carry a count, and how far they count."""

import re
from pathlib import Path

import pytest

from app.modules.identity.domain.permissions import PERMISSIONS, ROLES, permissions_for
from app.modules.review_queue.domain.menu import CAP, VIEW, counted_screens, menu_counts

NAV = Path(__file__).resolve().parents[3] / "apps" / "console" / "src" / "nav" / "nav.ts"


def test_every_counted_screen_is_a_menu_entry_opened_by_the_same_permission() -> None:
    entries = dict(
        re.findall(r'path: "([a-z-]+)",[^}]*?permission: "([a-z_.]+)"', NAV.read_text(), re.S)
    )
    for screen, permission in VIEW.items():
        assert permission in PERMISSIONS
        assert entries[screen] == permission


@pytest.mark.parametrize("role", ROLES)
def test_a_role_is_counted_only_the_screens_it_may_open(role: str) -> None:
    held = permissions_for(role)  # type: ignore[arg-type]
    assert counted_screens(held) == [s for s, p in VIEW.items() if p in held]
    counts = menu_counts(dict.fromkeys(VIEW, 3), held)
    assert set(counts) == set(counted_screens(held))


def test_support_staff_get_no_count_for_money_screens() -> None:
    counts = menu_counts(dict.fromkeys(VIEW, 5), permissions_for("support"))
    assert "reconciliation" not in counts
    assert "review-queue" not in counts
    assert counts["support"] == 5


def test_a_screen_nobody_counted_is_zero_and_a_large_count_stops_at_the_cap() -> None:
    counts = menu_counts({"review-queue": 1_000}, ["review_queue.view", "rates.view"])
    assert counts == {"review-queue": CAP, "rates": 0}


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(ValueError):
        menu_counts({"rates": -1}, ["rates.view"])


def test_no_permissions_no_counts() -> None:
    assert menu_counts(dict.fromkeys(VIEW, 2), []) == {}
