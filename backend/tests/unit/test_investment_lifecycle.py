from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.investments.domain.investments import (
    CANCEL_GRACE,
    PAYMENT_WINDOW,
    AtMaturity,
    HoldReason,
    InvestmentStatus,
    MaturityPlan,
    MaturityStep,
    Start,
    StartRefusal,
    can_be_cancelled,
    can_be_paid,
    can_choose_maturity,
    check_start,
    is_due_to_mature,
    pay_by,
    plan_maturity,
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


# Maturity ---------------------------------------------------------------------------------------

MATURES = date(2027, 2, 12)
MATURED = InvestmentStatus.MATURED


def test_the_choice_for_maturity_can_change_up_to_the_day_before() -> None:
    assert can_choose_maturity(AWAITING, 91, None, date(2026, 11, 13))
    assert can_choose_maturity(ACTIVE, 91, MATURES, MATURES - timedelta(days=1))
    assert not can_choose_maturity(ACTIVE, 91, MATURES, MATURES)
    assert not can_choose_maturity(ACTIVE, 91, MATURES, MATURES + timedelta(days=1))


@pytest.mark.parametrize("status", [CANCELLED, MATURED])
def test_a_closed_investment_has_no_choice_left(status: InvestmentStatus) -> None:
    assert not can_choose_maturity(status, 91, MATURES, date(2026, 11, 14))


def test_an_open_term_has_no_maturity_to_choose_for() -> None:
    assert not can_choose_maturity(ACTIVE, None, None, date(2026, 11, 14))
    assert not is_due_to_mature(ACTIVE, None, date(2030, 1, 1))


def test_an_investment_is_due_on_its_maturity_date_and_after() -> None:
    assert not is_due_to_mature(ACTIVE, MATURES, MATURES - timedelta(days=1))
    assert is_due_to_mature(ACTIVE, MATURES, MATURES)
    assert is_due_to_mature(ACTIVE, MATURES, MATURES + timedelta(days=9))


@pytest.mark.parametrize("status", [AWAITING, CANCELLED, MATURED])
def test_only_a_running_investment_matures(status: InvestmentStatus) -> None:
    assert not is_due_to_mature(status, MATURES, MATURES)


@pytest.mark.parametrize(
    ("choice", "has_destination", "product_open", "plan"),
    [
        (AtMaturity.ROLL_OVER, False, True, MaturityPlan(MaturityStep.ROLL_OVER)),
        (AtMaturity.PAY_OUT, True, True, MaturityPlan(MaturityStep.PAY_OUT)),
        (AtMaturity.PAY_OUT, True, False, MaturityPlan(MaturityStep.PAY_OUT)),
        # Nowhere to send it: held, never rolled over instead.
        (
            AtMaturity.PAY_OUT,
            False,
            True,
            MaturityPlan(MaturityStep.HOLD, HoldReason.NO_DESTINATION),
        ),
        # The product closed: held, never sent to a destination the client did not choose.
        (
            AtMaturity.ROLL_OVER,
            True,
            False,
            MaturityPlan(MaturityStep.HOLD, HoldReason.PRODUCT_NOT_OPEN),
        ),
        (
            AtMaturity.ROLL_OVER,
            False,
            False,
            MaturityPlan(MaturityStep.HOLD, HoldReason.PRODUCT_NOT_OPEN),
        ),
    ],
)
def test_money_at_maturity_follows_the_clients_choice_or_waits(
    choice: AtMaturity, has_destination: bool, product_open: bool, plan: MaturityPlan
) -> None:
    assert plan_maturity(choice, has_destination=has_destination, product_open=product_open) == plan
