"""The quarter's complaints by category, for the return to the regulator. Pure."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.modules.support.domain.complaints import CATEGORIES, working_days_between


@dataclass(frozen=True, slots=True)
class Complaint:
    category: str
    received_on: date
    due_on: date
    closed_on: date | None
    outcome: str | None


@dataclass(frozen=True, slots=True)
class CategoryLine:
    category: str
    received: int
    resolved: int
    upheld: int
    partly_upheld: int
    not_upheld: int
    open_at_end: int
    # Resolved after their deadline, or still open past it at the end of the quarter.
    past_deadline: int
    # Working days from received to resolved, averaged, in tenths, rounded half up.
    average_days_to_resolve_tenths: int | None

    @property
    def average_days(self) -> str:
        tenths = self.average_days_to_resolve_tenths
        return "" if tenths is None else f"{tenths // 10}.{tenths % 10}"


def lines(found: Iterable[Complaint], *, first: date, last: date) -> list[CategoryLine]:
    """One line per category in the catalogue's order, then `all`. Every category is listed,
    even at zero, so one quarter's return lines up with the next."""
    complaints = list(found)
    by_category = [
        _line(category, [c for c in complaints if c.category == category], first, last)
        for category in CATEGORIES
    ]
    return [*by_category, _line("all", complaints, first, last)]


def _line(category: str, complaints: list[Complaint], first: date, last: date) -> CategoryLine:
    received = [c for c in complaints if first <= c.received_on <= last]
    resolved = [c for c in complaints if c.closed_on is not None and first <= c.closed_on <= last]
    still_open = [
        c
        for c in complaints
        if c.received_on <= last and (c.closed_on is None or c.closed_on > last)
    ]
    late = [c for c in resolved if c.closed_on is not None and c.closed_on > c.due_on] + [
        c for c in still_open if c.due_on < last
    ]
    days = [
        working_days_between(c.received_on, c.closed_on)
        for c in resolved
        if c.closed_on is not None
    ]
    return CategoryLine(
        category=category,
        received=len(received),
        resolved=len(resolved),
        upheld=sum(1 for c in resolved if c.outcome == "upheld"),
        partly_upheld=sum(1 for c in resolved if c.outcome == "partly_upheld"),
        not_upheld=sum(1 for c in resolved if c.outcome == "not_upheld"),
        open_at_end=len(still_open),
        past_deadline=len(late),
        average_days_to_resolve_tenths=(
            (sum(days) * 20 + len(days)) // (2 * len(days)) if days else None
        ),
    )
