"""The pure rules of group goals: names shown to members, invites, and progress."""

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.goals.domain.goals import Standing
from app.modules.goals.domain.groups import (
    INVITE_LIFETIME,
    MAX_DISPLAY_NAME_LENGTH,
    MemberRefusal,
    check_display_name,
    clean_display_name,
    group_progress,
    invite_expires,
    invite_hash,
    invite_open,
)
from app.shared.money import Currency, Money

NOW = datetime(2026, 11, 13, 9, tzinfo=UTC)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


@pytest.mark.parametrize(
    ("name", "refusal"),
    [
        ("Grace", None),
        ("  Grace   Auma ", None),
        ("x" * MAX_DISPLAY_NAME_LENGTH, None),
        ("x" * (MAX_DISPLAY_NAME_LENGTH + 1), MemberRefusal.DISPLAY_NAME_TOO_LONG),
        ("", MemberRefusal.DISPLAY_NAME_EMPTY),
        (" \t\n", MemberRefusal.DISPLAY_NAME_EMPTY),
    ],
)
def test_a_name_to_be_shown_by(name: str, refusal: MemberRefusal | None) -> None:
    assert check_display_name(name) is refusal


def test_extra_spaces_are_removed_before_the_length_is_counted() -> None:
    assert clean_display_name("  Grace   Auma ") == "Grace Auma"
    assert check_display_name("a" + " " * 40 + "b") is None


def test_an_invite_is_stored_as_its_sha256() -> None:
    assert invite_hash("abc") == hashlib.sha256(b"abc").digest()
    assert len(invite_hash("abc")) == 32
    assert invite_hash("abc") != invite_hash("abd")


def test_an_invite_works_for_seven_days_and_not_a_moment_more() -> None:
    expires = invite_expires(NOW)
    assert expires - NOW == INVITE_LIFETIME == timedelta(days=7)
    assert invite_open(expires, NOW)
    assert invite_open(expires, expires - timedelta(microseconds=1))
    assert not invite_open(expires, expires)
    assert not invite_open(None, NOW)


@pytest.mark.parametrize(
    ("value", "target_date", "today", "percent", "standing"),
    [
        (0, date(2027, 11, 13), date(2026, 11, 13), 0, Standing.ON_TRACK),
        (0, date(2027, 11, 13), date(2027, 5, 13), 0, Standing.BEHIND),
        (5_000_000, date(2027, 11, 13), date(2027, 5, 13), 50, Standing.ON_TRACK),
        (10_000_000, date(2027, 11, 13), date(2027, 5, 13), 100, Standing.REACHED),
        (12_000_000, None, date(2027, 5, 13), 100, Standing.REACHED),
        (1_000_000, None, date(2027, 5, 13), 10, Standing.NO_DATE),
    ],
)
def test_a_groups_progress_follows_the_rule_for_any_goal(
    value: int, target_date: date | None, today: date, percent: int, standing: Standing
) -> None:
    found = group_progress(
        target=ugx(10_000_000),
        value=ugx(value),
        set_up_on=date(2026, 11, 13),
        target_date=target_date,
        today=today,
    )
    assert (found.value, found.percent, found.standing) == (ugx(value), percent, standing)
