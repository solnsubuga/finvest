from dataclasses import replace
from datetime import date

import pytest

from app.modules.dealing.domain.liquidity import (
    BufferLevel,
    BufferRules,
    DeskCash,
    Funding,
    InvalidBufferRules,
    buffer_level,
    desk_cash,
    fund_early_withdrawal,
    rules_in_force,
    to_raise,
)
from app.shared.money import Currency, Money

M = 1_000_000


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


# The rules drawn on the design canvas.
RULES = BufferRules(
    effective_from=date(2026, 11, 1),
    target=ugx(150 * M),
    floor=ugx(100 * M),
    from_buffer_up_to=ugx(5 * M),
    pay_within_working_days=2,
)


@pytest.mark.parametrize(
    ("amount", "buffer", "funding"),
    [
        (1, 142 * M, Funding.FROM_BUFFER),
        (5 * M, 142 * M, Funding.FROM_BUFFER),
        (5 * M, 100 * M, Funding.FROM_BUFFER),  # on the floor still pays
        (5 * M, 100 * M - 1, Funding.QUEUED),
        (1, 0, Funding.QUEUED),
        (5 * M + 1, 142 * M, Funding.SECONDARY_SALE),
        (5 * M + 1, 0, Funding.SECONDARY_SALE),  # a sale does not depend on the buffer
        (48_300_000, 500 * M, Funding.SECONDARY_SALE),  # never split, however full the buffer
    ],
)
def test_small_withdrawals_use_the_buffer_and_large_ones_a_sale(
    amount: int, buffer: int, funding: Funding
) -> None:
    assert fund_early_withdrawal(ugx(amount), ugx(buffer), RULES) is funding


def test_a_buffer_on_its_floor_always_holds_enough_for_one_withdrawal() -> None:
    assert RULES.from_buffer_up_to <= RULES.floor
    with pytest.raises(InvalidBufferRules):
        replace(RULES, from_buffer_up_to=ugx(100 * M + 1))


@pytest.mark.parametrize(("amount", "buffer"), [(0, M), (-1, M), (1, -1)])
def test_funding_refuses_figures_that_make_no_sense(amount: int, buffer: int) -> None:
    with pytest.raises(ValueError, match=r"positive|negative"):
        fund_early_withdrawal(ugx(amount), ugx(buffer), RULES)


def test_funding_never_converts_currencies() -> None:
    with pytest.raises(ValueError, match="currency"):
        fund_early_withdrawal(Money(100, Currency.USD), ugx(M), RULES)
    with pytest.raises(ValueError, match="currency"):
        fund_early_withdrawal(ugx(100), Money(100, Currency.USD), RULES)


@pytest.mark.parametrize(
    ("buffer", "level"),
    [
        (0, BufferLevel.BELOW_FLOOR),
        (100 * M - 1, BufferLevel.BELOW_FLOOR),
        (100 * M, BufferLevel.UNDER_TARGET),
        (142 * M, BufferLevel.UNDER_TARGET),
        (150 * M - 1, BufferLevel.UNDER_TARGET),
        (150 * M, BufferLevel.ON_TARGET),
        (900 * M, BufferLevel.ON_TARGET),
    ],
)
def test_the_buffer_is_measured_against_its_floor_and_target(
    buffer: int, level: BufferLevel
) -> None:
    assert buffer_level(ugx(buffer), RULES) is level


@pytest.mark.parametrize(
    ("cash", "to_place", "promised", "buffer", "short"),
    [
        (528 * M, 386 * M, 0, 142 * M, 0),
        (528 * M, 386 * M, 23_500_000, 118_500_000, 0),
        (386 * M, 386 * M, 0, 0, 0),
        (300 * M, 386 * M, 0, 0, 86 * M),
        (390 * M, 386 * M, 5 * M, 0, 1 * M),
        (0, 0, 0, 0, 0),
    ],
)
def test_client_money_is_counted_before_the_buffer(
    cash: int, to_place: int, promised: int, buffer: int, short: int
) -> None:
    result = desk_cash(ugx(cash), ugx(to_place), ugx(promised))
    assert result == DeskCash(buffer=ugx(buffer), short=ugx(short))


@pytest.mark.parametrize(("cash", "to_place", "promised"), [(-1, 0, 0), (0, -1, 0), (0, 0, -1)])
def test_cash_figures_are_never_negative(cash: int, to_place: int, promised: int) -> None:
    with pytest.raises(ValueError, match="negative"):
        desk_cash(ugx(cash), ugx(to_place), ugx(promised))


@pytest.mark.parametrize(
    ("buffer", "waiting", "raise_"),
    [
        (150 * M, 0, 0),
        (200 * M, 0, 0),
        (142 * M, 0, 8 * M),
        (142 * M, 48_300_000, 56_300_000),
        (200 * M, 48_300_000, 48_300_000),  # a full buffer still does not pay a large one
        (0, 0, 150 * M),
    ],
)
def test_the_desk_raises_enough_to_pay_those_waiting_and_refill_the_buffer(
    buffer: int, waiting: int, raise_: int
) -> None:
    assert to_raise(ugx(buffer), RULES, ugx(waiting)) == ugx(raise_)


def test_the_latest_version_not_after_the_day_is_in_force() -> None:
    later = replace(RULES, effective_from=date(2026, 12, 1), target=ugx(200 * M))
    assert rules_in_force([later, RULES], date(2026, 11, 30)) is RULES
    assert rules_in_force([RULES, later], date(2026, 12, 1)) is later
    assert rules_in_force([RULES, later], date(2027, 6, 1)) is later


def test_no_rules_in_force_is_an_error_and_never_means_no_buffer() -> None:
    with pytest.raises(InvalidBufferRules):
        rules_in_force([RULES], date(2026, 10, 31))
    with pytest.raises(InvalidBufferRules):
        rules_in_force([], date(2026, 11, 13))


def test_two_versions_cannot_share_an_effective_date() -> None:
    with pytest.raises(InvalidBufferRules):
        rules_in_force([RULES, replace(RULES, target=ugx(160 * M))], date(2026, 11, 13))


@pytest.mark.parametrize(
    "change",
    [
        {"target": ugx(0)},
        {"floor": ugx(0)},
        {"from_buffer_up_to": ugx(0)},
        {"floor": ugx(150 * M + 1)},
        {"target": Money(150 * M, Currency.USD)},
        {"pay_within_working_days": 0},
        {"pay_within_working_days": 11},
        {"pay_within_working_days": 2.0},
        {"pay_within_working_days": True},
    ],
)
def test_rules_that_contradict_themselves_are_refused(change: dict[str, object]) -> None:
    with pytest.raises(InvalidBufferRules):
        replace(RULES, **change)  # type: ignore[arg-type]
