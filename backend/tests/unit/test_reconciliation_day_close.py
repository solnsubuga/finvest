from dataclasses import replace
from datetime import date

import pytest

from app.modules.reconciliation.domain.day_close import (
    CloseError,
    CloseStatus,
    DayClose,
    PotPosition,
    approve,
    blockers,
    may_post_on,
    next_day_to_close,
    prepare,
    send_back,
)
from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.money import Currency, Money

DAY = date(2026, 11, 13)
MAKER, CHECKER = "staff-maker", "staff-checker"
REQUIRED = [
    (StatementSource.IOTEC, "mtn"),
    (StatementSource.IOTEC, "airtel"),
    (StatementSource.BANK, "trust"),
]


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def pot(
    source: StatementSource,
    reference: str,
    statement: int | None = 1_000,
    ledger: int = 1_000,
    exceptions: int = 0,
    count: int = 0,
) -> PotPosition:
    closing = None if statement is None else ugx(statement)
    return PotPosition(source, reference, closing, ugx(ledger), ugx(exceptions), count)


def tied() -> list[PotPosition]:
    return [pot(source, reference) for source, reference in REQUIRED]


def check(positions: list[PotPosition], drift: int = 0, previous: bool = True) -> tuple[str, ...]:
    return blockers(
        positions, balance_drift_accounts=drift, previous_day_closed=previous, required=REQUIRED
    )


def test_a_day_that_ties_has_nothing_blocking_it() -> None:
    assert check(tied()) == ()


@pytest.mark.parametrize(
    ("statement", "ledger", "exceptions", "unexplained"),
    [
        (1_000, 1_000, 0, 0),
        (1_100, 1_000, 100, 0),  # a deposit on the statement, not yet in the books
        (900, 1_000, -100, 0),  # a payout in the books that has not reached the statement
        (1_100, 1_000, 0, 100),
        (1_000, 1_100, 0, -100),
        (1_100, 1_000, 99, 1),  # one shilling is enough to stop the close
        (1_000, 1_000, 5, -5),  # an exception with no difference behind it
    ],
)
def test_every_shilling_of_difference_must_be_an_open_exception(
    statement: int, ledger: int, exceptions: int, unexplained: int
) -> None:
    position = pot(StatementSource.BANK, "trust", statement, ledger, exceptions, 1)
    assert position.unexplained == ugx(unexplained)
    positions = [*tied()[:2], position]
    assert check(positions) == (() if unexplained == 0 else ("difference_unexplained",))


def test_a_missing_statement_blocks_the_close() -> None:
    positions = [*tied()[:2], pot(StatementSource.BANK, "trust", statement=None)]
    assert positions[2].unexplained is None
    assert check(positions) == ("statement_missing",)


def test_a_pot_cannot_be_left_out_or_listed_twice() -> None:
    assert check(tied()[:2]) == ("pot_missing",)
    assert check([*tied(), tied()[0]]) == ("pot_listed_twice",)
    assert check([]) == ("pot_missing",)


def test_ledger_drift_and_an_open_previous_day_block_the_close() -> None:
    assert check(tied(), drift=1) == ("ledger_balance_drift",)
    assert check(tied(), previous=False) == ("previous_day_not_closed",)


def test_every_blocker_is_reported_once() -> None:
    positions = [
        pot(StatementSource.IOTEC, "mtn", 1, 2),
        pot(StatementSource.IOTEC, "airtel", 1, 2),
        pot(StatementSource.BANK, "trust", statement=None),
    ]
    assert check(positions, drift=3, previous=False) == (
        "previous_day_not_closed",
        "ledger_balance_drift",
        "difference_unexplained",
        "statement_missing",
    )


# --- Two people -------------------------------------------------------------------------------


def test_one_person_prepares_and_another_approves() -> None:
    prepared = prepare(DayClose(DAY), MAKER, ())
    assert (prepared.status, prepared.prepared_by) == (CloseStatus.PREPARED, MAKER)
    closed = approve(prepared, CHECKER, ())
    assert (closed.status, closed.prepared_by, closed.approved_by) == (
        CloseStatus.CLOSED,
        MAKER,
        CHECKER,
    )


def test_the_person_who_prepared_cannot_approve() -> None:
    prepared = prepare(DayClose(DAY), MAKER, ())
    with pytest.raises(CloseError) as refused:
        approve(prepared, MAKER, ())
    assert refused.value.code == "close_needs_second_person"


def test_nothing_is_prepared_while_something_blocks() -> None:
    with pytest.raises(CloseError) as refused:
        prepare(DayClose(DAY), MAKER, ("difference_unexplained", "statement_missing"))
    assert refused.value.code == "difference_unexplained"


def test_approval_checks_the_figures_again() -> None:
    prepared = prepare(DayClose(DAY), MAKER, ())
    with pytest.raises(CloseError) as refused:
        approve(prepared, CHECKER, ("difference_unexplained",))
    assert refused.value.code == "close_changed_since_prepared"
    reopened = send_back(prepared)
    assert reopened == DayClose(DAY)


@pytest.mark.parametrize("status", CloseStatus)
def test_each_step_happens_only_in_its_own_status(status: CloseStatus) -> None:
    close = replace(DayClose(DAY), status=status, prepared_by=MAKER)
    if status is not CloseStatus.OPEN:
        with pytest.raises(CloseError):
            prepare(close, MAKER, ())
    if status is not CloseStatus.PREPARED:
        with pytest.raises(CloseError):
            approve(close, CHECKER, ())
        with pytest.raises(CloseError):
            send_back(close)


def test_a_closed_day_cannot_be_changed_by_any_step() -> None:
    closed = approve(prepare(DayClose(DAY), MAKER, ()), CHECKER, ())
    with pytest.raises(CloseError):
        prepare(closed, MAKER, ())
    with pytest.raises(CloseError):
        approve(closed, "staff-third", ())
    with pytest.raises(CloseError):
        send_back(closed)


# --- Days in order ----------------------------------------------------------------------------


def test_days_close_in_order_with_none_skipped() -> None:
    first = date(2026, 11, 2)
    assert next_day_to_close(None, first) == first
    assert next_day_to_close(date(2026, 11, 13), first) == date(2026, 11, 14)  # a Saturday
    assert next_day_to_close(date(2026, 12, 31), first) == date(2027, 1, 1)


def test_nothing_is_posted_into_a_closed_day() -> None:
    assert may_post_on(DAY, None)
    assert not may_post_on(DAY, DAY)
    assert not may_post_on(date(2026, 11, 12), DAY)
    assert may_post_on(date(2026, 11, 14), DAY)
