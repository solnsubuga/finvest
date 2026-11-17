"""The desk's position in a bond issue: face value clients hold against face value held."""

import pytest

from app.modules.dealing.domain.bond_position import BondPosition, bonds_to_place
from app.modules.dealing.domain.position import Cover, InvalidPosition
from app.shared.money import Currency, Money


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def position(
    clients: int = 0,
    paid: int = 0,
    held: int = 0,
    cost: int = 0,
    on_order: int = 0,
    on_sale: int = 0,
    code: str = "BOND-2037",
) -> BondPosition:
    return BondPosition(
        code=code,
        clients_face=ugx(clients),
        clients_paid=ugx(paid),
        face_held=ugx(held),
        cost_held=ugx(cost),
        on_order=ugx(on_order),
        on_sale=ugx(on_sale),
    )


def test_a_holding_with_no_bond_behind_it_needs_one_and_its_money_waits() -> None:
    found = position(clients=3_000_000, paid=3_030_000)
    assert (found.to_buy, found.uncovered, found.surplus) == (
        ugx(3_000_000),
        ugx(3_000_000),
        ugx(0),
    )
    assert found.cover is Cover.BID_NEEDED
    assert found.to_place == ugx(3_030_000)


def test_an_order_prepared_for_it_is_on_order_and_the_money_still_waits() -> None:
    found = position(clients=3_000_000, paid=3_030_000, on_order=3_000_000)
    assert found.cover is Cover.ON_ORDER
    assert found.uncovered == ugx(0)
    assert found.to_place == ugx(3_030_000)


def test_bonds_cover_by_face_value_whatever_they_cost() -> None:
    found = position(clients=3_000_000, paid=3_030_000, held=3_000_000, cost=2_900_000)
    assert found.cover is Cover.COVERED
    assert (found.to_buy, found.to_place) == (ugx(0), ugx(0))


def test_money_for_part_of_the_face_value_is_its_share_rounded_up() -> None:
    # A third of 1,000,001 is 333,333.67: the buffer never counts the part shilling as its own.
    found = position(clients=3_000_000, paid=1_000_001, held=2_000_000, cost=666_000)
    assert found.to_buy == ugx(1_000_000)
    assert found.to_place == ugx(333_334)


def test_bonds_held_beyond_what_clients_hold_are_to_sell_less_what_is_on_sale() -> None:
    found = position(clients=1_000_000, paid=990_000, held=3_000_000, cost=2_970_000)
    assert found.cover is Cover.SURPLUS
    assert found.to_sell == ugx(2_000_000)
    assert position(held=3_000_000, cost=2_970_000, on_sale=500_000).to_sell == ugx(2_500_000)
    assert found.to_place == ugx(0)


def test_nothing_held_and_nothing_owed_is_covered() -> None:
    found = position()
    assert found.cover is Cover.COVERED
    assert found.to_place == ugx(0)


@pytest.mark.parametrize(
    "figures",
    [
        {"clients": -1, "paid": 0},
        {"clients": 1, "paid": 0},
        {"clients": 0, "paid": 1},
        {"held": 1, "on_sale": 2},
        {"code": ""},
    ],
)
def test_figures_that_cannot_describe_a_position_are_refused(figures: dict[str, object]) -> None:
    with pytest.raises(InvalidPosition):
        position(**figures)  # type: ignore[arg-type]


def test_a_position_is_in_one_currency() -> None:
    with pytest.raises(InvalidPosition):
        BondPosition(
            code="BOND-2037",
            clients_face=ugx(0),
            clients_paid=ugx(0),
            face_held=Money(1, Currency.USD),
            cost_held=ugx(0),
            on_order=ugx(0),
            on_sale=ugx(0),
        )


def test_money_to_place_adds_up_across_issues_and_a_surplus_never_offsets_a_gap() -> None:
    gap = position(clients=1_000_000, paid=1_010_000, code="BOND-2030")
    surplus = position(held=5_000_000, cost=5_000_000, code="BOND-2037")
    assert bonds_to_place([gap, surplus], Currency.UGX) == ugx(1_010_000)
    assert bonds_to_place([gap, surplus], Currency.USD) == Money(0, Currency.USD)
    with pytest.raises(InvalidPosition):
        bonds_to_place([gap, gap], Currency.UGX)
