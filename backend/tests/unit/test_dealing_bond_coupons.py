"""Coupons paid in on the desk's bonds: what a face value earns, what may be recorded, who may
decide it, and the entry it posts."""

from dataclasses import replace
from datetime import date

import pytest

from app.modules.bonds.domain.issues import BondIssue, BondStatus
from app.modules.dealing.domain.bond_coupons import (
    BANK_TRUST,
    COUPON_INCOME,
    SOURCE_TYPE,
    CouponStatus,
    Received,
    Refusal,
    accounts,
    check_decision,
    check_record,
    expected_coupon,
    posting,
    record_date,
)
from app.modules.ledger.domain.entries import AccountKind
from app.shared.money import Currency, Money

ISSUE = BondIssue(
    code="BOND-2037",
    isin="UG0000001234",
    name="10 Year Treasury Bond 2037",
    currency=Currency.UGX,
    coupon_bps=1600,
    tax_bps=1000,
    issue_date=date(2025, 5, 14),
    maturity_date=date(2037, 5, 14),
    minimum_face=Money(100_000, Currency.UGX),
    face_step=Money(100_000, Currency.UGX),
    status=BondStatus.LIVE,
)
COUPON = date(2026, 11, 14)
TODAY = date(2026, 11, 17)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


GOOD = Received(
    coupon_date=COUPON,
    face=ugx(2_000_000),
    expected=ugx(160_000),
    amount=ugx(160_000),
    value_date=COUPON,
    bank_reference="FT2631800123",
    note=None,
)


def test_a_coupon_is_half_the_yearly_rate_rounded_down() -> None:
    assert expected_coupon(ISSUE, ugx(2_000_000)) == ugx(160_000)
    assert expected_coupon(replace(ISSUE, coupon_bps=1575), ugx(100_001)) == ugx(7_875)
    assert record_date(COUPON) == date(2026, 11, 13)
    assert record_date(date(2027, 3, 1)) == date(2027, 2, 28)


def test_a_coupon_that_matches_the_face_held_is_recorded() -> None:
    assert check_record(ISSUE, GOOD, TODAY) is None
    # Paid late, on a later day, is still the coupon.
    assert check_record(ISSUE, replace(GOOD, value_date=TODAY), TODAY) is None
    # The last coupon is paid on the maturity date.
    last = replace(GOOD, coupon_date=ISSUE.maturity_date, value_date=ISSUE.maturity_date)
    assert check_record(ISSUE, last, ISSUE.maturity_date) is None


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"coupon_date": date(2026, 11, 13)}, Refusal.NOT_A_COUPON_DATE),
        ({"coupon_date": date(2025, 5, 14)}, Refusal.NOT_A_COUPON_DATE),
        ({"coupon_date": date(2027, 5, 14), "value_date": date(2027, 5, 14)},
         Refusal.COUPON_DATE_IN_FUTURE),
        ({"face": ugx(0), "expected": ugx(0)}, Refusal.NOTHING_HELD),
        ({"amount": ugx(0)}, Refusal.AMOUNT_NOT_POSITIVE),
        ({"bank_reference": "  "}, Refusal.REFERENCE_REQUIRED),
        ({"bank_reference": "F" * 101}, Refusal.REFERENCE_REQUIRED),
        ({"value_date": date(2026, 11, 13)}, Refusal.VALUE_DATE_BEFORE_COUPON),
        ({"value_date": date(2026, 11, 18)}, Refusal.VALUE_DATE_IN_FUTURE),
        ({"amount": ugx(144_000)}, Refusal.NOTE_REQUIRED),
        ({"amount": ugx(144_000), "note": " "}, Refusal.NOTE_REQUIRED),
        ({"amount": ugx(144_000), "note": "x" * 501}, Refusal.NOTE_REQUIRED),
    ],
)  # fmt: skip
def test_a_coupon_that_cannot_be_right_is_refused(
    change: dict[str, object], refusal: Refusal
) -> None:
    assert check_record(ISSUE, replace(GOOD, **change), TODAY) is refusal  # type: ignore[arg-type]


def test_a_difference_with_a_note_is_recorded() -> None:
    withheld = replace(GOOD, amount=ugx(144_000), note="10% withheld by Bank of Uganda")
    assert check_record(ISSUE, withheld, TODAY) is None


def test_only_a_second_person_decides_a_recorded_coupon_once() -> None:
    assert check_decision(CouponStatus.RECORDED, "daniel", "grace") is None
    assert check_decision(CouponStatus.RECORDED, "daniel", "daniel") is Refusal.SAME_PERSON
    for status in (CouponStatus.APPROVED, CouponStatus.DECLINED):
        assert check_decision(status, "daniel", "grace") is Refusal.ALREADY_DECIDED


def test_the_entry_puts_what_arrived_in_the_trust_account_as_income() -> None:
    entry = posting(
        "-OdCoupon", ugx(144_000), COUPON, bank_account_id="bank", income_account_id="income"
    )
    assert {line.account_id: line.amount.minor for line in entry.lines} == {
        "bank": 144_000,
        "income": -144_000,
    }
    assert (entry.effective_date, entry.source_type, entry.source_id) == (
        COUPON,
        SOURCE_TYPE,
        "-OdCoupon",
    )
    assert entry.idempotency_key == "dealing-bond-coupon:-OdCoupon"
    assert {code: spec.kind for code, spec in accounts(Currency.UGX).items()} == {
        BANK_TRUST: AccountKind.ASSET,
        COUPON_INCOME: AccountKind.INCOME,
    }
