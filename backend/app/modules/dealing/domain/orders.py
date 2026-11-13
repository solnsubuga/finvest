"""Orders to buy and sell bills, and the people who must agree to them. Pure: no I/O.

No bill is bought or sold on one person's word. An order is prepared by one member of staff and
approved by another before it goes to the primary dealer. What the dealer did is then recorded
from the custodian's note by one person and confirmed by a different one. Only a confirmed
result moves money in the ledger and allocates bills to clients.

| Status | Meaning |
| --- | --- |
| Prepared | Waiting for a second person |
| Approved | May be placed with the dealer. Nothing has been bought or sold yet. |
| Sent back | The second person declined it, with a note. A new order is prepared. |
| Withdrawn | The preparer took it back before anyone decided |
| Result recorded | Someone entered what the custodian's note says. Waiting to be confirmed. |
| Settled | A second person confirmed the result. The ledger and the allocations follow. |
| Unsuccessful | The bid won nothing, or the order was not dealt. No money moved. |
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.shared.money import Money

MAX_NOTE_LENGTH = 500
MAX_REFERENCE_LENGTH = 64


class InvalidOrder(ValueError):
    """An order or a result that contradicts itself."""


class OrderKind(StrEnum):
    # A bid at a Bank of Uganda auction, placed through the primary dealer.
    AUCTION_BID = "auction_bid"
    SECONDARY_PURCHASE = "secondary_purchase"
    # Selling bills held, to pay early withdrawals or refill the buffer.
    SECONDARY_SALE = "secondary_sale"

    @property
    def buys(self) -> bool:
        return self is not OrderKind.SECONDARY_SALE


class OrderStatus(StrEnum):
    PREPARED = "prepared"
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"
    RESULT_RECORDED = "result_recorded"
    SETTLED = "settled"
    UNSUCCESSFUL = "unsuccessful"

    @property
    def is_final(self) -> bool:
        return self in _FINAL

    @property
    def is_open(self) -> bool:
        """Counts towards "on order" in the position."""
        return not self.is_final


_FINAL = frozenset(
    {
        OrderStatus.SENT_BACK,
        OrderStatus.WITHDRAWN,
        OrderStatus.SETTLED,
        OrderStatus.UNSUCCESSFUL,
    }
)


@dataclass(frozen=True, slots=True)
class Order:
    kind: OrderKind
    product_code: str
    # The face value to bid for, buy or sell.
    face: Money
    prepared_by: str
    status: OrderStatus = OrderStatus.PREPARED
    result_recorded_by: str | None = None

    def __post_init__(self) -> None:
        if not self.product_code or not self.prepared_by:
            raise InvalidOrder("An order names its product and who prepared it")
        if not self.face.is_positive:
            raise InvalidOrder("An order is for a positive face value")
        if (self.status is OrderStatus.RESULT_RECORDED) != (self.result_recorded_by is not None):
            raise InvalidOrder("A recorded result names who recorded it, and nothing else does")


@dataclass(frozen=True, slots=True)
class Fill:
    """What was actually dealt, read from the custodian's note."""

    face: Money
    # Paid for a purchase, received for a sale.
    amount: Money
    settled_on: date
    matures_on: date
    custodian_reference: str

    def __post_init__(self) -> None:
        if self.face.currency is not self.amount.currency:
            raise InvalidOrder("Face value and cash are in one currency")
        if not self.face.is_positive or not self.amount.is_positive:
            raise InvalidOrder("A fill has a positive face value and cash amount")
        if self.matures_on <= self.settled_on:
            raise InvalidOrder("A bill matures after it settles")
        reference = self.custodian_reference
        if not reference.strip() or reference != reference.strip():
            raise InvalidOrder("A fill carries the custodian's reference")
        if len(reference) > MAX_REFERENCE_LENGTH:
            raise InvalidOrder("The custodian's reference is too long")


class Refusal(StrEnum):
    # The face value is not a whole number of the smallest step the market accepts.
    NOT_A_WHOLE_LOT = "not_a_whole_lot"
    # A sale of more than is held.
    MORE_THAN_HELD = "more_than_held"
    NOTE_REQUIRED = "note_required"
    # The order is not at the step this action belongs to.
    WRONG_STATUS = "wrong_status"
    # Nobody approves their own order, or confirms a result they recorded.
    SAME_PERSON = "same_person"
    NOT_THE_PREPARER = "not_the_preparer"
    CURRENCY_MISMATCH = "currency_mismatch"
    # More was dealt than the order allowed.
    OVER_THE_ORDER = "over_the_order"
    # A Treasury bill never costs or fetches more than its face value.
    ABOVE_FACE = "above_face"
    # The result settles on a day that has not come yet.
    SETTLES_IN_THE_FUTURE = "settles_in_the_future"


def check_new_order(order: Order, lot_size: Money, face_held: Money) -> Refusal | None:
    """Whether a newly prepared order may be saved.

    `face_held` is the face value held in the product that is not already in another open
    sale. It matters only for a sale.
    """
    if order.status is not OrderStatus.PREPARED:
        return Refusal.WRONG_STATUS
    if lot_size.currency is not order.face.currency or (
        face_held.currency is not order.face.currency
    ):
        return Refusal.CURRENCY_MISMATCH
    if not lot_size.is_positive:
        raise InvalidOrder("The lot size is a positive amount")
    if order.face.minor % lot_size.minor != 0:
        return Refusal.NOT_A_WHOLE_LOT
    if not order.kind.buys and order.face > face_held:
        return Refusal.MORE_THAN_HELD
    return None


def check_approval(order: Order, by: str) -> Refusal | None:
    if order.status is not OrderStatus.PREPARED:
        return Refusal.WRONG_STATUS
    return Refusal.SAME_PERSON if by == order.prepared_by else None


def check_send_back(order: Order, by: str, note: str) -> Refusal | None:
    if order.status is not OrderStatus.PREPARED:
        return Refusal.WRONG_STATUS
    if by == order.prepared_by:
        return Refusal.SAME_PERSON
    return None if valid_note(note) else Refusal.NOTE_REQUIRED


def check_withdrawal(order: Order, by: str) -> Refusal | None:
    if order.status is not OrderStatus.PREPARED:
        return Refusal.WRONG_STATUS
    return None if by == order.prepared_by else Refusal.NOT_THE_PREPARER


def check_result(
    order: Order, fill: Fill | None, today: date, *, discount_only: bool
) -> Refusal | None:
    """Whether a result may be recorded against an approved order.

    `fill` of `None` means nothing was dealt, which ends the order as unsuccessful. A bid may be
    filled in part. `discount_only` is true for Treasury bills, which never trade above their
    face value. A bond may.
    """
    if order.status is not OrderStatus.APPROVED:
        return Refusal.WRONG_STATUS
    if fill is None:
        return None
    if fill.face.currency is not order.face.currency:
        return Refusal.CURRENCY_MISMATCH
    if fill.face > order.face:
        return Refusal.OVER_THE_ORDER
    if discount_only and fill.amount > fill.face:
        return Refusal.ABOVE_FACE
    if fill.settled_on > today:
        return Refusal.SETTLES_IN_THE_FUTURE
    return None


def check_confirmation(order: Order, by: str) -> Refusal | None:
    """Whether `by` may confirm the recorded result, which settles the order."""
    if order.status is not OrderStatus.RESULT_RECORDED:
        return Refusal.WRONG_STATUS
    return Refusal.SAME_PERSON if by == order.result_recorded_by else None


def check_result_rejection(order: Order, by: str, note: str) -> Refusal | None:
    """Whether `by` may reject a recorded result that does not match the custodian's note.

    The order goes back to approved and the result is recorded again. Nothing was posted.
    """
    refusal = check_confirmation(order, by)
    if refusal is not None:
        return refusal
    return None if valid_note(note) else Refusal.NOTE_REQUIRED


def valid_note(text: str) -> bool:
    return isinstance(text, str) and len(text.strip()) > 0 and len(text) <= MAX_NOTE_LENGTH
