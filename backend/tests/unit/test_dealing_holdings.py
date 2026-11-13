from datetime import date

import pytest

from app.modules.dealing.domain.holdings import (
    Draw,
    InvalidLot,
    Lot,
    draw,
    face_for_cost,
    income_at_maturity,
    sell,
)
from app.shared.money import Currency, Money

MATURES = date(2027, 2, 17)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


LOT = Lot(face=ugx(240_000_000), cost=ugx(232_800_000), matures_on=MATURES)


def test_selling_the_whole_lot_releases_every_shilling_of_cost() -> None:
    sale = sell(LOT, LOT.face, ugx(235_000_000))
    assert sale.cost_released == LOT.cost
    assert sale.result == ugx(2_200_000)
    assert sale.remaining is None


def test_selling_part_releases_cost_in_proportion() -> None:
    sale = sell(LOT, ugx(60_000_000), ugx(58_500_000))
    assert sale.cost_released == ugx(58_200_000)
    assert sale.result == ugx(300_000)
    assert sale.remaining == Lot(ugx(180_000_000), ugx(174_600_000), MATURES)


def test_a_sale_below_cost_shows_a_loss() -> None:
    assert sell(LOT, ugx(60_000_000), ugx(58_000_000)).result == ugx(-200_000)


def test_cost_released_is_rounded_down_and_the_parts_always_add_up() -> None:
    lot: Lot | None = Lot(face=ugx(700), cost=ugx(100), matures_on=MATURES)
    released = ugx(0)
    for _ in range(7):
        assert lot is not None
        sale = sell(lot, ugx(100), ugx(100))
        released += sale.cost_released
        lot = sale.remaining
    assert lot is None
    assert released == ugx(100)
    first = sell(Lot(ugx(700), ugx(100), MATURES), ugx(100), ugx(100))
    assert first.cost_released == ugx(14)  # 14.28 rounded down
    assert first.remaining == Lot(ugx(600), ugx(86), MATURES)


@pytest.mark.parametrize("face_sold", [0, -1, 240_000_001])
def test_a_sale_is_above_zero_and_within_the_face_held(face_sold: int) -> None:
    with pytest.raises(InvalidLot):
        sell(LOT, ugx(face_sold), ugx(1))


def test_a_sale_needs_positive_proceeds_in_the_same_currency() -> None:
    with pytest.raises(InvalidLot):
        sell(LOT, ugx(100), ugx(0))
    with pytest.raises(InvalidLot):
        sell(LOT, ugx(100), Money(100, Currency.USD))


def test_a_lot_is_paid_its_face_value_on_or_after_the_maturity_date() -> None:
    assert income_at_maturity(LOT, MATURES) == ugx(7_200_000)
    assert income_at_maturity(LOT, date(2027, 3, 1)) == ugx(7_200_000)
    with pytest.raises(InvalidLot):
        income_at_maturity(LOT, date(2027, 2, 16))


def test_a_lot_needs_positive_figures_in_one_currency() -> None:
    with pytest.raises(InvalidLot):
        Lot(ugx(0), ugx(1), MATURES)
    with pytest.raises(InvalidLot):
        Lot(ugx(1), ugx(0), MATURES)
    with pytest.raises(InvalidLot):
        Lot(ugx(100), Money(90, Currency.USD), MATURES)


@pytest.mark.parametrize(
    ("cost", "price", "lot_size", "face"),
    [
        (232_000_000, 970_000, 100_000, 239_200_000),  # 239,175,257.7 rounds up to a lot
        (232_000_000, 970_000, 10_000_000, 240_000_000),
        (97_000_000, 970_000, 100_000, 100_000_000),  # exact, no extra lot
        (97_000_001, 970_000, 100_000, 100_100_000),
        (1, 970_000, 100_000, 100_000),
        (0, 970_000, 100_000, 0),
        (100_000, 1_000_000, 100_000, 100_000),
    ],
)
def test_the_face_to_bid_for_always_costs_at_least_the_money_to_place(
    cost: int, price: int, lot_size: int, face: int
) -> None:
    result = face_for_cost(ugx(cost), price, ugx(lot_size))
    assert result == ugx(face)
    assert result.minor % lot_size == 0
    assert result.minor * price >= cost * 1_000_000


@pytest.mark.parametrize("price", [0, -1, 97.0, True])
def test_the_price_is_a_whole_positive_number(price: object) -> None:
    with pytest.raises(InvalidLot):
        face_for_cost(ugx(100), price, ugx(100))  # type: ignore[arg-type]


def test_the_face_to_bid_for_refuses_a_bad_lot_size_or_cost() -> None:
    with pytest.raises(InvalidLot):
        face_for_cost(ugx(100), 970_000, ugx(0))
    with pytest.raises(InvalidLot):
        face_for_cost(ugx(100), 970_000, Money(100, Currency.USD))
    with pytest.raises(InvalidLot):
        face_for_cost(ugx(-1), 970_000, ugx(100))


# Drawing a sale from several lots ---------------------------------------------------------------

OLDER = Lot(face=ugx(100_000_000), cost=ugx(96_000_000), matures_on=MATURES)
NEWER = Lot(face=ugx(100_000_000), cost=ugx(98_000_000), matures_on=MATURES)


def test_a_sale_empties_the_first_lot_before_touching_the_next() -> None:
    assert draw([OLDER, NEWER], ugx(150_000_000)) == (
        Draw(0, ugx(100_000_000), ugx(96_000_000)),
        Draw(1, ugx(50_000_000), ugx(49_000_000)),
    )


def test_a_sale_inside_one_lot_leaves_the_others_alone() -> None:
    assert draw([OLDER, NEWER], ugx(1)) == (Draw(0, ugx(1), ugx(0)),)


def test_drawing_everything_releases_every_shilling_of_cost() -> None:
    parts = draw([OLDER, NEWER], ugx(200_000_000))
    assert sum((part.cost_released for part in parts), ugx(0)) == ugx(194_000_000)


@pytest.mark.parametrize("face", [0, -1, 200_000_001])
def test_a_draw_is_positive_and_within_what_the_lots_hold(face: int) -> None:
    with pytest.raises(InvalidLot):
        draw([OLDER, NEWER], ugx(face))


def test_a_draw_is_in_the_lots_currency() -> None:
    with pytest.raises(InvalidLot):
        draw([OLDER], Money(1, Currency.USD))


@pytest.mark.parametrize("face", range(1, 200_000_001, 9_999_991))
def test_cost_released_never_exceeds_the_share_sold(face: int) -> None:
    parts = draw([OLDER, NEWER], ugx(face))
    assert sum((part.face for part in parts), ugx(0)) == ugx(face)
    for part in parts:
        lot = (OLDER, NEWER)[part.lot]
        assert part.cost_released.minor * lot.face.minor <= lot.cost.minor * part.face.minor
