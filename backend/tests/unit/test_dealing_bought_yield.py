from datetime import date

import pytest

from app.modules.dealing.domain.bought_yield import InvalidPurchase, yearly_yield_bps


def test_a_91_day_bill_bought_at_a_discount() -> None:
    # UGX 1,000,000 face bought for 970,000 and held 91 days:
    # 30,000 / 970,000 x 365 / 91 = 12.40...%, so 1240 basis points.
    assert yearly_yield_bps(1_000_000, 970_000, date(2026, 11, 12), date(2027, 2, 11)) == 1240


def test_rounds_down_and_bought_at_face_yields_nothing() -> None:
    assert yearly_yield_bps(1_000_000, 1_000_000, date(2026, 1, 1), date(2026, 4, 2)) == 0
    # 1 / 999,999 x 365 / 364 is far below a basis point.
    assert yearly_yield_bps(1_000_000, 999_999, date(2026, 1, 1), date(2026, 12, 31)) == 0


def test_stays_exact_for_large_amounts() -> None:
    face = 9_999_999_999_999
    paid = 9_000_000_000_000
    assert yearly_yield_bps(face, paid, date(2026, 1, 1), date(2027, 1, 1)) == 1111


@pytest.mark.parametrize(
    ("face", "paid", "settled", "matures"),
    [
        (1_000, 900, date(2026, 1, 2), date(2026, 1, 2)),
        (1_000, 900, date(2026, 1, 2), date(2026, 1, 1)),
        (1_000, 0, date(2026, 1, 1), date(2026, 4, 1)),
        (900, 1_000, date(2026, 1, 1), date(2026, 4, 1)),
    ],
)
def test_figures_that_give_no_yield_are_refused(
    face: int, paid: int, settled: date, matures: date
) -> None:
    with pytest.raises(InvalidPurchase):
        yearly_yield_bps(face, paid, settled, matures)
