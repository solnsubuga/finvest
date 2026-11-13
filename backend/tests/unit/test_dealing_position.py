from datetime import UTC, datetime, timedelta

import pytest

from app.modules.dealing.domain.allocation import (
    Allocation,
    InvalidAllocation,
    Waiting,
    allocate,
)
from app.modules.dealing.domain.position import (
    Cover,
    InvalidPosition,
    ProductPosition,
    money_to_place,
)
from app.shared.money import Currency, Money

NOON = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def usd(cents: int) -> Money:
    return Money(cents, Currency.USD)


def position(owed: int, held: int, on_order: int = 0, code: str = "TREA-91") -> ProductPosition:
    return ProductPosition(code, ugx(owed), ugx(held), ugx(on_order))


@pytest.mark.parametrize(
    ("owed", "held", "on_order", "to_buy", "uncovered", "surplus", "cover"),
    [
        (0, 0, 0, 0, 0, 0, Cover.COVERED),
        (7_412, 7_412, 0, 0, 0, 0, Cover.COVERED),
        (7_412, 7_180, 0, 232, 232, 0, Cover.BID_NEEDED),
        (7_412, 7_180, 231, 232, 1, 0, Cover.BID_NEEDED),
        (7_412, 7_180, 232, 232, 0, 0, Cover.ON_ORDER),
        (7_412, 7_180, 240, 232, 0, 0, Cover.ON_ORDER),
        (7_180, 7_412, 0, 0, 0, 232, Cover.SURPLUS),
        (7_411, 7_412, 0, 0, 0, 1, Cover.SURPLUS),
        (1, 0, 0, 1, 1, 0, Cover.BID_NEEDED),
    ],
)
def test_the_position_compares_what_clients_are_owed_with_the_bills_held(
    owed: int, held: int, on_order: int, to_buy: int, uncovered: int, surplus: int, cover: Cover
) -> None:
    result = position(owed, held, on_order)
    assert result.to_buy == ugx(to_buy)
    assert result.uncovered == ugx(uncovered)
    assert result.surplus == ugx(surplus)
    assert result.cover is cover


@pytest.mark.parametrize(("owed", "held", "on_order"), [(-1, 0, 0), (0, -1, 0), (0, 0, -1)])
def test_a_position_figure_is_never_negative(owed: int, held: int, on_order: int) -> None:
    with pytest.raises(InvalidPosition):
        position(owed, held, on_order)


def test_a_position_is_in_one_currency_and_names_its_product() -> None:
    with pytest.raises(InvalidPosition):
        ProductPosition("USD-BOND", usd(100), ugx(100), usd(0))
    with pytest.raises(InvalidPosition):
        position(1, 1, code="")


def test_money_to_place_adds_the_gaps_and_ignores_a_surplus_elsewhere() -> None:
    positions = [
        position(7_412, 7_180, code="TREA-91"),
        position(9_870, 9_716, code="TREA-182"),
        position(500, 900, code="TREA-364"),
        ProductPosition("USD-BOND", usd(362_000_00), usd(300_000_00), usd(0)),
    ]
    assert money_to_place(positions, Currency.UGX) == ugx(232 + 154)
    assert money_to_place(positions, Currency.USD) == usd(62_000_00)
    assert money_to_place([], Currency.UGX) == ugx(0)


def test_money_to_place_is_not_reduced_by_orders_that_have_not_settled() -> None:
    assert money_to_place([position(1_000, 400, on_order=600)], Currency.UGX) == ugx(600)


def test_a_product_listed_twice_is_refused() -> None:
    with pytest.raises(InvalidPosition):
        money_to_place([position(1, 0), position(2, 0)], Currency.UGX)


def waiting(investment_id: str, minutes: int, amount: int) -> Waiting:
    return Waiting(investment_id, NOON + timedelta(minutes=minutes), ugx(amount))


QUEUE = [waiting("c", 30, 300), waiting("a", 0, 500), waiting("b", 10, 200)]


@pytest.mark.parametrize(
    ("available", "expected", "left_over"),
    [
        (0, [], 0),
        (1, [("a", 1)], 0),
        (499, [("a", 499)], 0),
        (500, [("a", 500)], 0),
        (501, [("a", 500), ("b", 1)], 0),
        (700, [("a", 500), ("b", 200)], 0),
        (999, [("a", 500), ("b", 200), ("c", 299)], 0),
        (1_000, [("a", 500), ("b", 200), ("c", 300)], 0),
        (1_240, [("a", 500), ("b", 200), ("c", 300)], 240),
    ],
)
def test_the_oldest_payment_is_served_first_and_in_full(
    available: int, expected: list[tuple[str, int]], left_over: int
) -> None:
    result = allocate(ugx(available), QUEUE)
    assert list(result.allocations) == [Allocation(name, ugx(amount)) for name, amount in expected]
    assert result.left_over == ugx(left_over)
    allocated = sum(allocation.amount.minor for allocation in result.allocations)
    assert allocated + result.left_over.minor == available


def test_nobody_is_allocated_more_than_they_are_waiting_for() -> None:
    for available in range(0, 1_100, 7):
        by_id = {item.investment_id: item.amount for item in QUEUE}
        for allocation in allocate(ugx(available), QUEUE).allocations:
            assert ugx(0) < allocation.amount <= by_id[allocation.investment_id]


def test_payments_at_the_same_instant_are_ordered_by_investment_id() -> None:
    same = [waiting("z", 0, 100), waiting("m", 0, 100), waiting("a", 0, 100)]
    first = allocate(ugx(150), same)
    again = allocate(ugx(150), list(reversed(same)))
    assert first == again
    assert [allocation.investment_id for allocation in first.allocations] == ["a", "m"]


def test_with_nothing_waiting_everything_is_left_with_the_desk() -> None:
    result = allocate(ugx(240), [])
    assert result.allocations == ()
    assert result.left_over == ugx(240)


def test_allocation_refuses_inputs_it_cannot_trust() -> None:
    with pytest.raises(InvalidAllocation):
        allocate(ugx(-1), QUEUE)
    with pytest.raises(InvalidAllocation):
        allocate(ugx(10), [waiting("a", 0, 5), waiting("a", 1, 5)])
    with pytest.raises(InvalidAllocation):
        waiting("a", 0, 0)
    with pytest.raises(InvalidAllocation):
        waiting("", 0, 5)
    with pytest.raises(InvalidAllocation):
        Waiting("a", datetime(2026, 11, 13, 9, 0), ugx(5))
    with pytest.raises(ValueError, match="Cannot combine"):
        allocate(ugx(10), [Waiting("a", NOON, usd(5))])
