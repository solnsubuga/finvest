"""The Finance screen's figures and the accounting export, read from the ledger.

Everything here reads the ledger through `app.modules.ledger.statements` and `closing`, and
moves no money. The one write is the record of each export, in `finance_journal_exports`, made
in the exporting person's own transaction together with its audit event.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.finance import code_maps
from app.modules.finance.domain.books import CodeLine, IncomeAndCosts, income_and_costs
from app.modules.finance.domain.formats import Format, render, system_of
from app.modules.finance.domain.journal import (
    JournalLine,
    balanced,
    check_days,
    debit_total,
)
from app.modules.ledger import closing, statements
from app.shared.clock import Clock
from app.shared.money import Currency
from app.shared.push_ids import push_id

RECENT_EXPORTS = 5


class JournalDoesNotBalance(RuntimeError):
    """A day's debits and credits differ. The ledger refuses such entries, so this is a fault."""


@dataclass(frozen=True, slots=True)
class Books:
    currency: Currency
    date_from: date
    date_to: date
    lines: tuple[CodeLine, ...]
    income: IncomeAndCosts


@dataclass(frozen=True, slots=True)
class ExportRecord:
    id: str
    currency: Currency
    date_from: date
    date_to: date
    lines: int
    debit_total_minor: int
    exported_by: str
    exported_at: datetime
    format: Format = Format.CSV
    code_map_id: str | None = None


@dataclass(frozen=True, slots=True)
class Journal:
    record: ExportRecord
    content: str


_SELECT_EXPORTS = text(
    "SELECT id, currency, date_from, date_to, lines, debit_total_minor, exported_by, exported_at, "
    "format, code_map_id "
    "FROM finance_journal_exports WHERE currency = :currency "
    "ORDER BY exported_at DESC, id DESC LIMIT :limit"
)
_SELECT_SENT_THROUGH = text(
    "SELECT max(date_to) AS sent_through FROM finance_journal_exports WHERE currency = :currency"
)
_INSERT_EXPORT = text(
    "INSERT INTO finance_journal_exports (id, currency, date_from, date_to, lines, "
    "debit_total_minor, exported_by, exported_at, format, code_map_id) VALUES (:id, :currency, "
    ":date_from, :date_to, :lines, :debit_total_minor, :exported_by, :exported_at, :format, "
    ":code_map_id)"
)


async def books(
    session: AsyncSession, currency: Currency, *, date_from: date, date_to: date
) -> Books:
    """Each account code over the days, and Finvest's own income and costs among them."""
    found = await statements.totals_by_code(session, currency, date_from=date_from, date_to=date_to)
    lines = tuple(
        CodeLine(t.code, t.kind, t.name, t.opening, t.debits, t.credits, t.closing) for t in found
    )
    return Books(currency, date_from, date_to, lines, income_and_costs(currency, list(lines)))


async def closed_through(session: AsyncSession) -> date | None:
    return await closing.closed_through(session)


async def sent_through(session: AsyncSession, currency: Currency) -> date | None:
    """The last day any export of this currency covered, or None if nothing was exported."""
    row = (await session.execute(_SELECT_SENT_THROUGH, {"currency": currency.value})).one()
    found: date | None = row.sent_through
    return found


async def recent_exports(
    session: AsyncSession, currency: Currency, limit: int = RECENT_EXPORTS
) -> list[ExportRecord]:
    rows = await session.execute(_SELECT_EXPORTS, {"currency": currency.value, "limit": limit})
    return [
        ExportRecord(
            id=row.id,
            currency=Currency(row.currency),
            date_from=row.date_from,
            date_to=row.date_to,
            lines=int(row.lines),
            debit_total_minor=int(row.debit_total_minor),
            exported_by=row.exported_by,
            exported_at=row.exported_at,
            format=Format(row.format),
            code_map_id=row.code_map_id,
        )
        for row in rows.all()
    ]


async def export_journal(
    session: AsyncSession,
    exporter_id: str,
    currency: Currency,
    *,
    date_from: date,
    date_to: date,
    clock: Clock,
    format_: Format = Format.CSV,
) -> Journal:
    """The daily journals from `date_from` to `date_to`, all closed days, as one file in
    `format_`: Finvest's generic CSV or an accounting system's journal import.

    Raises ExportRefused for days that are not all closed, run backwards or are too many, and
    for a system's format when the currency is not shillings or a code is not in the system's
    approved code map (CodesNotMapped names them).
    Records the export and an audit event in the caller's transaction, which must be the
    exporting staff member's own.
    """
    check_days(date_from, date_to, await closing.closed_through(session))
    found = await statements.day_totals(session, currency, date_from=date_from, date_to=date_to)
    lines = [
        JournalLine(t.effective_date, t.code, t.name, t.kind, t.debits, t.credits) for t in found
    ]
    if not balanced(lines):
        raise JournalDoesNotBalance("A day's debits and credits differ")
    system = system_of(format_)
    in_use = await code_maps.active_map(session, system) if system is not None else None
    content = render(format_, currency, lines, in_use.as_code_map() if in_use else None)
    record = ExportRecord(
        id=push_id(clock),
        currency=currency,
        date_from=date_from,
        date_to=date_to,
        lines=len(lines),
        debit_total_minor=debit_total(lines),
        exported_by=exporter_id,
        exported_at=clock.now(),
        format=format_,
        code_map_id=in_use.id if in_use else None,
    )
    await session.execute(
        _INSERT_EXPORT,
        {
            "id": record.id,
            "currency": currency.value,
            "date_from": date_from,
            "date_to": date_to,
            "lines": record.lines,
            "debit_total_minor": record.debit_total_minor,
            "exported_by": record.exported_by,
            "exported_at": record.exported_at,
            "format": format_.value,
            "code_map_id": record.code_map_id,
        },
    )
    await audit.record(
        session,
        AuditEvent(
            category="access",
            action="finance.journal_exported",
            resource_type="finance_journal_export",
            resource_id=record.id,
            actor=AuditActor.staff(exporter_id),
            details={
                "currency": currency.value,
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "lines": record.lines,
                "format": format_.value,
            },
        ),
        clock,
    )
    return Journal(record, content)
