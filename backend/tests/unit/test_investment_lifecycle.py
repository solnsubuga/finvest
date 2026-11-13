from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.investments.domain.investments import (
    CANCEL_GRACE,
    PAYMENT_WINDOW,
    InvestmentStatus,
    Start,
    StartRefusal,
    can_be_cancelled,
    can_be_paid,
    check_start,
    pay_by,
    start,
)
from app.shared.money import Currency, Money

CREATED = datetime(2026, 11, 13, 9, tzinfo=UTC)
DEADLINE = pay_by(CREATED)
AMOUNT = Money(1_000_000, Currency.UGX)
NOTHING = Money(0, Currency.UGX)
AWAITING, ACTIVE, CANCELLED = (
    InvestmentStatus.AWAITING_PAYMENT,
    InvestmentStatus.ACTIVE,
    InvestmentStatus.CANCELLED,
)


def test_a_review_stays_payable_for_three_days() -> None:
    assert timedelta(hours=72) == PAYMENT_WINDOW
    assert datetime(2026, 11, 16, 9, tzinfo=UTC) == DEADLINE
    assert can_be_paid(AWAITING, DEADLINE, DEADLINE - timedelta(seconds=1))
    assert not can_be_paid(AWAITING, DEADLINE, DEADLINE)


@pytest.mark.parametrize("status", [ACTIVE, CANCELLED])
def test_only_an_unpaid_investment_takes_a_payment(status: InvestmentStatus) -> None:
    assert not can_be_paid(status, DEADLINE, CREATED)


def test_an_unpaid_investment_is_cancelled_after_the_window_and_its_grace() -> None:
    late = DEADLINE + CANCEL_GRACE
    assert can_be_cancelled(AWAITING, DEADLINE, late, NOTHING)
    assert not can_be_cancelled(AWAITING, DEADLINE, late - timedelta(seconds=1), NOTHING)
    assert not can_be_cancelled(ACTIVE, DEADLINE, late, NOTHING)
    assert not can_be_cancelled(CANCELLED, DEADLINE, late, NOTHING)


def test_money_that_arrived_is_never_stranded_behind_a_cancellation() -> None:
    late = DEADLINE + timedelta(days=30)
    assert not can_be_cancelled(AWAITING, DEADLINE, late, Money(1, Currency.UGX))
    assert not can_be_cancelled(AWAITING, DEADLINE, late, AMOUNT)


def check(
    status: InvestmentStatus = AWAITING,
    waiting: Money = AMOUNT,
    confirmed_on: date = date(2026, 11, 13),
    today: date = date(2026, 11, 13),
) -> StartRefusal | None:
    return check_start(
        status,
        amount=AMOUNT,
        waiting=waiting,
        created_on=date(2026, 11, 13),
        confirmed_on=confirmed_on,
        today=today,
    )


def test_the_exact_payment_starts_the_investment() -> None:
    assert check() is None


@pytest.mark.parametrize("waiting", [0, 1, 999_999, 1_000_001, 2_000_000])
def test_a_part_payment_or_an_overpayment_does_not(waiting: int) -> None:
    assert check(waiting=Money(waiting, Currency.UGX)) is StartRefusal.PAYMENT_DOES_NOT_MATCH


@pytest.mark.parametrize("status", [ACTIVE, CANCELLED])
def test_an_investment_starts_once(status: InvestmentStatus) -> None:
    assert check(status=status) is StartRefusal.NOT_AWAITING_PAYMENT


def test_a_payment_confirmed_yesterday_and_processed_today_starts_yesterday() -> None:
    assert check(confirmed_on=date(2026, 11, 13), today=date(2026, 11, 14)) is None
    assert start(date(2026, 11, 13), 91) == Start(date(2026, 11, 13), date(2027, 2, 12))


@pytest.mark.parametrize(
    ("confirmed_on", "today"),
    [
        (date(2026, 11, 12), date(2026, 11, 13)),  # before the review
        (date(2026, 11, 14), date(2026, 11, 13)),  # in the future
    ],
)
def test_an_impossible_confirmation_date_is_refused(confirmed_on: date, today: date) -> None:
    assert check(confirmed_on=confirmed_on, today=today) is (
        StartRefusal.CONFIRMATION_DATE_IMPOSSIBLE
    )


def test_an_open_term_has_no_maturity_date() -> None:
    assert start(date(2026, 11, 13), None) == Start(date(2026, 11, 13), None)
