"""The pure rules of bond issues: coupon dates, coupons, accrued interest, prices and quotes."""

from datetime import date, timedelta

import pytest

from app.modules.bonds.domain import changes
from app.modules.bonds.domain.issues import (
    PRICE_SCALE,
    BondIssue,
    BondStatus,
    InvalidIssue,
    Refusal,
    accrued_interest,
    check_purchase,
    client_quote,
    coupon_dates,
    coupon_gross,
    coupon_label,
    default_tax_bps,
    period_around,
    price_label,
    quote,
    remaining_coupons,
    sale,
)
from app.shared.money import Currency, Money


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def bond(**overrides: object) -> BondIssue:
    values: dict[str, object] = {
        "code": "BOND-10Y-2037",
        "isin": "UG12K1405378",
        "name": "10 Year Treasury Bond 2037",
        "currency": Currency.UGX,
        "coupon_bps": 1600,
        "tax_bps": 1000,
        "issue_date": date(2025, 5, 14),
        "maturity_date": date(2037, 5, 14),
        "minimum_face": ugx(100_000),
        "face_step": ugx(100_000),
        "status": BondStatus.LIVE,
    }
    values.update(overrides)
    return BondIssue(**values)  # type: ignore[arg-type]


LARGEST = ugx(5_000_000_000)


# The issue itself ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": "bond"},
        {"isin": "UG12K14053"},
        {"isin": "ug12K1405378"},
        {"name": " Bond"},
        {"coupon_bps": 0},
        {"coupon_bps": 5001},
        {"tax_bps": 10_000},
        {"maturity_date": date(2025, 5, 14)},
        {"maturity_date": date(2060, 5, 14)},
        {"minimum_face": ugx(150_000)},
        {"face_step": Money(100, Currency.USD)},
        {"coupon_bps": 16.0},
    ],
)
def test_an_issue_that_contradicts_itself_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidIssue):
        bond(**overrides)


def test_a_bond_is_named_by_whole_years() -> None:
    assert bond().years == 12
    assert bond(issue_date=date(2027, 5, 15)).years == 9


@pytest.mark.parametrize(
    ("issued", "matures", "tax"),
    [
        (date(2026, 10, 28), date(2028, 11, 16), 2000),
        (date(2026, 10, 28), date(2032, 5, 20), 2000),
        (date(2027, 5, 18), date(2037, 5, 14), 1000),
        (date(2026, 10, 28), date(2050, 7, 7), 1000),
    ],
)
def test_the_usual_tax_is_twenty_percent_under_ten_years_and_ten_after(
    issued: date, matures: date, tax: int
) -> None:
    assert default_tax_bps(issued, matures) == tax


# Coupon dates -------------------------------------------------------------------------------


def test_coupons_fall_every_six_months_back_from_maturity() -> None:
    dates = coupon_dates(bond(issue_date=date(2035, 1, 1)))
    assert dates == (
        date(2035, 5, 14),
        date(2035, 11, 14),
        date(2036, 5, 14),
        date(2036, 11, 14),
        date(2037, 5, 14),
    )


def test_a_month_too_short_for_the_day_pays_on_its_last_day() -> None:
    issue = bond(issue_date=date(2026, 1, 1), maturity_date=date(2028, 8, 31))
    assert coupon_dates(issue) == (
        date(2026, 2, 28),
        date(2026, 8, 31),
        date(2027, 2, 28),
        date(2027, 8, 31),
        date(2028, 2, 29),
        date(2028, 8, 31),
    )


def test_the_period_around_a_day() -> None:
    issue = bond()
    assert period_around(issue, date(2026, 11, 16)) == (date(2026, 11, 14), date(2027, 5, 14))
    assert period_around(issue, date(2026, 11, 14)) == (date(2026, 11, 14), date(2027, 5, 14))
    # Before the first coupon, the period that began six months before it.
    assert period_around(issue, date(2025, 6, 1)) == (date(2025, 5, 14), date(2025, 11, 14))
    with pytest.raises(InvalidIssue):
        period_around(issue, date(2037, 5, 14))


# Coupons and accrued interest ---------------------------------------------------------------


def test_a_coupon_is_half_the_yearly_rate_rounded_down() -> None:
    assert coupon_gross(bond(), ugx(1_000_000)) == ugx(80_000)
    assert coupon_gross(bond(coupon_bps=1555), ugx(100_000)) == ugx(7_775)
    assert coupon_gross(bond(coupon_bps=1), ugx(100_000)) == ugx(5)


def test_accrued_interest_grows_through_the_period_and_never_reaches_the_coupon() -> None:
    issue = bond()
    face = ugx(1_000_000)
    start = date(2026, 11, 14)
    seen = []
    for days in range(181):
        seen.append(accrued_interest(issue, face, start + timedelta(days=days)).minor)
    assert seen[0] == 0
    assert seen == sorted(seen)
    assert seen[-1] < 80_000
    # Two days of a 181 day period: 80,000 x 2 / 181 = 883.97, rounded down.
    assert seen[2] == 883


# Quotes and buying --------------------------------------------------------------------------


def test_a_quote_is_price_plus_accrued_and_pays_every_coupon_left() -> None:
    found = quote(bond(), ugx(1_000_000), 985_000, date(2026, 11, 16))
    assert (found.clean, found.accrued, found.cost) == (ugx(985_000), ugx(883), ugx(985_883))
    assert (found.coupon_net, found.coupons_left) == (ugx(72_000), 21)
    assert found.next_coupon_date == date(2027, 5, 14)
    assert found.total_back == ugx(1_000_000 + 21 * 72_000)
    assert found.gain == found.total_back - found.cost
    shown = client_quote(found)
    assert not any("tax" in name or "gross" in name for name in shown.__dataclass_fields__)


def test_what_a_client_pays_rounds_up() -> None:
    # 100,000 at 98.12345 would be 98,123.45.
    assert quote(bond(), ugx(100_000), 981_235, date(2026, 11, 14)).clean == ugx(98_124)


@pytest.mark.parametrize(
    ("issue", "face", "on", "refusal"),
    [
        (bond(status=BondStatus.PAUSED), ugx(100_000), date(2026, 11, 16), Refusal.ISSUE_NOT_OPEN),
        (bond(), Money(100_000, Currency.USD), date(2026, 11, 16), Refusal.CURRENCY_NOT_SUPPORTED),
        (bond(), ugx(0), date(2026, 11, 16), Refusal.AMOUNT_NOT_POSITIVE),
        (bond(), ugx(50_000), date(2026, 11, 16), Refusal.BELOW_MINIMUM),
        (bond(), ugx(150_000), date(2026, 11, 16), Refusal.NOT_A_STEP),
        (bond(), ugx(5_000_100_000), date(2026, 11, 16), Refusal.ABOVE_MAXIMUM),
        (bond(), ugx(100_000), date(2036, 11, 14), Refusal.TOO_CLOSE_TO_MATURITY),
        (bond(), ugx(100_000), date(2036, 11, 13), None),
    ],
)
def test_who_may_buy(issue: BondIssue, face: Money, on: date, refusal: Refusal | None) -> None:
    assert check_purchase(issue, face, on, LARGEST) == refusal


def test_remaining_coupons_end_on_the_maturity_date() -> None:
    left = remaining_coupons(bond(), date(2036, 6, 1))
    assert left == (date(2036, 11, 14), date(2037, 5, 14))


# Selling early ------------------------------------------------------------------------------


def test_a_sale_pays_the_price_and_the_accrued_interest_less_its_tax() -> None:
    sold = sale(bond(), ugx(1_000_000), 970_000, date(2026, 11, 16))
    assert (sold.clean, sold.accrued, sold.tax) == (ugx(970_000), ugx(883), ugx(88))
    assert sold.proceeds == ugx(970_000 + 883 - 88)


def test_what_a_sale_pays_rounds_down() -> None:
    assert sale(bond(), ugx(100_000), 981_235, date(2026, 11, 14)).clean == ugx(98_123)


# Labels -------------------------------------------------------------------------------------


def test_labels() -> None:
    assert price_label(985_000) == "98.5000"
    assert price_label(PRICE_SCALE) == "100.0000"
    assert coupon_label(1600, 1000) == "14.40"
    assert coupon_label(1555, 2000) == "12.44"
    with pytest.raises(InvalidIssue):
        price_label(400_000)


# Changes ------------------------------------------------------------------------------------


def test_a_second_person_decides_and_only_the_proposer_withdraws() -> None:
    proposed = changes.BondChangeStatus.PROPOSED
    assert changes.check_decision(status=proposed, proposed_by="a", decided_by="a") is (
        changes.Refusal.OWN_PROPOSAL
    )
    assert changes.check_decision(status=proposed, proposed_by="a", decided_by="b") is None
    assert (
        changes.check_decision(
            status=changes.BondChangeStatus.APPROVED, proposed_by="a", decided_by="b"
        )
        is changes.Refusal.NOT_WAITING
    )
    assert changes.check_withdrawal(status=proposed, proposed_by="a", by="b") is (
        changes.Refusal.NOT_PROPOSER
    )
    assert changes.check_withdrawal(status=proposed, proposed_by="a", by="a") is None


def test_opening_needs_a_price_and_time_left() -> None:
    draft = bond(status=BondStatus.DRAFT)
    open_ = changes.BondChangeAction.OPEN
    unpriced = changes.Current(draft, 1, None)
    priced = changes.Current(draft, 1, 985_000)
    assert changes.check_status_change(unpriced, open_, can_be_bought=True) is (
        changes.Refusal.NO_PRICE
    )
    assert changes.check_status_change(priced, open_, can_be_bought=False) is (
        changes.Refusal.MATURED
    )
    assert changes.check_status_change(priced, open_, can_be_bought=True) is None
    live = changes.Current(bond(), 2, 985_000)
    assert changes.check_status_change(live, open_, can_be_bought=True) is (
        changes.Refusal.ALREADY_LIVE
    )
    assert (
        changes.check_status_change(live, changes.BondChangeAction.PAUSE, can_be_bought=True)
        is None
    )
    assert (
        changes.check_status_change(
            changes.Current(
                bond(
                    currency=Currency.USD,
                    minimum_face=Money(100_000, Currency.USD),
                    face_step=Money(100_000, Currency.USD),
                    status=BondStatus.DRAFT,
                ),
                1,
                985_000,
            ),
            open_,
            can_be_bought=True,
        )
        is changes.Refusal.CURRENCY_NOT_OPEN
    )
