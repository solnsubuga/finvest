from datetime import UTC, date, datetime

import pytest

from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.limits import (
    MOBILE_MONEY_PER_PAYOUT_UGX,
    InvalidLimits,
    Refusal,
    WithdrawalLimits,
    business_date,
    check_withdrawal,
    limits_in_force,
)
from app.shared.money import Currency, Money

MOBILE, BANK = DestinationKind.MOBILE_MONEY, DestinationKind.BANK


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


NOTHING = ugx(0)
LAUNCH = WithdrawalLimits(date(2026, 1, 1), mobile_money_per_payout=ugx(5_000_000))
FULL = WithdrawalLimits(
    date(2026, 1, 1),
    mobile_money_per_payout=ugx(5_000_000),
    mobile_money_per_day=ugx(7_000_000),
    bank_per_payout=ugx(100_000_000),
    bank_per_day=ugx(150_000_000),
    review_above=ugx(20_000_000),
)


def test_the_mobile_money_limit_is_five_million_shillings() -> None:
    assert ugx(5_000_000) == MOBILE_MONEY_PER_PAYOUT_UGX


@pytest.mark.parametrize(
    ("amount", "refusal"),
    [
        (1, None),
        (4_999_999, None),
        (5_000_000, None),
        (5_000_001, Refusal.OVER_MOBILE_MONEY_LIMIT),
        (50_000_000, Refusal.OVER_MOBILE_MONEY_LIMIT),
    ],
)
def test_mobile_money_carries_up_to_the_limit_exactly(amount: int, refusal: Refusal | None) -> None:
    decision = check_withdrawal(LAUNCH, MOBILE, ugx(amount), NOTHING)
    assert decision.refusal is refusal
    assert decision.allowed is (refusal is None)


def test_the_same_amount_can_go_to_a_bank_when_no_bank_limit_is_set() -> None:
    assert check_withdrawal(LAUNCH, BANK, ugx(5_000_001), NOTHING).allowed
    assert check_withdrawal(LAUNCH, BANK, ugx(10**12), ugx(10**12)).allowed


@pytest.mark.parametrize("amount", [0, -1, -5_000_000])
@pytest.mark.parametrize("kind", DestinationKind)
def test_an_amount_must_be_positive(amount: int, kind: DestinationKind) -> None:
    assert check_withdrawal(FULL, kind, ugx(amount), NOTHING).refusal is Refusal.AMOUNT_NOT_POSITIVE


@pytest.mark.parametrize("kind", DestinationKind)
def test_another_currency_is_refused_not_converted(kind: DestinationKind) -> None:
    dollars = Money(100, Currency.USD)
    assert check_withdrawal(FULL, kind, dollars, NOTHING).refusal is Refusal.CURRENCY_NOT_SUPPORTED
    assert (
        check_withdrawal(FULL, kind, ugx(100), Money(0, Currency.USD)).refusal
        is Refusal.CURRENCY_NOT_SUPPORTED
    )


@pytest.mark.parametrize(
    ("already", "amount", "refusal"),
    [
        (0, 5_000_000, None),
        (2_000_000, 5_000_000, None),  # exactly the day's limit
        (2_000_001, 5_000_000, Refusal.OVER_DAILY_LIMIT),
        (6_999_999, 1, None),
        (7_000_000, 1, Refusal.OVER_DAILY_LIMIT),
        (9_000_000, 1, Refusal.OVER_DAILY_LIMIT),  # limit was lowered during the day
        (0, 5_000_001, Refusal.OVER_MOBILE_MONEY_LIMIT),  # the single limit is reported first
    ],
)
def test_the_daily_limit_counts_what_already_went_today(
    already: int, amount: int, refusal: Refusal | None
) -> None:
    assert check_withdrawal(FULL, MOBILE, ugx(amount), ugx(already)).refusal is refusal


@pytest.mark.parametrize(
    ("already", "amount", "refusal"),
    [
        (0, 100_000_000, None),
        (0, 100_000_001, Refusal.OVER_BANK_LIMIT),
        (50_000_000, 100_000_000, None),
        (50_000_001, 100_000_000, Refusal.OVER_DAILY_LIMIT),
    ],
)
def test_bank_limits_apply_when_set(already: int, amount: int, refusal: Refusal | None) -> None:
    assert check_withdrawal(FULL, BANK, ugx(amount), ugx(already)).refusal is refusal


def test_a_negative_running_total_is_a_bug_not_an_allowance() -> None:
    with pytest.raises(ValueError):
        check_withdrawal(FULL, MOBILE, ugx(1), ugx(-1))


@pytest.mark.parametrize(
    ("amount", "held"), [(20_000_000, False), (20_000_001, True), (100_000_000, True)]
)
def test_a_large_withdrawal_is_held_for_staff(amount: int, held: bool) -> None:
    decision = check_withdrawal(FULL, BANK, ugx(amount), NOTHING)
    assert decision.allowed
    assert decision.needs_review is held


def test_a_refused_withdrawal_is_not_also_sent_for_review() -> None:
    decision = check_withdrawal(FULL, BANK, ugx(100_000_001), NOTHING)
    assert not decision.allowed
    assert not decision.needs_review


def test_nothing_is_held_when_no_review_threshold_is_set() -> None:
    assert not check_withdrawal(LAUNCH, BANK, ugx(10**12), NOTHING).needs_review


# --- Versions ---------------------------------------------------------------------------------


def test_the_version_in_force_is_the_latest_one_already_effective() -> None:
    first = WithdrawalLimits(date(2026, 1, 1), ugx(5_000_000))
    second = WithdrawalLimits(date(2026, 6, 1), ugx(3_000_000))
    future = WithdrawalLimits(date(2027, 1, 1), ugx(1_000_000))
    versions = [future, first, second]
    assert limits_in_force(versions, date(2026, 1, 1)) is first
    assert limits_in_force(versions, date(2026, 5, 31)) is first
    assert limits_in_force(versions, date(2026, 6, 1)) is second
    assert limits_in_force(versions, date(2026, 12, 31)) is second
    assert limits_in_force(versions, date(2027, 1, 1)) is future


def test_no_limits_in_force_is_an_error_not_no_limit() -> None:
    with pytest.raises(InvalidLimits):
        limits_in_force([], date(2026, 1, 1))
    with pytest.raises(InvalidLimits):
        limits_in_force([LAUNCH], date(2025, 12, 31))


def test_two_versions_cannot_share_an_effective_date() -> None:
    with pytest.raises(InvalidLimits):
        limits_in_force([LAUNCH, FULL], date(2026, 6, 1))


@pytest.mark.parametrize(
    "build",
    [
        lambda: WithdrawalLimits(date(2026, 1, 1), ugx(5_000_001)),
        lambda: WithdrawalLimits(date(2026, 1, 1), ugx(0)),
        lambda: WithdrawalLimits(date(2026, 1, 1), ugx(-1)),
        lambda: WithdrawalLimits(date(2026, 1, 1), Money(500, Currency.USD)),
        lambda: WithdrawalLimits(
            date(2026, 1, 1), ugx(5_000_000), mobile_money_per_day=ugx(4_999_999)
        ),
        lambda: WithdrawalLimits(date(2026, 1, 1), ugx(5_000_000), bank_per_payout=ugx(0)),
        lambda: WithdrawalLimits(
            date(2026, 1, 1), ugx(5_000_000), bank_per_payout=ugx(10), bank_per_day=ugx(9)
        ),
        lambda: WithdrawalLimits(
            date(2026, 1, 1), ugx(5_000_000), review_above=Money(100, Currency.USD)
        ),
    ],
)
def test_limits_that_contradict_themselves_are_refused(build: object) -> None:
    assert callable(build)
    with pytest.raises(InvalidLimits):
        build()


def test_limits_never_take_a_float() -> None:
    with pytest.raises(ValueError):
        WithdrawalLimits(date(2026, 1, 1), Money(5_000_000.0, Currency.UGX))  # type: ignore[arg-type]


# --- The business day -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "day"),
    [
        (datetime(2026, 11, 13, 20, 59, 59, tzinfo=UTC), date(2026, 11, 13)),
        (datetime(2026, 11, 13, 21, 0, 0, tzinfo=UTC), date(2026, 11, 14)),  # midnight in Kampala
        (datetime(2026, 12, 31, 21, 0, 0, tzinfo=UTC), date(2027, 1, 1)),
        (datetime(2026, 11, 13, 0, 0, 0, tzinfo=UTC), date(2026, 11, 13)),
    ],
)
def test_the_day_turns_at_midnight_in_kampala(moment: datetime, day: date) -> None:
    assert business_date(moment) == day


def test_the_business_day_needs_a_zone() -> None:
    with pytest.raises(ValueError):
        business_date(datetime(2026, 11, 13, 9, 0))
