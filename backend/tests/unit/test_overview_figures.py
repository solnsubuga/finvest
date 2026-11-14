"""The Overview's figures from what the owning modules read."""

from datetime import UTC, date, datetime, timedelta

from app.modules.overview.domain.figures import (
    FLOAT_WARNING_LINE,
    Attention,
    MaturingDay,
    SourceDay,
    attention,
    balancing,
    floats,
    flows,
    ladder,
    ladder_window,
)
from app.shared.money import Currency, Money

TODAY = date(2026, 11, 14)  # a Saturday


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def test_every_one_of_seven_days_is_listed_today_last() -> None:
    days = flows([], Currency.UGX, today=TODAY)
    assert [day.day for day in days] == [TODAY - timedelta(days=n) for n in range(6, -1, -1)]
    assert all(day.money_in == ugx(0) and day.money_out == ugx(0) for day in days)


def test_deposits_are_in_and_payouts_less_returns_are_out() -> None:
    days = flows(
        [
            SourceDay(TODAY, "payment.deposit", ugx(500_000), ugx(0)),
            SourceDay(TODAY, "payout", ugx(20_000), ugx(150_000)),
            # A pot transfer is neither, and a day outside the window is ignored.
            SourceDay(TODAY, "reconciliation.pot_transfer", ugx(9), ugx(9)),
            SourceDay(TODAY - timedelta(days=7), "payment.deposit", ugx(1), ugx(0)),
        ],
        Currency.UGX,
        today=TODAY,
    )
    assert (days[-1].money_in, days[-1].money_out) == (ugx(500_000), ugx(130_000))
    assert days[0].money_in == ugx(0)


def test_the_ladder_is_this_week_from_today_then_three_whole_weeks() -> None:
    first, last = ladder_window(TODAY)
    assert (first, last) == (TODAY, date(2026, 12, 6))
    weeks = ladder(
        [
            MaturingDay(TODAY, 2, ugx(300), ugx(100)),
            MaturingDay(date(2026, 11, 15), 1, ugx(50), ugx(0)),  # Sunday, still this week
            MaturingDay(date(2026, 11, 16), 3, ugx(700), ugx(700)),  # Monday, the next week
            MaturingDay(date(2026, 12, 7), 9, ugx(9), ugx(9)),  # past the window
        ],
        Currency.UGX,
        today=TODAY,
    )
    assert [(w.first_day, w.last_day) for w in weeks] == [
        (TODAY, date(2026, 11, 15)),
        (date(2026, 11, 16), date(2026, 11, 22)),
        (date(2026, 11, 23), date(2026, 11, 29)),
        (date(2026, 11, 30), date(2026, 12, 6)),
    ]
    assert (weeks[0].investments, weeks[0].principal, weeks[0].pay_out_principal) == (
        3,
        ugx(350),
        ugx(100),
    )
    assert (weeks[1].investments, weeks[1].principal) == (3, ugx(700))
    assert weeks[3].investments == 0


def test_a_monday_ladder_starts_with_a_whole_week() -> None:
    monday = date(2026, 11, 16)
    weeks = ladder([], Currency.UGX, today=monday)
    assert (weeks[0].first_day, weeks[0].last_day) == (monday, date(2026, 11, 22))


def test_each_network_has_a_float_and_low_is_below_the_line() -> None:
    found = floats({"mtn": FLOAT_WARNING_LINE - ugx(1), "airtel": FLOAT_WARNING_LINE})
    assert [(f.network, f.low) for f in found] == [("mtn", True), ("airtel", False)]
    assert floats({})[0].balance == ugx(0)


def test_balancing_counts_ended_days_not_closed() -> None:
    assert balancing(TODAY - timedelta(days=1), None, today=TODAY).days_behind == 0
    assert balancing(TODAY - timedelta(days=4), None, today=TODAY).days_behind == 3
    assert balancing(None, None, today=TODAY).days_behind == 0


def test_attention_shows_warnings_first_and_nothing_at_zero() -> None:
    since = datetime(2026, 11, 14, 6, tzinfo=UTC)
    shown = attention(
        [
            Attention("approvals_for_you", "info", 2, since=since),
            Attention("review_queue", "info", 0),
            Attention("float_low", "warning", 1, subject="mtn"),
            Attention("books_not_balanced", "warning", 3),
        ]
    )
    assert [item.code for item in shown] == ["float_low", "books_not_balanced", "approvals_for_you"]
