"""Importing statements and reading their lines. The rules are in `domain/statements.py`.

A statement is parsed and checked by the domain before it reaches this module, and the
database checks it again: one statement per pot and day, each day joined to the last, and the
lines adding up to the closing balance (migration 0040). Importing never moves money.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.reconciliation.domain.statements import (
    Direction,
    Statement,
    StatementSource,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

MAX_FOUND = 200


class StatementRefused(Exception):
    """The statement was not imported, and nothing of it was saved. `code` is safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ImportedStatement:
    id: str
    source: StatementSource
    account_reference: str
    business_date: date
    opening_balance: Money
    closing_balance: Money
    line_count: int


@dataclass(frozen=True, slots=True)
class StatementLineView:
    """One imported line. `matched` is filled in once matching is stored; False until then."""

    id: str
    source: StatementSource
    account_reference: str
    value_date: date
    amount: Money
    direction: Direction
    reference: str
    counterparty_hint: str
    matched: bool = False


_INSERT_STATEMENT = text(
    "INSERT INTO reconciliation_statements (id, source, account_reference, business_date, "
    "currency, opening_minor, closing_minor, line_count, file_sha256, imported_by, imported_at) "
    "VALUES (:id, :source, :account_reference, :business_date, :currency, :opening_minor, "
    ":closing_minor, :line_count, :file_sha256, :imported_by, :imported_at)"
)
_INSERT_LINE = text(
    "INSERT INTO reconciliation_statement_lines (id, statement_id, line_no, value_date, "
    "direction, amount_minor, reference, narrative, fingerprint) VALUES (:id, :statement_id, "
    ":line_no, :value_date, :direction, :amount_minor, :reference, :narrative, :fingerprint)"
)
_GET = text(
    "SELECT id, source, account_reference, business_date, currency, opening_minor, "
    "closing_minor, line_count FROM reconciliation_statements "
    "WHERE source = :source AND account_reference = :account_reference "
    "AND business_date = :business_date"
)
_FIND = text(
    "SELECT line.id, statement.source, statement.account_reference, statement.currency, "
    "line.value_date, line.direction, line.amount_minor, line.reference, line.narrative "
    "FROM reconciliation_statement_lines AS line "
    "JOIN reconciliation_statements AS statement ON statement.id = line.statement_id "
    "WHERE statement.source = :source AND line.value_date BETWEEN :date_from AND :date_to "
    "AND (CAST(:reference AS text) IS NULL OR line.reference = :reference) "
    "AND (CAST(:amount_minor AS bigint) IS NULL "
    "     OR (line.amount_minor = :amount_minor AND statement.currency = :currency)) "
    "ORDER BY line.value_date, line.id LIMIT :limit"
)
_REFUSALS = {
    "uq_reconciliation_statements_pot_day": "statement_already_imported",
    "ck_reconciliation_statements_next_day": "statement_day_out_of_order",
    "ck_reconciliation_statements_continuity": "statement_opening_balance_differs",
    "ck_reconciliation_statements_adds_up": "statement_does_not_add_up",
}


def file_digest(content: bytes) -> str:
    """The SHA-256 of the uploaded file, kept so the import can be tied to what was uploaded."""
    return hashlib.sha256(content).hexdigest()


async def import_statement(
    session: AsyncSession, staff: Actor, statement: Statement, file_sha256: str, clock: Clock
) -> ImportedStatement:
    """Save a parsed statement in the staff member's transaction, or raise `StatementRefused`.

    Only a day that has ended can be imported: today's statement is not final yet.
    """
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    now = clock.now()
    if statement.statement_date >= business_date(now):
        raise StatementRefused("statement_day_not_over")
    statement_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_STATEMENT,
                {
                    "id": statement_id,
                    "source": statement.source,
                    "account_reference": statement.account_reference,
                    "business_date": statement.statement_date,
                    "currency": statement.currency,
                    "opening_minor": statement.opening_balance.minor,
                    "closing_minor": statement.closing_balance.minor,
                    "line_count": len(statement.lines),
                    "file_sha256": file_sha256,
                    "imported_by": staff.id,
                    "imported_at": now,
                },
            )
            for line_no, line in enumerate(statement.lines, start=1):
                await session.execute(
                    _INSERT_LINE,
                    {
                        "id": push_id(),
                        "statement_id": statement_id,
                        "line_no": line_no,
                        "value_date": line.value_date,
                        "direction": line.direction,
                        "amount_minor": line.amount.minor,
                        "reference": line.reference,
                        "narrative": line.narrative,
                        "fingerprint": line.fingerprint,
                    },
                )
            # Run the deferred check now, so a refusal is raised here and not at commit.
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    except IntegrityError as error:
        name = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        if name in _REFUSALS:
            raise StatementRefused(_REFUSALS[name]) from None
        raise
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="reconciliation.statement_imported",
            resource_type="reconciliation_statement",
            resource_id=statement_id,
            actor=AuditActor(staff.kind, staff.id),
            details={
                "source": statement.source.value,
                "account_reference": statement.account_reference,
                "business_date": statement.statement_date.isoformat(),
                "line_count": len(statement.lines),
            },
        ),
        clock,
    )
    return ImportedStatement(
        statement_id,
        statement.source,
        statement.account_reference,
        statement.statement_date,
        statement.opening_balance,
        statement.closing_balance,
        len(statement.lines),
    )


async def get_statement(
    session: AsyncSession, source: StatementSource, account_reference: str, on: date
) -> ImportedStatement | None:
    row = (
        await session.execute(
            _GET, {"source": source, "account_reference": account_reference, "business_date": on}
        )
    ).one_or_none()
    if row is None:
        return None
    currency = Currency(row.currency)
    return ImportedStatement(
        row.id,
        StatementSource(row.source),
        row.account_reference,
        row.business_date,
        Money(int(row.opening_minor), currency),
        Money(int(row.closing_minor), currency),
        row.line_count,
    )


async def find_statement_lines(
    session: AsyncSession,
    *,
    source: StatementSource,
    date_from: date,
    date_to: date,
    reference: str | None = None,
    amount: Money | None = None,
) -> tuple[StatementLineView, ...]:
    """Imported lines for one source in a date range, optionally by reference and amount.

    For a module that needs to see whether money it expects has reached a statement, such as a
    bank transfer waiting for its receipt to be matched. Staff transactions and the worker only.
    """
    rows = await session.execute(
        _FIND,
        {
            "source": source,
            "date_from": date_from,
            "date_to": date_to,
            "reference": reference,
            "amount_minor": None if amount is None else amount.minor,
            "currency": None if amount is None else amount.currency.value,
            "limit": MAX_FOUND,
        },
    )
    return tuple(_line(row) for row in rows.all())


def _line(row: Row[Any]) -> StatementLineView:
    return StatementLineView(
        id=row.id,
        source=StatementSource(row.source),
        account_reference=row.account_reference,
        value_date=row.value_date,
        amount=Money(int(row.amount_minor), Currency(row.currency)),
        direction=Direction(row.direction),
        reference=row.reference,
        counterparty_hint=row.narrative,
    )
