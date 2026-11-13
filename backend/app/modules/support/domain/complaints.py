"""Rules for the complaints register. Pure: no I/O.

A complaint is a regulated record, kept apart from help chat. It always has a reference, a
date by which Finvest promised to answer, and, once closed, an outcome, what was done and
whether the client accepted it. A closed complaint never changes.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, get_args

from app.shared.money import Money

Status = Literal["new", "investigating", "waiting_for_client", "escalated", "closed"]
STATUSES: tuple[Status, ...] = get_args(Status)
OPEN_STATUSES: tuple[Status, ...] = tuple(status for status in STATUSES if status != "closed")
Category = Literal[
    "payments", "withdrawals", "investments", "identity", "account", "fees", "service", "other"
]
CATEGORIES: tuple[Category, ...] = get_args(Category)
# How the complaint reached Finvest.
ReceivedVia = Literal["app", "chat", "phone", "email", "in_person", "letter"]
RECEIVED_VIA: tuple[ReceivedVia, ...] = get_args(ReceivedVia)
# Whether the client was right.
Outcome = Literal["upheld", "partly_upheld", "not_upheld"]
OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)

# The working default until the regulator's own deadline is confirmed. See docs/decisions.md.
WORKING_DAYS_TO_REPLY = 5
MAX_SUMMARY = 120
MAX_DETAILS = 4000
MAX_NOTE = 2000
MAX_RESOLUTION = 2000

_MOVES: dict[Status, frozenset[Status]] = {
    "new": frozenset({"investigating", "waiting_for_client", "escalated", "closed"}),
    "investigating": frozenset({"waiting_for_client", "escalated", "closed"}),
    "waiting_for_client": frozenset({"investigating", "escalated", "closed"}),
    "escalated": frozenset({"investigating", "waiting_for_client", "closed"}),
    "closed": frozenset(),
}


class InvalidComplaint(ValueError):
    def __init__(self, field: str, code: str) -> None:
        super().__init__(code)
        self.field = field
        self.code = code


def can_move(current: Status, target: Status) -> bool:
    return target in _MOVES[current]


def add_working_days(start: date, days: int) -> date:
    """`days` working days after `start`. Monday to Friday. Public holidays are not known yet."""
    if days < 0:
        raise ValueError("Working days are counted forwards")
    day = start
    while days:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days -= 1
    return day


def reply_due(received_on: date) -> date:
    return add_working_days(received_on, WORKING_DAYS_TO_REPLY)


def is_overdue(status: Status, due_on: date, today: date) -> bool:
    return status != "closed" and today > due_on


def _text(field: str, value: str, limit: int) -> str:
    cleaned = value.replace("\r\n", "\n").strip()
    if not cleaned:
        raise InvalidComplaint(field, "required")
    if len(cleaned) > limit:
        raise InvalidComplaint(field, "too_long")
    return cleaned


@dataclass(frozen=True, slots=True)
class NewComplaint:
    category: Category
    summary: str
    details: str
    received_via: ReceivedVia
    amount: Money | None = None

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise InvalidComplaint("category", "unknown")
        if self.received_via not in RECEIVED_VIA:
            raise InvalidComplaint("received_via", "unknown")
        object.__setattr__(
            self, "summary", " ".join(_text("summary", self.summary, MAX_SUMMARY).split())
        )
        object.__setattr__(self, "details", _text("details", self.details, MAX_DETAILS))
        if self.amount is not None and not self.amount.is_positive:
            raise InvalidComplaint("amount", "must_be_positive")


@dataclass(frozen=True, slots=True)
class Closure:
    """What closing a complaint must record."""

    outcome: Outcome
    resolution: str
    client_accepted: bool

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise InvalidComplaint("outcome", "unknown")
        if type(self.client_accepted) is not bool:
            raise InvalidComplaint("client_accepted", "required")
        object.__setattr__(self, "resolution", _text("resolution", self.resolution, MAX_RESOLUTION))


def note(value: str) -> str:
    return _text("note", value, MAX_NOTE)


def working_days_between(start: date, end: date) -> int:
    """Working days from `start` to `end`, for the time a complaint took to resolve."""
    if end < start:
        return 0
    days, day = 0, start
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days += 1
    return days
