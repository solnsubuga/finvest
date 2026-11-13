"""Match statement lines to ledger movements on one account. Pure: no I/O.

Matching is deliberately strict. A statement line and a ledger movement match only when they
carry the same reference, go the same way and are for the same amount. Nothing is matched on
amount and date alone: those are offered to staff as suggestions, and a person decides.

Every line and every movement given in comes out exactly once, either in a match or in an
exception. Nothing is dropped.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.reconciliation.domain.statements import Direction, StatementLine
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class LedgerMovement:
    """One ledger line on the asset account being reconciled, seen from the account's side."""

    line_id: str
    effective_date: date
    direction: Direction  # IN is a debit to the asset account, OUT is a credit
    amount: Money  # always positive
    # The reference the provider was given for this movement: the deposit or payout ID.
    reference: str

    @property
    def signed_amount(self) -> Money:
        return self.direction.signed(self.amount)


class ExceptionKind(StrEnum):
    # On the statement, not in the books: an unmatched deposit, or a payout we thought failed.
    MISSING_IN_LEDGER = "missing_in_ledger"
    # In the books, not on the statement: not yet settled, or it never happened.
    MISSING_IN_STATEMENT = "missing_in_statement"
    # Same reference and direction, different amount.
    AMOUNT_DIFFERS = "amount_differs"
    # Same reference, opposite directions.
    DIRECTION_DIFFERS = "direction_differs"
    # The same reference more than once on one side. A person must look.
    DUPLICATE_REFERENCE = "duplicate_reference"


@dataclass(frozen=True, slots=True)
class Match:
    statement_line: StatementLine
    movement: LedgerMovement


@dataclass(frozen=True, slots=True)
class ReconciliationException:
    kind: ExceptionKind
    statement_lines: tuple[StatementLine, ...] = ()
    movements: tuple[LedgerMovement, ...] = ()

    def difference(self, currency: Currency) -> Money:
        """How much this exception puts the statement above the books (negative if below)."""
        total = Money.zero(currency)
        for line in self.statement_lines:
            total = total + line.signed_amount
        for movement in self.movements:
            total = total - movement.signed_amount
        return total


@dataclass(frozen=True, slots=True)
class MatchResult:
    matches: tuple[Match, ...]
    exceptions: tuple[ReconciliationException, ...]


def match(
    statement_lines: Sequence[StatementLine], movements: Sequence[LedgerMovement]
) -> MatchResult:
    """Pair lines with movements by reference. The result does not depend on input order."""
    lines_by_reference: dict[str, list[StatementLine]] = defaultdict(list)
    movements_by_reference: dict[str, list[LedgerMovement]] = defaultdict(list)
    for line in statement_lines:
        lines_by_reference[line.reference].append(line)
    for movement in movements:
        movements_by_reference[movement.reference].append(movement)

    matches: list[Match] = []
    exceptions: list[ReconciliationException] = []
    for reference in sorted(lines_by_reference.keys() | movements_by_reference.keys()):
        lines = sorted(lines_by_reference.get(reference, ()), key=lambda item: item.fingerprint)
        moves = sorted(movements_by_reference.get(reference, ()), key=lambda item: item.line_id)
        if not reference:
            # No reference means nothing to match on. Each one stands alone for a person.
            exceptions.extend(_alone(lines, moves))
        elif len(lines) == 1 and len(moves) == 1:
            line, movement = lines[0], moves[0]
            kind = _compare(line, movement)
            if kind is None:
                matches.append(Match(line, movement))
            else:
                exceptions.append(ReconciliationException(kind, (line,), (movement,)))
        elif len(lines) > 1 or len(moves) > 1:
            exceptions.append(
                ReconciliationException(
                    ExceptionKind.DUPLICATE_REFERENCE, tuple(lines), tuple(moves)
                )
            )
        else:
            exceptions.extend(_alone(lines, moves))
    return MatchResult(tuple(matches), tuple(exceptions))


def _compare(line: StatementLine, movement: LedgerMovement) -> ExceptionKind | None:
    if line.direction is not movement.direction:
        return ExceptionKind.DIRECTION_DIFFERS
    if line.amount != movement.amount:
        return ExceptionKind.AMOUNT_DIFFERS
    return None


def _alone(
    lines: Iterable[StatementLine], moves: Iterable[LedgerMovement]
) -> list[ReconciliationException]:
    return [
        *(ReconciliationException(ExceptionKind.MISSING_IN_LEDGER, (line,)) for line in lines),
        *(
            ReconciliationException(ExceptionKind.MISSING_IN_STATEMENT, (), (movement,))
            for movement in moves
        ),
    ]


def suggestions(
    line: StatementLine, unmatched_movements: Iterable[LedgerMovement], *, within_days: int = 3
) -> tuple[LedgerMovement, ...]:
    """Movements a person might pair with a line that has no usable reference.

    Same direction, same amount, and dated within a few days. Offered to staff, never applied.
    """
    return tuple(
        sorted(
            (
                movement
                for movement in unmatched_movements
                if movement.direction is line.direction
                and movement.amount.currency is line.amount.currency
                and movement.amount == line.amount
                and abs((movement.effective_date - line.value_date).days) <= within_days
            ),
            key=lambda item: (abs((item.effective_date - line.value_date).days), item.line_id),
        )
    )
