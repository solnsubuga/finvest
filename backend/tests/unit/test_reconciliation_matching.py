import itertools
import random
from datetime import date, timedelta

import pytest

from app.modules.reconciliation.domain.matching import (
    ExceptionKind,
    LedgerMovement,
    MatchResult,
    match,
    suggestions,
)
from app.modules.reconciliation.domain.statements import Direction, StatementLine
from app.shared.money import Currency, Money

DAY = date(2026, 11, 13)
IN, OUT = Direction.IN, Direction.OUT
_counter = itertools.count(1)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def line(reference: str, amount: int, direction: Direction = IN, day: date = DAY) -> StatementLine:
    return StatementLine(day, direction, ugx(amount), reference, "", f"fp{next(_counter):05d}")


def move(reference: str, amount: int, direction: Direction = IN, day: date = DAY) -> LedgerMovement:
    return LedgerMovement(f"ln{next(_counter):05d}", day, direction, ugx(amount), reference)


def kinds(result: MatchResult) -> list[ExceptionKind]:
    return [exception.kind for exception in result.exceptions]


def test_same_reference_direction_and_amount_match() -> None:
    result = match(
        [line("DEP1", 100), line("PAY1", 40, OUT)], [move("PAY1", 40, OUT), move("DEP1", 100)]
    )
    assert [(m.statement_line.reference, m.movement.reference) for m in result.matches] == [
        ("DEP1", "DEP1"),
        ("PAY1", "PAY1"),
    ]
    assert result.exceptions == ()


@pytest.mark.parametrize(
    ("statement", "ledger", "kind", "difference"),
    [
        (("R", 100, IN), ("R", 90, IN), ExceptionKind.AMOUNT_DIFFERS, 10),
        (("R", 100, OUT), ("R", 90, OUT), ExceptionKind.AMOUNT_DIFFERS, -10),
        (("R", 100, IN), ("R", 100, OUT), ExceptionKind.DIRECTION_DIFFERS, 200),
        (("R", 100, OUT), ("R", 100, IN), ExceptionKind.DIRECTION_DIFFERS, -200),
    ],
)
def test_a_shared_reference_that_disagrees_is_an_exception(
    statement: tuple[str, int, Direction],
    ledger: tuple[str, int, Direction],
    kind: ExceptionKind,
    difference: int,
) -> None:
    result = match([line(*statement)], [move(*ledger)])
    assert result.matches == ()
    assert kinds(result) == [kind]
    assert result.exceptions[0].difference(Currency.UGX) == ugx(difference)


@pytest.mark.parametrize(("direction", "difference"), [(IN, 100), (OUT, -100)])
def test_a_statement_line_with_no_ledger_movement(direction: Direction, difference: int) -> None:
    result = match([line("DEP7", 100, direction)], [])
    assert kinds(result) == [ExceptionKind.MISSING_IN_LEDGER]
    assert result.exceptions[0].difference(Currency.UGX) == ugx(difference)


@pytest.mark.parametrize(("direction", "difference"), [(IN, -100), (OUT, 100)])
def test_a_ledger_movement_with_no_statement_line(direction: Direction, difference: int) -> None:
    result = match([], [move("PAY7", 100, direction)])
    assert kinds(result) == [ExceptionKind.MISSING_IN_STATEMENT]
    assert result.exceptions[0].difference(Currency.UGX) == ugx(difference)


def test_nothing_is_matched_on_amount_and_date_alone() -> None:
    result = match([line("", 100)], [move("DEP1", 100)])
    assert result.matches == ()
    assert sorted(kinds(result)) == [
        ExceptionKind.MISSING_IN_LEDGER,
        ExceptionKind.MISSING_IN_STATEMENT,
    ]


def test_lines_without_a_reference_never_pair_with_each_other() -> None:
    result = match([line("", 100), line("", 100)], [move("", 100)])
    assert result.matches == ()
    assert len(result.exceptions) == 3


@pytest.mark.parametrize(
    ("lines", "moves"),
    [(2, 1), (1, 2), (2, 2), (2, 0), (0, 2), (3, 1)],
)
def test_a_reference_seen_twice_on_either_side_goes_to_a_person(lines: int, moves: int) -> None:
    result = match([line("R", 100) for _ in range(lines)], [move("R", 100) for _ in range(moves)])
    assert result.matches == ()
    assert kinds(result) == [ExceptionKind.DUPLICATE_REFERENCE]
    exception = result.exceptions[0]
    assert (len(exception.statement_lines), len(exception.movements)) == (lines, moves)
    assert exception.difference(Currency.UGX) == ugx(100 * (lines - moves))


def test_references_are_compared_exactly() -> None:
    result = match([line("dep1", 100), line("DEP1 ", 100)], [move("DEP1", 100)])
    assert result.matches == ()
    assert len(result.exceptions) == 3


def random_case(rng: random.Random) -> tuple[list[StatementLine], list[LedgerMovement]]:
    references = ["", "A", "B", "C", "D", "E"]
    lines = [
        line(rng.choice(references), rng.choice([50, 100]), rng.choice([IN, OUT]))
        for _ in range(rng.randrange(0, 8))
    ]
    moves = [
        move(rng.choice(references), rng.choice([50, 100]), rng.choice([IN, OUT]))
        for _ in range(rng.randrange(0, 8))
    ]
    return lines, moves


@pytest.mark.parametrize("seed", range(200))
def test_everything_given_in_comes_out_exactly_once_and_the_difference_ties(seed: int) -> None:
    rng = random.Random(seed)
    lines, moves = random_case(rng)
    result = match(lines, moves)

    out_lines = [m.statement_line for m in result.matches]
    out_moves = [m.movement for m in result.matches]
    for exception in result.exceptions:
        out_lines.extend(exception.statement_lines)
        out_moves.extend(exception.movements)
    assert sorted(item.fingerprint for item in out_lines) == sorted(i.fingerprint for i in lines)
    assert sorted(item.line_id for item in out_moves) == sorted(i.line_id for i in moves)

    # The exceptions account for the whole gap between the statement and the books.
    statement_total = sum(item.signed_amount.minor for item in lines)
    ledger_total = sum(item.signed_amount.minor for item in moves)
    explained = sum(e.difference(Currency.UGX).minor for e in result.exceptions)
    assert statement_total - ledger_total == explained
    for pair in result.matches:
        assert pair.statement_line.signed_amount == pair.movement.signed_amount
        assert pair.statement_line.reference == pair.movement.reference != ""

    # The order things arrive in changes nothing.
    rng.shuffle(lines)
    rng.shuffle(moves)
    assert match(lines, moves) == result


def test_suggestions_are_same_way_same_amount_and_close_in_time() -> None:
    target = line("", 100, IN, DAY)
    near = move("DEP1", 100, IN, DAY - timedelta(days=1))
    same_day = move("DEP2", 100, IN, DAY)
    edge = move("DEP3", 100, IN, DAY + timedelta(days=3))
    too_far = move("DEP4", 100, IN, DAY + timedelta(days=4))
    wrong_way = move("PAY1", 100, OUT, DAY)
    wrong_amount = move("DEP5", 101, IN, DAY)
    dollars = LedgerMovement("lnUSD", DAY, IN, Money(100, Currency.USD), "DEP6")
    offered = suggestions(target, [too_far, edge, wrong_way, near, wrong_amount, same_day, dollars])
    assert offered == (same_day, near, edge)
