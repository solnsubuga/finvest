"""The daily close: two people agree that the books and the statements tie. Pure: no I/O.

For each pot of money (an ioTec wallet, the trust bank account, the custodian) the close holds
the statement's closing balance, the ledger's balance at the end of the same day, and the
exceptions still open. The day can be closed only when every shilling of difference between
statement and ledger is accounted for by an open exception. One staff member prepares the
close and a different one approves it. A closed day is never reopened: a later correction is a
new ledger entry on a later day.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.money import Money


class CloseStatus(StrEnum):
    OPEN = "open"
    PREPARED = "prepared"  # one person has signed; waiting for a second
    CLOSED = "closed"


class CloseError(Exception):
    """A step the day close does not allow. `code` is safe to log and to return."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PotPosition:
    """One pot at the end of the day."""

    source: StatementSource
    account_reference: str
    # None until that day's statement has been imported.
    statement_closing: Money | None
    ledger_closing: Money
    # The sum of `ReconciliationException.difference` over every exception still open for this
    # pot, dated on or before the day: how far above the books they say the statement should be.
    open_exceptions_total: Money
    open_exceptions_count: int

    @property
    def unexplained(self) -> Money | None:
        """What no open exception accounts for. Must be zero to close. None with no statement."""
        if self.statement_closing is None:
            return None
        return self.statement_closing - self.ledger_closing - self.open_exceptions_total


@dataclass(frozen=True, slots=True)
class DayClose:
    business_date: date
    status: CloseStatus = CloseStatus.OPEN
    prepared_by: str | None = None
    approved_by: str | None = None


def blockers(
    positions: Sequence[PotPosition],
    *,
    balance_drift_accounts: int,
    previous_day_closed: bool,
    required: Sequence[tuple[StatementSource, str]],
) -> tuple[str, ...]:
    """Every reason the day cannot be closed yet, as codes. Empty means it can.

    `required` lists the pots that must be present, so a close cannot pass by leaving one out.
    `balance_drift_accounts` is the count from the ledger's own check that every cached balance
    equals the sum of its lines.
    """
    found: list[str] = []
    if not previous_day_closed:
        found.append("previous_day_not_closed")
    if balance_drift_accounts != 0:
        found.append("ledger_balance_drift")
    present = [(position.source, position.account_reference) for position in positions]
    if len(set(present)) != len(present):
        found.append("pot_listed_twice")
    if set(required) - set(present):
        found.append("pot_missing")
    for position in positions:
        unexplained = position.unexplained
        if unexplained is None:
            found.append("statement_missing")
        elif not unexplained.is_zero:
            found.append("difference_unexplained")
    return tuple(dict.fromkeys(found))


def prepare(close: DayClose, staff_id: str, blocking: Sequence[str]) -> DayClose:
    """The first signature. Refused while anything blocks the close."""
    if close.status is not CloseStatus.OPEN:
        raise CloseError("close_not_open")
    if blocking:
        raise CloseError(blocking[0])
    return replace(close, status=CloseStatus.PREPARED, prepared_by=staff_id)


def approve(close: DayClose, staff_id: str, blocking: Sequence[str]) -> DayClose:
    """The second signature, by a different person, checked against the figures as they are now.

    The blockers are worked out again at approval. If a late statement line or ledger entry
    changed the picture after the first signature, the close goes back to be prepared again.
    """
    if close.status is not CloseStatus.PREPARED:
        raise CloseError("close_not_prepared")
    if staff_id == close.prepared_by:
        raise CloseError("close_needs_second_person")
    if blocking:
        raise CloseError("close_changed_since_prepared")
    return replace(close, status=CloseStatus.CLOSED, approved_by=staff_id)


def send_back(close: DayClose) -> DayClose:
    """The second person declined, or the figures changed. The close is open again."""
    if close.status is not CloseStatus.PREPARED:
        raise CloseError("close_not_prepared")
    return replace(close, status=CloseStatus.OPEN, prepared_by=None)


def may_post_on(effective_date: date, last_closed: date | None) -> bool:
    """A ledger entry may not be dated on a day that is already closed."""
    return last_closed is None or effective_date > last_closed


def next_day_to_close(last_closed: date | None, first_business_date: date) -> date:
    """Days are closed in order, with none skipped. Weekends and holidays are closed too."""
    return first_business_date if last_closed is None else last_closed + timedelta(days=1)
