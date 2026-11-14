"""Transaction monitoring rules: each rule at its edges, and each alert raised once."""

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from app.modules.screening.domain.monitoring import (
    LARGE_AMOUNT,
    MANY_COUNT,
    MANY_WINDOW,
    QUICK_FLOOR,
    QUICK_WINDOW,
    SPLIT_WINDOW,
    UNUSUAL_FLOOR,
    UNUSUAL_HISTORY,
    UNUSUAL_LOOKBACK,
    UNUSUAL_MULTIPLE,
    Kind,
    Rule,
    Transaction,
    evaluate,
    median_minor,
)
from app.shared.money import Currency

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
CLIENT = "client-1"
_ids = count(1)


def tx(
    amount: int,
    *,
    ago: timedelta = timedelta(0),
    kind: Kind = Kind.DEPOSIT,
    currency: Currency = Currency.UGX,
    id: str | None = None,
) -> Transaction:
    return Transaction(id or f"t-{next(_ids)}", kind, amount, currency, NOW - ago)


def rules(new: Transaction, earlier: list[Transaction] | None = None) -> set[Rule]:
    return {alert.rule for alert in evaluate(CLIENT, new, earlier or [])}


@pytest.mark.parametrize("amount", [0, -1])
def test_an_amount_is_positive(amount: int) -> None:
    with pytest.raises(ValueError):
        tx(amount)


def test_an_amount_is_a_whole_number() -> None:
    with pytest.raises(ValueError):
        Transaction("t", Kind.DEPOSIT, 1.5, Currency.UGX, NOW)  # type: ignore[arg-type]


def test_a_small_deposit_with_no_history_raises_nothing() -> None:
    assert evaluate(CLIENT, tx(100_000), []) == ()


def test_dollars_are_not_read_yet() -> None:
    assert evaluate(CLIENT, tx(LARGE_AMOUNT * 10, currency=Currency.USD), []) == ()
    # Earlier dollar deposits count towards nothing either.
    earlier = [tx(LARGE_AMOUNT - 1, currency=Currency.USD, ago=timedelta(hours=1))]
    assert rules(tx(2), earlier) == set()


# Large deposit


def test_a_deposit_at_the_large_amount_raises_it() -> None:
    new = tx(LARGE_AMOUNT, id="d-1")
    [alert] = evaluate(CLIENT, new, [])
    assert alert.rule is Rule.LARGE_DEPOSIT
    assert alert.dedupe_key == "d-1"
    assert alert.detail == {
        "deposit_id": "d-1",
        "amount_minor": LARGE_AMOUNT,
        "threshold": LARGE_AMOUNT,
    }


def test_a_deposit_just_under_the_large_amount_does_not() -> None:
    assert Rule.LARGE_DEPOSIT not in rules(tx(LARGE_AMOUNT - 1))


# Split deposits


def test_deposits_that_together_reach_the_amount_within_a_week_raise_it() -> None:
    first = tx(LARGE_AMOUNT // 2, ago=timedelta(days=3), id="a")
    second = tx(LARGE_AMOUNT // 2 - 1, ago=timedelta(days=1), id="b")
    new = tx(1, id="c")
    [alert] = evaluate(CLIENT, new, [second, first])
    assert alert.rule is Rule.SPLIT_DEPOSITS
    assert alert.detail["deposit_ids"] == ["a", "b", "c"]
    assert alert.detail["total_minor"] == LARGE_AMOUNT
    # Week 46 of 2026 in Kampala.
    assert alert.dedupe_key == f"{CLIENT}:2026-W46"


def test_a_total_just_under_the_amount_does_not() -> None:
    earlier = [tx(LARGE_AMOUNT // 2, ago=timedelta(days=2))]
    assert Rule.SPLIT_DEPOSITS not in rules(tx(LARGE_AMOUNT // 2 - 1), earlier)


def test_a_deposit_a_week_old_falls_out_of_the_window() -> None:
    earlier = [tx(LARGE_AMOUNT - 1, ago=SPLIT_WINDOW)]
    assert Rule.SPLIT_DEPOSITS not in rules(tx(1), earlier)
    inside = [tx(LARGE_AMOUNT - 1, ago=SPLIT_WINDOW - timedelta(seconds=1))]
    assert Rule.SPLIT_DEPOSITS in rules(tx(1), inside)


def test_only_the_deposit_that_crosses_the_amount_raises_it() -> None:
    earlier = [tx(LARGE_AMOUNT - 1, ago=timedelta(days=2)), tx(5, ago=timedelta(days=1))]
    assert Rule.SPLIT_DEPOSITS not in rules(tx(5), earlier)


def test_a_large_deposit_is_not_also_a_split_one() -> None:
    earlier = [tx(1_000, ago=timedelta(days=1))]
    assert rules(tx(LARGE_AMOUNT), earlier) == {Rule.LARGE_DEPOSIT}
    # And an earlier large deposit does not count towards a split.
    earlier = [tx(LARGE_AMOUNT, ago=timedelta(days=1))]
    assert Rule.SPLIT_DEPOSITS not in rules(tx(1_000), earlier)


def test_payouts_do_not_count_towards_a_split() -> None:
    earlier = [tx(LARGE_AMOUNT - 1, ago=timedelta(days=1), kind=Kind.PAYOUT)]
    assert Rule.SPLIT_DEPOSITS not in rules(tx(1), earlier)


# Many deposits


def test_the_deposit_that_reaches_the_count_in_a_day_raises_it() -> None:
    earlier = [tx(1_000, ago=timedelta(hours=h), id=f"m{h}") for h in range(1, MANY_COUNT)]
    new = tx(1_000, id="m0")
    [alert] = evaluate(CLIENT, new, earlier)
    assert alert.rule is Rule.MANY_DEPOSITS
    assert alert.detail["count"] == MANY_COUNT
    assert alert.detail["hours"] == 24
    assert alert.detail["deposit_ids"] == [f"m{h}" for h in range(MANY_COUNT - 1, 0, -1)] + ["m0"]
    assert alert.dedupe_key == f"{CLIENT}:2026-11-14"


def test_one_fewer_does_not_and_one_more_does_not_raise_it_again() -> None:
    fewer = [tx(1_000, ago=timedelta(hours=h)) for h in range(1, MANY_COUNT - 1)]
    assert Rule.MANY_DEPOSITS not in rules(tx(1_000), fewer)
    more = [tx(1_000, ago=timedelta(hours=h)) for h in range(1, MANY_COUNT + 1)]
    assert Rule.MANY_DEPOSITS not in rules(tx(1_000), more)


def test_deposits_a_day_old_fall_out_of_the_count() -> None:
    earlier = [tx(1_000, ago=timedelta(hours=h)) for h in range(1, MANY_COUNT - 1)]
    earlier.append(tx(1_000, ago=MANY_WINDOW))
    assert Rule.MANY_DEPOSITS not in rules(tx(1_000), earlier)


# Unusual deposit


def usual(amount: int, n: int = UNUSUAL_HISTORY) -> list[Transaction]:
    return [tx(amount, ago=timedelta(days=30 + i)) for i in range(n)]


def test_a_deposit_five_times_the_usual_raises_it() -> None:
    new = tx(UNUSUAL_FLOOR, id="u")
    [alert] = evaluate(CLIENT, new, usual(UNUSUAL_FLOOR // UNUSUAL_MULTIPLE))
    assert alert.rule is Rule.UNUSUAL_DEPOSIT
    assert alert.dedupe_key == "u"
    assert alert.detail["usual_minor"] == UNUSUAL_FLOOR // UNUSUAL_MULTIPLE
    assert alert.detail["earlier_deposits"] == UNUSUAL_HISTORY


def test_just_under_five_times_the_usual_does_not() -> None:
    history = usual(UNUSUAL_FLOOR // UNUSUAL_MULTIPLE + 1)
    assert Rule.UNUSUAL_DEPOSIT not in rules(tx(UNUSUAL_FLOOR), history)


def test_an_unusual_deposit_under_the_floor_does_not() -> None:
    assert Rule.UNUSUAL_DEPOSIT not in rules(tx(UNUSUAL_FLOOR - 1), usual(1_000))


def test_too_little_history_says_nothing_about_usual() -> None:
    history = usual(1_000, UNUSUAL_HISTORY - 1)
    assert Rule.UNUSUAL_DEPOSIT not in rules(tx(UNUSUAL_FLOOR), history)


def test_old_deposits_are_not_usual_any_more() -> None:
    old = [tx(1_000, ago=UNUSUAL_LOOKBACK + timedelta(days=i)) for i in range(UNUSUAL_HISTORY)]
    assert Rule.UNUSUAL_DEPOSIT not in rules(tx(UNUSUAL_FLOOR), old)


def test_one_large_earlier_deposit_does_not_move_the_usual() -> None:
    # The median, not the mean: a single big deposit does not hide the next one.
    history = [*usual(1_000_000), tx(30_000_000, ago=timedelta(days=60))]
    assert Rule.UNUSUAL_DEPOSIT in rules(tx(UNUSUAL_FLOOR), history)


def test_a_large_and_unusual_deposit_raises_both() -> None:
    assert rules(tx(LARGE_AMOUNT), usual(1_000_000)) == {Rule.LARGE_DEPOSIT, Rule.UNUSUAL_DEPOSIT}


# Quick withdrawal


def test_a_payout_of_half_of_last_weeks_deposits_raises_it() -> None:
    earlier = [
        tx(QUICK_FLOOR, ago=timedelta(days=2), id="q1"),
        tx(QUICK_FLOOR, ago=timedelta(days=1), id="q2"),
    ]
    new = tx(QUICK_FLOOR, kind=Kind.PAYOUT, id="p")
    [alert] = evaluate(CLIENT, new, earlier)
    assert alert.rule is Rule.QUICK_WITHDRAWAL
    assert alert.dedupe_key == "p"
    assert alert.detail == {
        "payout_id": "p",
        "amount_minor": QUICK_FLOOR,
        "deposits_minor": 2 * QUICK_FLOOR,
        "deposit_ids": ["q1", "q2"],
        "days": 7,
    }


def test_a_payout_just_under_half_does_not() -> None:
    earlier = [tx(2 * QUICK_FLOOR, ago=timedelta(days=1))]
    assert rules(tx(QUICK_FLOOR - 1, kind=Kind.PAYOUT), earlier) == set()


def test_small_deposits_paid_straight_out_do_not() -> None:
    earlier = [tx(QUICK_FLOOR - 1, ago=timedelta(days=1))]
    assert rules(tx(QUICK_FLOOR - 1, kind=Kind.PAYOUT), earlier) == set()


def test_deposits_older_than_a_week_do_not_count() -> None:
    earlier = [tx(QUICK_FLOOR, ago=QUICK_WINDOW)]
    assert rules(tx(QUICK_FLOOR, kind=Kind.PAYOUT), earlier) == set()


def test_a_payout_raises_no_deposit_rule() -> None:
    earlier = [tx(1_000, ago=timedelta(hours=h)) for h in range(1, MANY_COUNT)]
    assert rules(tx(LARGE_AMOUNT, kind=Kind.PAYOUT), earlier) == set()


# What counts as earlier


def test_the_new_transaction_and_anything_after_it_are_ignored() -> None:
    new = tx(1, id="same")
    later = tx(LARGE_AMOUNT - 1, ago=-timedelta(hours=1))
    assert rules(new, [new, later]) == set()


def test_a_repeat_raises_the_same_keys() -> None:
    earlier = [tx(LARGE_AMOUNT // 2, ago=timedelta(days=1))]
    new = tx(LARGE_AMOUNT, id="r")
    first = evaluate(CLIENT, new, earlier)
    assert first == evaluate(CLIENT, new, list(reversed(earlier)))


@pytest.mark.parametrize(
    ("amounts", "middle"),
    [([7], 7), ([3, 1], 1), ([5, 1, 3], 3), ([4, 1, 3, 2], 2), ([2, 2, 9, 9], 2)],
)
def test_the_median_is_the_lower_middle(amounts: list[int], middle: int) -> None:
    assert median_minor(amounts) == middle


def test_there_is_no_median_of_nothing() -> None:
    with pytest.raises(ValueError):
        median_minor([])


# Client-to-client transfers ---------------------------------------------------------------


@pytest.mark.parametrize("kind", [Kind.DEPOSIT, Kind.TRANSFER_RECEIVED])
def test_money_received_by_transfer_counts_as_a_deposit(kind: Kind) -> None:
    assert rules(tx(LARGE_AMOUNT, kind=kind)) == {Rule.LARGE_DEPOSIT}
    assert rules(tx(LARGE_AMOUNT - 1, kind=kind)) == set()


@pytest.mark.parametrize("kind", [Kind.PAYOUT, Kind.TRANSFER_SENT])
def test_money_sent_by_transfer_counts_as_a_payout(kind: Kind) -> None:
    earlier = [tx(QUICK_FLOOR, ago=timedelta(days=1))]
    assert rules(tx(QUICK_FLOOR // 2, kind=kind), earlier) == {Rule.QUICK_WITHDRAWAL}
    assert rules(tx(QUICK_FLOOR // 2 - 1, kind=kind), earlier) == set()


def test_money_sent_by_transfer_never_counts_as_money_in() -> None:
    assert rules(tx(LARGE_AMOUNT, kind=Kind.TRANSFER_SENT)) == set()
    sent_before = [tx(QUICK_FLOOR * 4, kind=Kind.TRANSFER_SENT, ago=timedelta(days=1))]
    assert rules(tx(QUICK_FLOOR, kind=Kind.PAYOUT), sent_before) == set()
    assert (
        rules(tx(LARGE_AMOUNT - 1), [tx(1, kind=Kind.TRANSFER_SENT, ago=timedelta(hours=1))])
        == set()
    )


def test_a_transfer_in_and_a_deposit_add_up_to_a_split() -> None:
    half = LARGE_AMOUNT // 2
    received = tx(half, kind=Kind.TRANSFER_RECEIVED, ago=timedelta(days=1), id="transfer-1")
    deposit = tx(LARGE_AMOUNT - half, id="deposit-1")
    (alert,) = evaluate(CLIENT, deposit, [received])
    assert alert.rule is Rule.SPLIT_DEPOSITS
    assert alert.detail["deposit_ids"] == ["transfer-1", "deposit-1"]
    assert alert.detail["transfer_ids"] == ["transfer-1"]


def test_a_quick_transfer_out_names_the_transfers_it_saw() -> None:
    came_in = [
        tx(QUICK_FLOOR, kind=Kind.TRANSFER_RECEIVED, ago=timedelta(days=2), id="transfer-in"),
        tx(QUICK_FLOOR, ago=timedelta(days=1), id="deposit-1"),
    ]
    (alert,) = evaluate(
        CLIENT, tx(QUICK_FLOOR, kind=Kind.TRANSFER_SENT, id="transfer-out"), came_in
    )
    assert alert.rule is Rule.QUICK_WITHDRAWAL
    assert alert.detail["payout_id"] == "transfer-out"
    assert alert.detail["deposits_minor"] == 2 * QUICK_FLOOR
    assert alert.detail["transfer_ids"] == ["transfer-in", "transfer-out"]


def test_an_alert_with_no_transfer_names_none() -> None:
    (alert,) = evaluate(CLIENT, tx(LARGE_AMOUNT), [])
    assert "transfer_ids" not in alert.detail
