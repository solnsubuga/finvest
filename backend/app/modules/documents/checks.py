"""Statement checks: the code on each statement, and what the website shows for it.

The worker records a statement's code in the transaction that builds it (`record`). Anyone can
then enter the code at finvestug.com/check; the API answers through `check`, which reads one
exact code and nothing else. See docs/documents.md, "Checking a statement".
"""

import json
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.domain.checks import normalised
from app.modules.documents.domain.statements import Statement
from app.shared.money import Currency, Money

_RECORD = text(
    "INSERT INTO document_checks (document_id, code, account_ending, closing, created_at) "
    "VALUES (:id, :code, :ending, CAST(:closing AS jsonb), :now)"
)
_CHECK = text(
    "SELECT kind, period_start, period_end, issued_at, account_ending, closing "
    "FROM documents_check(:code)"
)


@dataclass(frozen=True, slots=True)
class Checked:
    """What the check page shows for a genuine statement."""

    monthly: bool
    period_start: date
    period_end: date
    issued_at: datetime
    account_ending: str
    closing: tuple[Money, ...]


async def record(
    session: AsyncSession, document_id: str, statement: Statement, now: datetime
) -> None:
    """Keep the code printed on a statement just drawn, with what the check page shows."""
    if statement.check_code is None:
        raise ValueError("The statement has no check code")
    closing = [
        {"currency": summary.currency.value, "minor": summary.closing.minor}
        for summary in statement.summaries
    ]
    await session.execute(
        _RECORD,
        {
            "id": document_id,
            "code": statement.check_code,
            "ending": statement.account_number[-4:],
            "closing": json.dumps(closing),
            "now": now,
        },
    )


async def check(session: AsyncSession, typed: str) -> Checked | None:
    """The statement a code belongs to, or None when no finished statement has it."""
    code = normalised(typed)
    if code is None:
        return None
    row = (await session.execute(_CHECK, {"code": code})).one_or_none()
    if row is None:
        return None
    return Checked(
        monthly=row.kind == "monthly_statement",
        period_start=row.period_start,
        period_end=row.period_end,
        issued_at=row.issued_at,
        account_ending=row.account_ending,
        closing=tuple(
            Money(int(each["minor"]), Currency(each["currency"])) for each in row.closing
        ),
    )
