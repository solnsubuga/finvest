"""The Treasury bill auction calendar: which changes may be proposed and decided, what each date's
state is, and each product's next auction. Pure: no I/O.

The Bank of Uganda auctions Treasury bills on dates it publishes. One member of staff proposes a
date for a product, and a different one approves it before the desk counts it. A date the Bank
moves is cancelled the same way: one person proposes the cancellation with a reason, and another
approves it. The person who proposed a change may withdraw it while it waits.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

ChangeAction = Literal["add", "cancel"]
Status = Literal["proposed", "approved", "declined", "withdrawn"]
Step = Literal["approve", "decline", "withdraw", "cancel"]
# What a date is now: waiting for a second person, in the calendar, with a cancellation waiting,
# cancelled, or never in the calendar because it was declined or withdrawn.
State = Literal["waiting", "standing", "cancelling", "cancelled", "declined", "withdrawn"]


class Refusal(StrEnum):
    NOT_A_TREASURY_BILL = "not_a_treasury_bill"
    DATE_PASSED = "auction_date_passed"
    ALREADY_ENTERED = "auction_already_entered"
    NOT_STANDING = "auction_not_standing"
    CANCELLATION_WAITING = "cancellation_waiting"
    REASON_REQUIRED = "reason_required"
    NOTE_REQUIRED = "note_required"
    NOT_WAITING = "not_waiting"
    OWN_CHANGE = "own_change"
    NOT_YOURS = "not_yours"


@dataclass(frozen=True, slots=True)
class Change:
    """One proposed change to the calendar: a date to add, or a standing date to cancel."""

    id: str
    action: ChangeAction
    product_code: str
    auction_date: date
    # For a cancellation, the date it cancels.
    cancels: str | None
    reason: str | None
    status: Status
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


def cancellation_of(date_id: str, changes: Iterable[Change]) -> Change | None:
    """The cancellation waiting or approved for a date, else the latest declined or withdrawn."""
    found = [c for c in changes if c.action == "cancel" and c.cancels == date_id]
    live = [c for c in found if c.status in ("proposed", "approved")]
    if live:
        return live[0]
    return max(found, key=lambda c: (c.proposed_at, c.id), default=None)


def state_of(entry: Change, changes: Iterable[Change]) -> State:
    """The state of a date, from the change that added it and any cancellation of it."""
    if entry.status == "proposed":
        return "waiting"
    if entry.status in ("declined", "withdrawn"):
        return entry.status
    cancellation = cancellation_of(entry.id, changes)
    if cancellation is not None and cancellation.status == "approved":
        return "cancelled"
    if cancellation is not None and cancellation.status == "proposed":
        return "cancelling"
    return "standing"


def _in_calendar(entry: Change, changes: Sequence[Change]) -> bool:
    """Proposed, or standing with or without a cancellation waiting."""
    return entry.action == "add" and state_of(entry, changes) in (
        "waiting",
        "standing",
        "cancelling",
    )


def check_new(
    product_code: str,
    auction_date: date,
    *,
    treasury_bill: bool,
    today: date,
    changes: Iterable[Change],
) -> Refusal | None:
    """Whether a date may be proposed for a product. Today counts: bids close during the day."""
    if not treasury_bill:
        return Refusal.NOT_A_TREASURY_BILL
    if auction_date < today:
        return Refusal.DATE_PASSED
    changes = list(changes)
    if any(
        c.product_code == product_code
        and c.auction_date == auction_date
        and _in_calendar(c, changes)
        for c in changes
    ):
        return Refusal.ALREADY_ENTERED
    return None


def check_cancel(entry: Change, reason: str, changes: Iterable[Change]) -> Refusal | None:
    """Whether a cancellation of a date may be proposed."""
    changes = list(changes)
    state = state_of(entry, changes) if entry.action == "add" else None
    if state == "cancelling":
        return Refusal.CANCELLATION_WAITING
    if state != "standing":
        return Refusal.NOT_STANDING
    if not reason.strip():
        return Refusal.REASON_REQUIRED
    return None


def check_decision(
    change: Change, step: Step, staff_id: str, today: date, *, note: str = ""
) -> Refusal | None:
    """Whether this person may approve, decline or withdraw a waiting change."""
    if change.status != "proposed":
        return Refusal.NOT_WAITING
    own = change.proposed_by == staff_id
    if step == "withdraw":
        return None if own else Refusal.NOT_YOURS
    if own:
        return Refusal.OWN_CHANGE
    if step == "decline" and not note.strip():
        return Refusal.NOTE_REQUIRED
    # A date proposed for a day that has since passed can only be declined or withdrawn.
    if step == "approve" and change.action == "add" and change.auction_date < today:
        return Refusal.DATE_PASSED
    return None


def allowed(change: Change, changes: Iterable[Change], staff_id: str, today: date) -> list[Step]:
    """The steps this person may take on a change, for the console's buttons."""
    if change.status == "proposed":
        if change.proposed_by == staff_id:
            return ["withdraw"]
        if change.action == "add" and change.auction_date < today:
            return ["decline"]
        return ["approve", "decline"]
    standing = change.action == "add" and state_of(change, changes) == "standing"
    if standing and change.auction_date >= today:
        return ["cancel"]
    return []


def next_auctions(changes: Iterable[Change], today: date) -> dict[str, date]:
    """Each product's earliest standing auction on or after today.

    A date waiting for its second person does not count yet. A date whose cancellation waits
    still does, until the cancellation is approved."""
    changes = list(changes)
    found: dict[str, date] = {}
    for c in changes:
        if c.action != "add" or c.auction_date < today:
            continue
        if state_of(c, changes) not in ("standing", "cancelling"):
            continue
        if c.product_code not in found or c.auction_date < found[c.product_code]:
            found[c.product_code] = c.auction_date
    return found
