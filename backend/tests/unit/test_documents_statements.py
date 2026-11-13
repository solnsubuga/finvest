from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.documents.domain import formatting
from app.modules.documents.domain.receipts import Receipt, masked
from app.modules.documents.domain.statements import (
    MAX_PERIOD_DAYS,
    Activity,
    ActivityKind,
    Holding,
    Period,
    PeriodRefused,
    Statement,
    StatementDoesNotAddUp,
    StatementKind,
    group_interest,
    month_end,
    previous_month,
    requested_period,
    summarise,
)
from app.shared.business_day import KAMPALA
from app.shared.money import Currency, Money


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def usd(cents: int) -> Money:
    return Money(cents, Currency.USD)


def line(on: date, kind: ActivityKind, amount: Money, reference: str = "IN-1") -> Activity:
    return Activity(on, kind, f"{kind.value} line", reference, amount)


# Formatting


@pytest.mark.parametrize(
    ("value", "written"),
    [
        (ugx(0), "UGX 0"),
        (ugx(1_000_000), "UGX 1,000,000"),
        (ugx(-2_073_250), "-UGX 2,073,250"),
        (usd(574), "$5.74"),
        (usd(5), "$0.05"),
        (usd(123_456_789), "$1,234,567.89"),
    ],
)
def test_money_is_written_from_its_exact_minor_units(value: Money, written: str) -> None:
    assert formatting.money(value) == written


def test_signed_marks_money_in_with_a_plus() -> None:
    assert formatting.signed(ugx(5)) == "+UGX 5"
    assert formatting.signed(ugx(-5)) == "-UGX 5"
    assert formatting.signed(ugx(0)) == "UGX 0"


def test_dates_and_moments_are_written_in_kampala_time() -> None:
    assert formatting.day(date(2026, 11, 3)) == "3 Nov 2026"
    assert formatting.long_day(date(2026, 11, 3)) == "3 November 2026"
    assert formatting.month(date(2026, 10, 9)) == "October 2026"
    utc_evening = datetime(2026, 11, 13, 22, 30, tzinfo=UTC)
    assert formatting.moment(utc_evening) == "14 Nov 2026, 01:30"
    with pytest.raises(ValueError, match="timezone-aware"):
        formatting.moment(datetime(2026, 11, 13, 12, 0))


def test_account_numbers_are_grouped_four_three_three() -> None:
    assert formatting.account_number("1001004827") == "1001 004 827"
    for wrong in ("100100482", "10010048271", "1001 004 827", "100100482x"):
        with pytest.raises(ValueError, match="ten digits"):
            formatting.account_number(wrong)


# Periods


def test_previous_month_is_the_whole_calendar_month_before() -> None:
    assert previous_month(date(2026, 11, 1)) == Period(date(2026, 10, 1), date(2026, 10, 31))
    assert previous_month(date(2026, 3, 15)) == Period(date(2026, 2, 1), date(2026, 2, 28))
    assert previous_month(date(2028, 3, 1)) == Period(date(2028, 2, 1), date(2028, 2, 29))
    assert previous_month(date(2027, 1, 1)) == Period(date(2026, 12, 1), date(2026, 12, 31))


def test_every_month_end_is_the_last_day() -> None:
    for month in range(1, 13):
        end = month_end(date(2026, month, 10))
        assert (end + timedelta(days=1)).day == 1


def test_a_whole_month_is_labelled_by_its_name_and_anything_else_by_its_dates() -> None:
    assert Period(date(2026, 10, 1), date(2026, 10, 31)).label == "October 2026"
    assert Period(date(2026, 10, 1), date(2026, 10, 30)).label == "1 Oct 2026 to 30 Oct 2026"
    assert Period(date(2026, 8, 1), date(2026, 11, 13)).label == "1 Aug 2026 to 13 Nov 2026"


def test_a_requested_period_may_end_today_and_be_one_day_long() -> None:
    today = date(2026, 11, 13)
    assert requested_period(today, today, today=today) == Period(today, today)


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        (date(2026, 11, 14), date(2026, 11, 13), "period_backwards"),
        (date(2026, 11, 1), date(2026, 11, 14), "period_in_future"),
        (date(2025, 11, 12), date(2026, 11, 13), "period_too_long"),
    ],
)
def test_a_requested_period_is_refused_with_a_reason(start: date, end: date, code: str) -> None:
    with pytest.raises(PeriodRefused) as refused:
        requested_period(start, end, today=date(2026, 11, 13))
    assert refused.value.code == code


def test_the_longest_period_is_exactly_the_limit() -> None:
    end = date(2026, 11, 13)
    start = end - timedelta(days=MAX_PERIOD_DAYS - 1)
    assert requested_period(start, end, today=end).start == start
    with pytest.raises(PeriodRefused):
        requested_period(start - timedelta(days=1), end, today=end)


# Activity lines


def test_an_activity_line_must_move_money_in_its_own_direction() -> None:
    on = date(2026, 10, 1)
    with pytest.raises(ValueError, match="moves money"):
        line(on, ActivityKind.EARNED, ugx(0))
    with pytest.raises(ValueError, match="adds"):
        line(on, ActivityKind.PAID_IN, ugx(-1))
    with pytest.raises(ValueError, match="adds"):
        line(on, ActivityKind.EARNED, ugx(-1))
    with pytest.raises(ValueError, match="takes"):
        line(on, ActivityKind.PAID_OUT, ugx(1))
    assert line(on, ActivityKind.CORRECTION, ugx(-1)).amount == ugx(-1)
    assert line(on, ActivityKind.CORRECTION, ugx(1)).amount == ugx(1)


# Summaries


def test_a_summary_adds_up_every_kind_of_line() -> None:
    on = date(2026, 10, 5)
    lines = [
        line(on, ActivityKind.PAID_IN, ugx(1_000_000)),
        line(on, ActivityKind.EARNED, ugx(300)),
        line(on, ActivityKind.EARNED, ugx(301)),
        line(on, ActivityKind.PAID_OUT, ugx(-500_000)),
        line(on, ActivityKind.CORRECTION, ugx(-1)),
    ]
    summary = summarise(Currency.UGX, opening=ugx(10), closing=ugx(500_610), activity=lines)
    assert summary.paid_in == ugx(1_000_000)
    assert summary.earned == ugx(601)
    assert summary.paid_out == ugx(500_000)
    assert summary.corrections == ugx(-1)


def test_a_summary_that_is_one_shilling_out_is_refused() -> None:
    lines = [line(date(2026, 10, 5), ActivityKind.EARNED, ugx(100))]
    with pytest.raises(StatementDoesNotAddUp):
        summarise(Currency.UGX, opening=ugx(0), closing=ugx(101), activity=lines)
    with pytest.raises(StatementDoesNotAddUp):
        summarise(Currency.UGX, opening=ugx(0), closing=ugx(99), activity=lines)


def test_a_summary_counts_only_its_own_currency() -> None:
    on = date(2026, 10, 5)
    lines = [line(on, ActivityKind.PAID_IN, usd(10_000)), line(on, ActivityKind.EARNED, ugx(7))]
    assert summarise(Currency.UGX, opening=ugx(0), closing=ugx(7), activity=lines).earned == ugx(7)
    dollars = summarise(Currency.USD, opening=usd(0), closing=usd(10_000), activity=lines)
    assert dollars.paid_in == usd(10_000)
    assert dollars.earned == usd(0)


# Folding daily interest


def test_daily_interest_folds_into_one_line_per_investment_and_month() -> None:
    lines = [
        line(date(2026, 9, 29), ActivityKind.EARNED, ugx(10), "IN-1"),
        line(date(2026, 9, 30), ActivityKind.EARNED, ugx(11), "IN-1"),
        line(date(2026, 10, 1), ActivityKind.EARNED, ugx(12), "IN-1"),
        line(date(2026, 10, 1), ActivityKind.EARNED, ugx(5), "IN-2"),
        line(date(2026, 10, 2), ActivityKind.EARNED, ugx(6), "IN-2"),
    ]
    folded = group_interest(lines)
    assert [(item.on, item.reference, item.amount) for item in folded] == [
        (date(2026, 9, 30), "IN-1", ugx(21)),
        (date(2026, 10, 1), "IN-1", ugx(12)),
        (date(2026, 10, 2), "IN-2", ugx(11)),
    ]


def test_folding_keeps_every_shilling_and_every_other_line() -> None:
    start = date(2026, 8, 1)
    lines = [
        line(start + timedelta(days=n), ActivityKind.EARNED, ugx(n % 7 + 1), f"IN-{n % 3}")
        for n in range(120)
    ]
    lines.append(line(date(2026, 9, 10), ActivityKind.PAID_IN, ugx(1_000)))
    lines.append(line(date(2026, 9, 10), ActivityKind.PAID_OUT, ugx(-400)))
    lines.sort(key=lambda item: item.on)
    folded = group_interest(lines)
    assert sum((item.amount for item in folded), ugx(0)) == sum(
        (item.amount for item in lines), ugx(0)
    )
    assert len([item for item in folded if item.kind is ActivityKind.EARNED]) <= 3 * 4
    assert [item.on for item in folded] == sorted(item.on for item in folded)


def test_within_a_day_money_in_comes_first_and_money_out_last() -> None:
    on = date(2026, 10, 24)
    folded = group_interest(
        [
            line(on, ActivityKind.PAID_OUT, ugx(-100)),
            line(on, ActivityKind.CORRECTION, ugx(1)),
            line(on, ActivityKind.EARNED, ugx(9)),
            line(on, ActivityKind.PAID_IN, ugx(50)),
        ]
    )
    assert [item.kind for item in folded] == [
        ActivityKind.PAID_IN,
        ActivityKind.EARNED,
        ActivityKind.CORRECTION,
        ActivityKind.PAID_OUT,
    ]


# A whole statement


def _statement(**changes: object) -> Statement:
    period = Period(date(2026, 10, 1), date(2026, 10, 31))
    activity = (line(date(2026, 10, 3), ActivityKind.PAID_IN, ugx(500)),)
    fields: dict[str, object] = {
        "reference": "ST-1",
        "kind": StatementKind.MONTHLY,
        "client_name": "Amara Nakato",
        "account_number": "1001004827",
        "period": period,
        "issued_on": date(2026, 11, 1),
        "summaries": (
            summarise(Currency.UGX, opening=ugx(0), closing=ugx(500), activity=activity),
        ),
        "holdings": (),
        "activity": activity,
    }
    fields.update(changes)
    return Statement(**fields)  # type: ignore[arg-type]


def test_a_statement_refuses_lines_outside_its_period_or_out_of_order() -> None:
    with pytest.raises(ValueError, match="outside"):
        _statement(activity=(line(date(2026, 11, 1), ActivityKind.PAID_IN, ugx(1)),))
    with pytest.raises(ValueError, match="date order"):
        _statement(
            activity=(
                line(date(2026, 10, 5), ActivityKind.PAID_IN, ugx(1)),
                line(date(2026, 10, 4), ActivityKind.PAID_IN, ugx(1)),
            )
        )
    with pytest.raises(ValueError, match="ten digits"):
        _statement(account_number="12345")
    with pytest.raises(ValueError, match="one currency"):
        _statement(summaries=())


def test_a_statement_with_nothing_held_and_nothing_done_is_empty() -> None:
    assert not _statement().is_empty
    empty = summarise(Currency.UGX, opening=ugx(0), closing=ugx(0), activity=())
    assert _statement(activity=(), summaries=(empty,)).is_empty
    held = summarise(Currency.UGX, opening=ugx(5), closing=ugx(5), activity=())
    assert not _statement(activity=(), summaries=(held,)).is_empty


def test_holdings_are_plain_records() -> None:
    holding = Holding("91 Day Treasury Bill", "IN-1", None, None, "Waiting", ugx(1), ugx(0), ugx(1))
    assert holding.value_at_end == ugx(1)


# Receipts


def test_a_payers_number_is_shown_by_its_last_three_digits() -> None:
    assert masked("+256 772 123 456") == "number ending 456"
    assert masked("0772123456") == "number ending 456"
    with pytest.raises(ValueError):
        masked("12345")


def test_a_receipt_is_for_money_received_at_a_known_time() -> None:
    fields: dict[str, object] = {
        "reference": "RC-1",
        "client_name": "Amara Nakato",
        "account_number": "1001004827",
        "amount": ugx(1_000_000),
        "received_at": datetime(2026, 11, 13, 15, 32, tzinfo=KAMPALA),
        "method": "MTN Mobile Money",
        "paid_from": "number ending 456",
        "provider_reference": None,
        "payment_reference": "FV-1",
        "paid_for": "91 Day Treasury Bill",
    }
    assert Receipt(**fields).amount == ugx(1_000_000)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="received"):
        Receipt(**{**fields, "amount": ugx(0)})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone"):
        Receipt(**{**fields, "received_at": datetime(2026, 11, 13, 15, 32)})  # type: ignore[arg-type]
