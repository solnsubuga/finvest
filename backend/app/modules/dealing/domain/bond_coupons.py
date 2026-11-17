"""Coupons Bank of Uganda pays in for the bonds the dealing desk holds. One member of staff
records each from the custodian's note, a different one approves it. Pure.

Bank of Uganda pays every holder of an issue the same coupon on the same dates, through the
custodian into the trust bank account. The desk is paid on the face value it holds the day
before the coupon date, whatever it paid for it. Finvest works out the coupon that face value
earns before tax (`expected`) and records what actually arrived (`amount`), as the statement
shows it. Whether Bank of Uganda withholds tax on the desk's coupons is not settled (see the
open question in docs/decisions.md), so what arrived is booked, and a difference needs a note.

| Debit | Credit |
| --- | --- |
| `bank.trust`, what arrived | `dealing.bond_coupons`, Finvest's income |

Clients' coupons are paid by the bonds module from `interest.expense` on the same dates, so the
two meet in Finvest's earnings. The entry is dated the day the money arrived and points back at
the coupon (source type `dealing.bond_coupon`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.bonds.domain.issues import BondIssue, coupon_dates
from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    credit,
    debit,
)
from app.shared.money import Currency, Money, Rounding

BANK_TRUST = "bank.trust"
COUPON_INCOME = "dealing.bond_coupons"
SOURCE_TYPE = "dealing.bond_coupon"
ENTRY_TYPE = "dealing.bond_coupon"
MAX_NOTE_LENGTH = 500
MAX_REFERENCE_LENGTH = 100
BASIS_POINTS = 10_000


class CouponStatus(StrEnum):
    RECORDED = "recorded"
    APPROVED = "approved"
    DECLINED = "declined"


class Refusal(StrEnum):
    # The date is not one of the issue's coupon dates, or has not come yet.
    NOT_A_COUPON_DATE = "not_a_coupon_date"
    COUPON_DATE_IN_FUTURE = "coupon_date_in_future"
    # The desk held none of the issue the day before the coupon date, so it is paid nothing.
    NOTHING_HELD = "nothing_held"
    AMOUNT_NOT_POSITIVE = "amount_not_positive"
    REFERENCE_REQUIRED = "reference_required"
    # The money arrives on or after the coupon date, and never in the future.
    VALUE_DATE_BEFORE_COUPON = "value_date_before_coupon"
    VALUE_DATE_IN_FUTURE = "value_date_in_future"
    # What arrived differs from what the face value earns, so say why.
    NOTE_REQUIRED = "note_required"
    REASON_REQUIRED = "reason_required"
    ALREADY_DECIDED = "already_decided"
    SAME_PERSON = "same_person"


def expected_coupon(issue: BondIssue, face: Money) -> Money:
    """One coupon before tax on `face`: half the yearly rate, rounded down to the shilling."""
    return face.mul_ratio(issue.coupon_bps, 2 * BASIS_POINTS, Rounding.DOWN)


def record_date(coupon_date: date) -> date:
    """The day whose holding is paid the coupon: the day before it."""
    return date.fromordinal(coupon_date.toordinal() - 1)


@dataclass(frozen=True, slots=True)
class Received:
    """A coupon as recorded from the custodian's note."""

    coupon_date: date
    face: Money
    expected: Money
    amount: Money
    value_date: date
    bank_reference: str
    note: str | None


def check_record(issue: BondIssue, received: Received, today: date) -> Refusal | None:
    """Whether a coupon may be recorded. `received.face` is what the desk held of the issue on
    the record date, and `received.expected` what that earns."""
    if received.coupon_date not in coupon_dates(issue):
        return Refusal.NOT_A_COUPON_DATE
    if received.coupon_date > today:
        return Refusal.COUPON_DATE_IN_FUTURE
    if not received.face.is_positive:
        return Refusal.NOTHING_HELD
    if not received.amount.is_positive:
        return Refusal.AMOUNT_NOT_POSITIVE
    reference = received.bank_reference
    if not reference.strip() or len(reference) > MAX_REFERENCE_LENGTH:
        return Refusal.REFERENCE_REQUIRED
    if received.value_date < received.coupon_date:
        return Refusal.VALUE_DATE_BEFORE_COUPON
    if received.value_date > today:
        return Refusal.VALUE_DATE_IN_FUTURE
    note = (received.note or "").strip()
    if received.amount != received.expected and not note:
        return Refusal.NOTE_REQUIRED
    if len(note) > MAX_NOTE_LENGTH:
        return Refusal.NOTE_REQUIRED
    return None


def check_decision(status: CouponStatus, recorded_by: str, by: str) -> Refusal | None:
    if status is not CouponStatus.RECORDED:
        return Refusal.ALREADY_DECIDED
    return Refusal.SAME_PERSON if by == recorded_by else None


def accounts(currency: Currency) -> dict[str, AccountSpec]:
    return {
        BANK_TRUST: AccountSpec(BANK_TRUST, AccountKind.ASSET, currency, "Trust bank account"),
        COUPON_INCOME: AccountSpec(
            COUPON_INCOME, AccountKind.INCOME, currency, "Coupons received on bonds held"
        ),
    }


def posting(
    coupon_id: str, amount: Money, value_date: date, *, bank_account_id: str, income_account_id: str
) -> EntryDraft:
    return EntryDraft(
        entry_type=ENTRY_TYPE,
        effective_date=value_date,
        lines=(debit(bank_account_id, amount), credit(income_account_id, amount)),
        idempotency_key=f"dealing-bond-coupon:{coupon_id}",
        source_type=SOURCE_TYPE,
        source_id=coupon_id,
    )
