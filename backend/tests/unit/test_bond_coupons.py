"""The pure rules of bond coupons: which day pays which holding, and how much."""

from datetime import date

import pytest

from app.modules.bonds.domain.coupons import coupon, is_coupon_run_day, is_owed
from app.modules.bonds.domain.issues import BondIssue, BondStatus
from app.shared.money import Currency, Money


def issue(**overrides: object) -> BondIssue:
    values: dict[str, object] = {
        "code": "BOND-10Y-2037",
        "isin": "UG12K1405378",
        "name": "10 Year Treasury Bond 2037",
        "currency": Currency.UGX,
        "coupon_bps": 1600,
        "tax_bps": 1000,
        "issue_date": date(2025, 5, 14),
        "maturity_date": date(2037, 5, 14),
        "minimum_face": Money(100_000, Currency.UGX),
        "face_step": Money(100_000, Currency.UGX),
        "status": BondStatus.LIVE,
    }
    values.update(overrides)
    return BondIssue(**values)  # type: ignore[arg-type]


def test_a_coupon_is_half_the_yearly_rate_less_the_tax() -> None:
    paid = coupon(issue(), Money(1_000_000, Currency.UGX))
    assert (paid.gross, paid.tax, paid.net) == (
        Money(80_000, Currency.UGX),
        Money(8_000, Currency.UGX),
        Money(72_000, Currency.UGX),
    )


def test_a_coupon_and_its_tax_round_down_to_the_shilling() -> None:
    # 15.25% on 100,100 is 7,632.625 a half year; 20% of 7,632 is 1,526.4.
    paid = coupon(
        issue(coupon_bps=1525, tax_bps=2000, face_step=Money(100, Currency.UGX)),
        Money(100_100, Currency.UGX),
    )
    assert (paid.gross.minor, paid.tax.minor, paid.net.minor) == (7_632, 1_526, 6_106)


@pytest.mark.parametrize(
    ("on", "due"),
    [
        (date(2026, 11, 14), True),
        (date(2027, 5, 14), True),
        (date(2026, 11, 15), False),
        (date(2026, 11, 13), False),
        # The last coupon is paid with the face value at maturity, not by the run.
        (date(2037, 5, 14), False),
        (date(2037, 11, 14), False),
    ],
)
def test_the_run_pays_on_coupon_dates_before_maturity(on: date, due: bool) -> None:
    assert is_coupon_run_day(issue(), on) is due


def test_a_month_end_maturity_pays_on_the_last_day_of_shorter_months() -> None:
    month_end = issue(issue_date=date(2025, 8, 31), maturity_date=date(2030, 8, 31))
    assert is_coupon_run_day(month_end, date(2026, 2, 28))
    assert is_coupon_run_day(month_end, date(2028, 2, 29))
    assert not is_coupon_run_day(month_end, date(2026, 3, 1))


@pytest.mark.parametrize(
    ("start", "owed"),
    [
        (date(2026, 11, 13), True),
        # Bought on the coupon date: it paid no accrued interest, so the coupon is not its own.
        (date(2026, 11, 14), False),
        (date(2026, 11, 15), False),
        (None, False),
    ],
)
def test_a_holding_is_owed_coupons_dated_after_it_started(start: date | None, owed: bool) -> None:
    assert is_owed(start, date(2026, 11, 14)) is owed
