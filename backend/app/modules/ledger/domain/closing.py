"""Closing a day. Pure: no I/O.

When a day has been reconciled and signed off, the ledger is closed through it. Nothing more can
be dated on or before that day, so the balance that was agreed cannot change afterwards. Closed
days only move forward and are never reopened. A mistake found later is corrected by an entry
dated in an open day.
"""

from dataclasses import dataclass
from datetime import date, datetime

from app.modules.ledger.domain.entries import InvalidEntry
from app.modules.ledger.domain.errors import DayNotEnded
from app.shared.actors import Actor
from app.shared.business_day import business_date


@dataclass(frozen=True, slots=True)
class ClosedDay:
    closed_through: date
    closed_by: str
    closed_at: datetime


def closing_staff_id(actor: Actor) -> str:
    """A day is closed by a person on the staff, never by a client or a job."""
    if actor.kind != "staff" or actor.id is None:
        raise InvalidEntry("Only a staff member can close a day")
    return actor.id


def check_can_close(day: date, now: datetime) -> None:
    """A day is closed only once it is over in Kampala. Today and future days stay open,
    because payments and payouts are still being dated today."""
    if day >= business_date(now):
        raise DayNotEnded("Only a day that has ended can be closed")


def may_post_on(effective_date: date, closed_through: date | None) -> bool:
    """Whether an entry may carry this date. The database applies the same rule."""
    return closed_through is None or effective_date > closed_through
