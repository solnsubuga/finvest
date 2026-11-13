"""A group goal: one target that several clients save towards together. Pure: no I/O.

A group holds no money, and neither does a member. Each member saves into a goal of their own,
whose savings are their own investments, and can leave with them at any time. The group adds
only the shared target and how far the members are together. Nothing is pooled, nothing needs
anyone's approval, and nothing moves between members.

    active ──► closed

* `active`: members join with an invite, save into their own goals and see the group's
  progress.
* `closed`: the one who set it up stopped it. Final. Every member's goal carries on as their
  own.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.modules.goals.domain.goals import Standing, progress
from app.shared.money import Money

# Members at once, the one who set it up included. A family or a few friends, not a scheme.
MAX_MEMBERS = 10
MAX_DISPLAY_NAME_LENGTH = 30
# How long an invite can be used for. Sent on by WhatsApp or SMS, so it should not live long.
INVITE_LIFETIME = timedelta(days=7)


class GroupStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class MemberRefusal(StrEnum):
    DISPLAY_NAME_EMPTY = "display_name_empty"
    DISPLAY_NAME_TOO_LONG = "display_name_too_long"


@dataclass(frozen=True, slots=True)
class GroupProgress:
    """How far the members are together. Figures after tax, from their running investments."""

    value: Money
    # Whole percent of the target reached, 0 to 100.
    percent: int
    standing: Standing


def clean_display_name(name: str) -> str:
    return " ".join(name.split())


def check_display_name(name: str) -> MemberRefusal | None:
    cleaned = clean_display_name(name)
    if not cleaned:
        return MemberRefusal.DISPLAY_NAME_EMPTY
    if len(cleaned) > MAX_DISPLAY_NAME_LENGTH:
        return MemberRefusal.DISPLAY_NAME_TOO_LONG
    return None


def invite_hash(code: str) -> bytes:
    """What is stored and looked up: the code's SHA-256. A code is random, so no salt is needed."""
    return hashlib.sha256(code.encode()).digest()


def invite_expires(now: datetime) -> datetime:
    return now + INVITE_LIFETIME


def invite_open(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and now < expires_at


def group_progress(
    *, target: Money, value: Money, set_up_on: date, target_date: date | None, today: date
) -> GroupProgress:
    """Where a group stands, by the same rule as a goal of one's own."""
    zero = Money.zero(target.currency)
    found = progress(
        target=target,
        value=value,
        interest=zero,
        awaiting_payment=zero,
        set_up_on=set_up_on,
        target_date=target_date,
        today=today,
    )
    return GroupProgress(value=found.value, percent=found.percent, standing=found.standing)
