"""The pure rules of a client's bond holding: when it may be paid, started and cancelled."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.bonds.domain.holdings import (
    CANCEL_GRACE,
    PAYMENT_WINDOW,
    HoldingStatus,
    StartRefusal,
    can_be_cancelled,
    can_be_paid,
    check_start,
    coupon_passed,
    pay_by,
)
from app.modules.bonds.domain.issues import BondIssue, BondStatus
from app.shared.money import Currency, Money

# Coupons on 14 May and 14 November.
ISSUE = BondIssue(
    code="BOND-10Y-2037",
    isin="UG12K1405378",
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
CREATED = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
COST = Money(985_883, Currency.UGX)


def test_a_quote_holds_for_a_day() -> None:
    assert timedelta(hours=24) == PAYMENT_WINDOW
    assert pay_by(CREATED) == CREATED + timedelta(hours=24)


@pytest.mark.parametrize(
    ("on", "passed"),
    [
        (date(2026, 11, 16), False),
        (date(2027, 5, 13), False),
        # The next coupon is on 14 May 2027: it pays the interest the client paid for.
        (date(2027, 5, 14), True),
        (date(2027, 6, 1), True),
    ],
)
def test_a_coupon_passes_on_its_own_date(on: date, passed: bool) -> None:
    assert coupon_passed(ISSUE, date(2026, 11, 16), on) is passed


def test_no_coupon_left_counts_as_passed() -> None:
    assert coupon_passed(ISSUE, date(2037, 5, 14), date(2037, 5, 14))


@pytest.mark.parametrize("status", list(HoldingStatus))
def test_only_an_unpaid_holding_may_be_paid(status: HoldingStatus) -> None:
    allowed = can_be_paid(
        status,
        pay_by(CREATED),
        CREATED,
        issue=ISSUE,
        quoted_on=date(2026, 11, 16),
        today=date(2026, 11, 16),
    )
    assert allowed is (status is HoldingStatus.UNPAID)


def test_payment_closes_with_the_window() -> None:
    closes = pay_by(CREATED)

    def paid_at(now: datetime) -> bool:
        return can_be_paid(
            HoldingStatus.UNPAID,
            closes,
            now,
            issue=ISSUE,
            quoted_on=date(2026, 11, 16),
            today=date(2026, 11, 17),
        )

    assert paid_at(closes - timedelta(seconds=1))
    assert not paid_at(closes)


def test_payment_closes_when_a_coupon_falls_due() -> None:
    # Quoted the day before a coupon, with the window still open on the coupon date.
    created = datetime(2027, 5, 13, 20, 0, tzinfo=UTC)
    assert not can_be_paid(
        HoldingStatus.UNPAID,
        pay_by(created),
        created + timedelta(hours=1),
        issue=ISSUE,
        quoted_on=date(2027, 5, 13),
        today=date(2027, 5, 14),
    )


def test_a_holding_is_cancelled_only_after_its_window_and_grace() -> None:
    closes = pay_by(CREATED)
    zero = Money.zero(Currency.UGX)
    assert not can_be_cancelled(HoldingStatus.UNPAID, closes, closes, zero)
    assert not can_be_cancelled(
        HoldingStatus.UNPAID, closes, closes + CANCEL_GRACE - timedelta(seconds=1), zero
    )
    assert can_be_cancelled(HoldingStatus.UNPAID, closes, closes + CANCEL_GRACE, zero)


def test_money_waiting_is_never_stranded_behind_a_cancelled_holding() -> None:
    late = pay_by(CREATED) + CANCEL_GRACE
    assert not can_be_cancelled(HoldingStatus.UNPAID, pay_by(CREATED), late, COST)


@pytest.mark.parametrize("status", [HoldingStatus.ACTIVE, HoldingStatus.CANCELLED])
def test_a_holding_that_moved_on_is_not_cancelled(status: HoldingStatus) -> None:
    late = pay_by(CREATED) + CANCEL_GRACE
    assert not can_be_cancelled(status, pay_by(CREATED), late, Money.zero(Currency.UGX))


def start(**overrides: object) -> StartRefusal | None:
    values: dict[str, object] = {
        "issue": ISSUE,
        "cost": COST,
        "waiting": COST,
        "quoted_on": date(2026, 11, 16),
        "confirmed_on": date(2026, 11, 16),
        "today": date(2026, 11, 17),
    }
    values.update(overrides)
    status = values.pop("status", HoldingStatus.UNPAID)
    return check_start(status, **values)  # type: ignore[arg-type]


def test_the_whole_cost_starts_a_holding() -> None:
    assert start() is None


@pytest.mark.parametrize("waiting", [985_882, 985_884, 0])
def test_anything_but_the_whole_cost_is_refused(waiting: int) -> None:
    assert start(waiting=Money(waiting, Currency.UGX)) is StartRefusal.PAYMENT_DOES_NOT_MATCH


@pytest.mark.parametrize("status", [HoldingStatus.ACTIVE, HoldingStatus.CANCELLED])
def test_only_an_unpaid_holding_starts(status: HoldingStatus) -> None:
    assert start(status=status) is StartRefusal.NOT_UNPAID


@pytest.mark.parametrize(
    "confirmed_on", [date(2026, 11, 15), date(2026, 11, 18)], ids=["before quote", "future"]
)
def test_an_impossible_confirmation_date_is_refused(confirmed_on: date) -> None:
    assert start(confirmed_on=confirmed_on) is StartRefusal.CONFIRMATION_DATE_IMPOSSIBLE


def test_a_payment_confirmed_on_the_coupon_date_is_refused() -> None:
    refused = start(
        quoted_on=date(2027, 5, 13), confirmed_on=date(2027, 5, 14), today=date(2027, 5, 14)
    )
    assert refused is StartRefusal.COUPON_PASSED
