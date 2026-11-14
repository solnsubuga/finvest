"""The allocation report for one product: bills held, cost behind investments, what is left."""

from datetime import date

from app.modules.dealing.domain.allocation_report import (
    Allocated,
    LotHeld,
    Owed,
    product_report,
)
from app.shared.money import Currency, Money

UGX = Currency.UGX


def ugx(minor: int) -> Money:
    return Money(minor, UGX)


def lot(lot_id: str, cost: int, face: int | None = None) -> LotHeld:
    return LotHeld(
        lot_id, date(2026, 11, 2), date(2027, 2, 1), f"CSD-{lot_id}", ugx(face or cost), ugx(cost)
    )


def test_an_empty_product_reports_zeros_in_its_currency() -> None:
    found = product_report("TREA-91", UGX, [], [], [])
    assert found.lots == ()
    assert (found.cost_held, found.allocated, found.unallocated) == (ugx(0), ugx(0), ugx(0))
    assert (found.investments, found.investments_waiting) == (0, 0)
    assert (found.owed, found.covered, found.waiting) == (ugx(0), ugx(0), ugx(0))


def test_cost_left_over_and_investments_waiting_are_both_shown() -> None:
    lots = [lot("a", 900, face=1_000), lot("b", 500)]
    allocations = [
        Allocated("a", "inv-1", ugx(600)),
        Allocated("a", "inv-2", ugx(300)),
        Allocated("b", "inv-2", ugx(100)),
    ]
    owed = [Owed("inv-1", ugx(600)), Owed("inv-2", ugx(700)), Owed("inv-3", ugx(250))]
    found = product_report("TREA-91", UGX, lots, allocations, owed)

    first, second = found.lots
    assert (first.lot_id, first.face, first.cost) == ("a", ugx(1_000), ugx(900))
    assert (first.allocated, first.unallocated, first.investments) == (ugx(900), ugx(0), 2)
    assert (second.allocated, second.unallocated, second.investments) == (ugx(100), ugx(400), 1)
    assert (found.cost_held, found.allocated, found.unallocated) == (
        ugx(1_400),
        ugx(1_000),
        ugx(400),
    )
    assert (found.investments, found.owed, found.covered) == (3, ugx(1_550), ugx(1_000))
    # inv-2 is short 300 and inv-3 has nothing yet.
    assert (found.investments_waiting, found.waiting) == (2, ugx(550))


def test_allocations_the_next_run_releases_count_in_neither_total() -> None:
    lots = [lot("a", 1_000)]
    allocations = [
        # The investment has ended.
        Allocated("a", "ended", ugx(400)),
        # The lot has been sold or has matured.
        Allocated("gone", "inv-1", ugx(200)),
        Allocated("a", "inv-1", ugx(100)),
    ]
    found = product_report("TREA-91", UGX, lots, allocations, [Owed("inv-1", ugx(300))])
    (line,) = found.lots
    assert (line.allocated, line.unallocated, line.investments) == (ugx(100), ugx(900), 1)
    assert (found.covered, found.waiting, found.investments_waiting) == (ugx(100), ugx(200), 1)


def test_an_investment_is_never_shown_covered_beyond_what_it_is_owed() -> None:
    # After an early withdrawal reduces it, until the next run releases the difference.
    found = product_report(
        "TREA-91",
        UGX,
        [lot("a", 1_000)],
        [Allocated("a", "inv-1", ugx(800))],
        [Owed("inv-1", ugx(500))],
    )
    assert (found.covered, found.waiting, found.investments_waiting) == (ugx(500), ugx(0), 0)
    assert found.allocated == ugx(800)
