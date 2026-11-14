"""Changes to the product catalogue and who may make them. Pure: no I/O.

Staff never change a product directly. One member of staff proposes a change and a different
one approves it, exactly as for a rate. A change is one of four actions:

* `create`: a new product, with its first rate. It is created as a draft, which clients and
  the website never see, so a second approval is needed to open it.
* `edit`: new terms for an existing product. The code and currency never change, because
  investments, goals, plans and the ledger refer to them. The kind and when interest is paid
  change only while the product is a draft: once clients have been told them, they stay.
* `pause`: no new investments. Running ones carry on to maturity.
* `open`: clients can start new investments. The product needs a rate in force, and its
  currency must be one Finvest can take payments in and pay out in.

A proposal names the whole product as it will be after the change. It also names the version of
the product it was made against, so approving a proposal that another change has overtaken is
refused: the second person approves exactly what they were shown.

A term change affects new investments only. An investment keeps the terms it started with.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from app.modules.investments.domain.products import Product, ProductStatus
from app.modules.investments.domain.rates import ProposalStatus, RateVersion, valid_reason
from app.shared.money import Currency

# Paying in and paying out exist in shillings only. The dollar product stays paused until both
# exist in dollars, then its currency is added here and it can be opened like any other.
CURRENCIES_THAT_CAN_OPEN: frozenset[Currency] = frozenset({Currency.UGX})


class ProductAction(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    PAUSE = "pause"
    OPEN = "open"


class Refusal(StrEnum):
    REASON_REQUIRED = "reason_required"
    # A product with this code already exists, or is waiting to be created.
    CODE_TAKEN = "code_taken"
    # A new product starts as a draft.
    NOT_A_DRAFT = "not_a_draft"
    # The code or currency of an existing product never changes.
    CODE_FIXED = "code_fixed"
    CURRENCY_FIXED = "currency_fixed"
    # The kind and when interest is paid change only while the product is a draft.
    KIND_FIXED = "kind_fixed"
    INTEREST_PAID_FIXED = "interest_paid_fixed"
    # An edit changes the terms. Pausing and opening are their own actions.
    STATUS_FIXED = "status_fixed"
    # The product would be the same as it is now.
    NOTHING_CHANGES = "nothing_changes"
    ALREADY_LIVE = "already_live"
    NOT_LIVE = "not_live"
    # Opening needs a rate that has started.
    NO_RATE = "no_rate"
    # Opening needs paying in and out in the product's currency.
    CURRENCY_NOT_OPEN = "currency_not_open"
    # Another change was approved after this one was proposed.
    PRODUCT_CHANGED = "product_changed"
    ALREADY_DECIDED = "already_decided"
    SAME_PERSON = "same_person"
    NOT_THE_PROPOSER = "not_the_proposer"


@dataclass(frozen=True, slots=True)
class Current:
    """A product as it stands, with the version a change is made against."""

    product: Product
    version: int
    # Whether a rate is in force for it today.
    has_rate: bool


@dataclass(frozen=True, slots=True)
class ProductChange:
    action: ProductAction
    # The product as it will be once the change is approved.
    target: Product
    reason: str
    proposed_by: str
    # The version of the product this change was made against. None for a new product.
    base_version: int | None = None
    # A new product's first rate. None for every other action.
    first_rate: RateVersion | None = None
    status: ProposalStatus = ProposalStatus.PROPOSED


def check_create(target: Product, reason: str, *, code_taken: bool) -> Refusal | None:
    """Whether a new product may be proposed. Its first rate was checked when it was built."""
    if not valid_reason(reason):
        return Refusal.REASON_REQUIRED
    if target.status is not ProductStatus.DRAFT:
        return Refusal.NOT_A_DRAFT
    if code_taken:
        return Refusal.CODE_TAKEN
    return None


def check_edit(current: Current, target: Product, reason: str) -> Refusal | None:
    """Whether new terms for an existing product may be proposed, or approved."""
    if not valid_reason(reason):
        return Refusal.REASON_REQUIRED
    now = current.product
    if target.code != now.code:
        return Refusal.CODE_FIXED
    if target.currency is not now.currency:
        return Refusal.CURRENCY_FIXED
    if target.status is not now.status:
        return Refusal.STATUS_FIXED
    if now.status is not ProductStatus.DRAFT:
        if target.kind is not now.kind:
            return Refusal.KIND_FIXED
        if target.interest_paid is not now.interest_paid:
            return Refusal.INTEREST_PAID_FIXED
    if target == now:
        return Refusal.NOTHING_CHANGES
    return None


def check_pause(current: Current, reason: str) -> Refusal | None:
    if not valid_reason(reason):
        return Refusal.REASON_REQUIRED
    return None if current.product.status is ProductStatus.LIVE else Refusal.NOT_LIVE


def check_open(current: Current, reason: str) -> Refusal | None:
    if not valid_reason(reason):
        return Refusal.REASON_REQUIRED
    product = current.product
    if product.status is ProductStatus.LIVE:
        return Refusal.ALREADY_LIVE
    if product.currency not in CURRENCIES_THAT_CAN_OPEN:
        return Refusal.CURRENCY_NOT_OPEN
    if not current.has_rate:
        return Refusal.NO_RATE
    return None


def with_status(product: Product, status: ProductStatus) -> Product:
    return replace(product, status=status)


def check_proposal(
    change: ProductChange, current: Current | None, *, code_taken: bool
) -> Refusal | None:
    """Whether `change` may be saved, against the product as it stands now."""
    if change.action is ProductAction.CREATE:
        assert change.first_rate is not None
        return check_create(change.target, change.reason, code_taken=code_taken)
    assert current is not None
    return _check_against(change, current)


def check_approval(
    change: ProductChange, approver: str, current: Current | None, *, code_taken: bool
) -> Refusal | None:
    """Whether `approver` may approve `change` now.

    Everything is checked again, because rates start and other changes are approved between the
    proposal and its approval. A change made against an older version is refused outright.
    """
    if change.status.is_final:
        return Refusal.ALREADY_DECIDED
    if approver == change.proposed_by:
        return Refusal.SAME_PERSON
    if change.action is ProductAction.CREATE:
        return check_proposal(change, None, code_taken=code_taken)
    assert current is not None
    if current.version != change.base_version:
        return Refusal.PRODUCT_CHANGED
    return _check_against(change, current)


def check_send_back(change: ProductChange, by: str, note: str) -> Refusal | None:
    if change.status.is_final:
        return Refusal.ALREADY_DECIDED
    if by == change.proposed_by:
        return Refusal.SAME_PERSON
    return None if valid_reason(note) else Refusal.REASON_REQUIRED


def check_withdrawal(change: ProductChange, by: str) -> Refusal | None:
    if change.status.is_final:
        return Refusal.ALREADY_DECIDED
    return None if by == change.proposed_by else Refusal.NOT_THE_PROPOSER


def _check_against(change: ProductChange, current: Current) -> Refusal | None:
    if change.action is ProductAction.EDIT:
        return check_edit(current, change.target, change.reason)
    # Pausing and opening change the status and nothing else.
    if change.action is ProductAction.PAUSE:
        expected = with_status(current.product, ProductStatus.PAUSED)
        refusal = check_pause(current, change.reason)
    else:
        expected = with_status(current.product, ProductStatus.LIVE)
        refusal = check_open(current, change.reason)
    if refusal is None and change.target != expected:
        return Refusal.PRODUCT_CHANGED
    return refusal
