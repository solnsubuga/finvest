"""Changes to bond issues and their prices, and who may make them. Pure: no I/O.

Staff never change a bond issue or its price directly. One member of staff proposes and a
different one approves, or sends it back with a note; only the proposer withdraws. A change is
one of four actions:

* `create`: a new issue, with its first price. It is created as a draft, which clients and the
  website never see, so a second approval is needed to open it.
* `price`: today's price for an issue, from the auction result or the dealing desk's quote. It
  takes effect when it is approved. A client's quote keeps the price it was made at for the
  payment window.
* `pause`: no new buying. Holdings carry on to maturity and are paid their coupons.
* `open`: clients can buy. The issue needs a price, and must not have entered its last period.

An issue's ISIN, coupon, dates and tax never change once created: clients hold them. A typing
mistake on a draft is put right by leaving it paused and creating it again under a new code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.bonds.domain.issues import BondIssue, BondStatus, check_price

MAX_REASON_LENGTH = 500
# Paying in and out exists in shillings only.
CURRENCIES_THAT_CAN_OPEN = frozenset({"UGX"})


class BondChangeAction(StrEnum):
    CREATE = "create"
    PRICE = "price"
    PAUSE = "pause"
    OPEN = "open"


class BondChangeStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"


class Refusal(StrEnum):
    REASON_REQUIRED = "reason_required"
    NOTE_REQUIRED = "note_required"
    CODE_TAKEN = "code_taken"
    ISIN_TAKEN = "isin_taken"
    NOT_A_DRAFT = "not_a_draft"
    ALREADY_LIVE = "already_live"
    NOT_LIVE = "not_live"
    NO_PRICE = "no_price"
    SAME_PRICE = "same_price"
    CURRENCY_NOT_OPEN = "currency_not_open"
    MATURED = "matured"
    # Another change for the issue waits for a second person.
    ALREADY_WAITING = "already_waiting"
    # The change is decided, or was made against an issue that has moved on.
    NOT_WAITING = "not_waiting"
    STALE = "stale"
    OWN_PROPOSAL = "own_proposal"
    NOT_PROPOSER = "not_proposer"


def valid_text(text: str | None) -> bool:
    return text is not None and bool(text.strip()) and len(text) <= MAX_REASON_LENGTH


@dataclass(frozen=True, slots=True)
class Current:
    """An issue as it is now, for checking a change against."""

    issue: BondIssue
    version: int
    price: int | None


def check_create(
    issue: BondIssue, price: int, *, code_taken: bool, isin_taken: bool
) -> Refusal | None:
    check_price(price)
    if code_taken:
        return Refusal.CODE_TAKEN
    if isin_taken:
        return Refusal.ISIN_TAKEN
    if issue.status is not BondStatus.DRAFT:
        return Refusal.NOT_A_DRAFT
    return None


def check_price_change(current: Current, price: int) -> Refusal | None:
    check_price(price)
    if current.price == price:
        return Refusal.SAME_PRICE
    return None


def check_status_change(
    current: Current, action: BondChangeAction, *, can_be_bought: bool
) -> Refusal | None:
    """Pausing needs a live issue. Opening needs one not live, priced, in a currency Finvest
    pays in and out, and not in its last coupon period."""
    status = current.issue.status
    if action is BondChangeAction.PAUSE:
        return None if status is BondStatus.LIVE else Refusal.NOT_LIVE
    if status is BondStatus.LIVE:
        return Refusal.ALREADY_LIVE
    if current.issue.currency.value not in CURRENCIES_THAT_CAN_OPEN:
        return Refusal.CURRENCY_NOT_OPEN
    if current.price is None:
        return Refusal.NO_PRICE
    if not can_be_bought:
        return Refusal.MATURED
    return None


def check_decision(
    *, status: BondChangeStatus, proposed_by: str, decided_by: str
) -> Refusal | None:
    """A second person decides a waiting change."""
    if status is not BondChangeStatus.PROPOSED:
        return Refusal.NOT_WAITING
    if proposed_by == decided_by:
        return Refusal.OWN_PROPOSAL
    return None


def check_withdrawal(*, status: BondChangeStatus, proposed_by: str, by: str) -> Refusal | None:
    if status is not BondChangeStatus.PROPOSED:
        return Refusal.NOT_WAITING
    if proposed_by != by:
        return Refusal.NOT_PROPOSER
    return None


def status_after(action: BondChangeAction, status: BondStatus) -> BondStatus:
    if action is BondChangeAction.PAUSE:
        return BondStatus.PAUSED
    if action is BondChangeAction.OPEN:
        return BondStatus.LIVE
    return status
