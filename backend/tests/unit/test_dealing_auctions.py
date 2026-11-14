"""The auction calendar: which dates may be entered or cancelled, and each product's next one."""

from datetime import date

import pytest

from app.modules.dealing.domain.auctions import (
    AuctionDate,
    Refusal,
    check_cancel,
    check_new,
    next_auctions,
)

TODAY = date(2026, 11, 14)


def standing(product: str, day: date) -> AuctionDate:
    return AuctionDate(product, day, cancelled=False)


def cancelled(product: str, day: date) -> AuctionDate:
    return AuctionDate(product, day, cancelled=True)


def new(
    day: date, *, treasury_bill: bool = True, existing: tuple[AuctionDate, ...] = ()
) -> Refusal | None:
    return check_new("TREA-91", day, treasury_bill=treasury_bill, today=TODAY, existing=existing)


def test_a_future_date_for_a_treasury_bill_may_be_entered() -> None:
    assert new(date(2026, 11, 18)) is None


def test_today_may_be_entered_because_bids_close_during_the_day() -> None:
    assert new(TODAY) is None


def test_a_day_already_past_is_refused() -> None:
    assert new(date(2026, 11, 13)) is Refusal.DATE_PASSED


def test_a_product_that_is_not_a_treasury_bill_has_no_auctions() -> None:
    assert new(date(2026, 11, 18), treasury_bill=False) is Refusal.NOT_A_TREASURY_BILL


def test_the_same_product_and_day_is_entered_only_once() -> None:
    day = date(2026, 11, 18)
    assert new(day, existing=(standing("TREA-91", day),)) is Refusal.ALREADY_ENTERED


def test_a_cancelled_day_may_be_entered_again() -> None:
    day = date(2026, 11, 18)
    assert new(day, existing=(cancelled("TREA-91", day),)) is None


def test_another_product_on_the_same_day_does_not_stand_in_the_way() -> None:
    day = date(2026, 11, 18)
    assert new(day, existing=(standing("TREA-182", day),)) is None


def test_a_standing_date_is_cancelled_with_a_reason() -> None:
    assert check_cancel(standing("TREA-91", TODAY), "The Bank moved it to Thursday") is None


@pytest.mark.parametrize("reason", ["", "   ", "\n"])
def test_a_cancellation_needs_a_reason(reason: str) -> None:
    assert check_cancel(standing("TREA-91", TODAY), reason) is Refusal.REASON_REQUIRED


def test_a_date_is_cancelled_only_once() -> None:
    assert check_cancel(cancelled("TREA-91", TODAY), "Again") is Refusal.ALREADY_CANCELLED


def test_the_next_auction_is_the_earliest_standing_date_from_today() -> None:
    dates = [
        standing("TREA-91", date(2026, 12, 2)),
        standing("TREA-91", date(2026, 11, 18)),
        standing("TREA-91", date(2026, 11, 4)),
        cancelled("TREA-91", date(2026, 11, 16)),
        standing("TREA-182", TODAY),
    ]
    assert next_auctions(dates, TODAY) == {
        "TREA-91": date(2026, 11, 18),
        "TREA-182": TODAY,
    }


def test_a_product_with_only_past_or_cancelled_dates_has_no_next_auction() -> None:
    dates = [standing("TREA-91", date(2026, 11, 4)), cancelled("TREA-91", date(2026, 11, 18))]
    assert next_auctions(dates, TODAY) == {}
