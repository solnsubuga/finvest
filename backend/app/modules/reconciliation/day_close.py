"""Matching and the two-person day close. The rules are in `domain/`; migration 0041 repeats them.

Reconciliation keeps its own copy of the ledger movements on each pot, read through the
ledger's API. A statement line and a movement are paired by reference, or by a person with a
reason. Whatever is left unpaired on either side is an open exception. A day can be closed
only when, for every pot, the statement balance equals the ledger balance plus what the open
exceptions account for, and two different members of staff have signed.

Nothing here moves money. A difference is put right by a ledger entry made elsewhere, which
then shows up as a movement and is matched.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.ledger import closing as ledger_closing
from app.modules.ledger import service as ledger
from app.modules.ledger import statements as ledger_statements
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain import day_close as rules
from app.modules.reconciliation.domain.day_close import CloseError, CloseStatus, PotPosition
from app.modules.reconciliation.domain.matching import (
    LedgerMovement,
    ReconciliationException,
    match,
)
from app.modules.reconciliation.domain.pots import CASH_POTS, Pot
from app.modules.reconciliation.domain.statements import (
    Direction,
    StatementLine,
    StatementSource,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

# The ledger has no entries before this. Movements are read from here the first time.
_EPOCH = date(2000, 1, 1)


class MatchRefused(Exception):
    """The pair was not saved. `code` is safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DayCloseView:
    id: str
    business_date: date
    status: CloseStatus
    prepared_by: str
    decided_by: str | None
    positions: tuple[PotPosition, ...]


_LAST_CLOSED = text(
    "SELECT max(business_date) FROM reconciliation_day_closes WHERE status = 'closed'"
)
_INSERT_MOVEMENT = text(
    "INSERT INTO reconciliation_movements (entry_id, line_no, source, account_reference, "
    "effective_date, currency, amount_minor, reference, entry_type, recorded_at) VALUES "
    "(:entry_id, :line_no, :source, :account_reference, :effective_date, :currency, "
    ":amount_minor, :reference, :entry_type, :now) ON CONFLICT (entry_id, line_no) DO NOTHING"
)
_OPEN_LINES = text(
    "SELECT line.id, line.value_date, line.direction, line.amount_minor, line.reference, "
    "line.narrative, statement.currency "
    "FROM reconciliation_statement_lines AS line "
    "JOIN reconciliation_statements AS statement ON statement.id = line.statement_id "
    "WHERE statement.source = :source AND statement.account_reference = :account_reference "
    "AND statement.business_date <= :day AND NOT EXISTS (SELECT FROM reconciliation_matches "
    "AS matched WHERE matched.statement_line_id = line.id) ORDER BY line.id"
)
_OPEN_MOVEMENTS = text(
    "SELECT movement.entry_id, movement.line_no, movement.effective_date, movement.currency, "
    "movement.amount_minor, movement.reference FROM reconciliation_movements AS movement "
    "WHERE movement.source = :source AND movement.account_reference = :account_reference "
    "AND movement.effective_date <= :day AND NOT EXISTS (SELECT FROM reconciliation_matches "
    "AS matched WHERE matched.entry_id = movement.entry_id "
    "AND matched.line_no = movement.line_no) ORDER BY movement.entry_id, movement.line_no"
)
_INSERT_MATCH = text(
    "INSERT INTO reconciliation_matches (statement_line_id, entry_id, line_no, how, "
    "matched_by, reason, matched_at) VALUES (:statement_line_id, :entry_id, :line_no, :how, "
    ":matched_by, :reason, :now)"
)
_INSERT_CLOSE = text(
    "INSERT INTO reconciliation_day_closes (id, business_date, status, prepared_by, "
    "prepared_at) VALUES (:id, :business_date, 'prepared', :prepared_by, :now)"
)
_INSERT_POSITION = text(
    "INSERT INTO reconciliation_close_positions (close_id, source, account_reference, "
    "currency, statement_closing_minor, ledger_closing_minor, open_total_minor, open_count) "
    "VALUES (:close_id, :source, :account_reference, :currency, :statement_closing_minor, "
    ":ledger_closing_minor, :open_total_minor, :open_count)"
)
_LIVE_CLOSE = (
    "SELECT id, business_date, status, prepared_by, decided_by FROM reconciliation_day_closes "
    "WHERE business_date = :day AND status <> 'sent_back'"
)
_GET_CLOSE = text(_LIVE_CLOSE)
_LOCK_CLOSE = text(_LIVE_CLOSE + " FOR UPDATE")
_GET_POSITIONS = text(
    "SELECT source, account_reference, currency, statement_closing_minor, "
    "ledger_closing_minor, open_total_minor, open_count FROM reconciliation_close_positions "
    "WHERE close_id = :close_id ORDER BY source, account_reference"
)
_IMPORTED_BY = text(
    "SELECT EXISTS (SELECT FROM reconciliation_statements "
    "WHERE business_date = :day AND imported_by = :staff_id)"
)
_DECIDE = text(
    "UPDATE reconciliation_day_closes SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, reason = :reason WHERE id = :id"
)
_MATCH_REFUSALS = {
    "pk_reconciliation_matches": "line_already_matched",
    "uq_reconciliation_matches_movement": "movement_already_matched",
    "fk_reconciliation_matches_line": "match_not_found",
    "fk_reconciliation_matches_movement": "match_not_found",
    "ck_reconciliation_matches_same_money": "match_not_same_money",
}


async def last_closed(session: AsyncSession) -> date | None:
    """The last day two people signed off, or None. Staff and the worker only."""
    return (await session.execute(_LAST_CLOSED)).scalar_one_or_none()


async def refresh(
    session: AsyncSession,
    staff: Actor,
    day: date,
    clock: Clock,
    *,
    pots: Sequence[Pot] = CASH_POTS,
) -> None:
    """Bring in the ledger's movements on each pot up to `day` and pair what can be paired.

    Safe to run again: a movement is recorded once and a pair is made once.
    """
    _staff_only(staff)
    read_from = await _read_from(session)
    now = clock.now()
    for pot in pots:
        account = await ledger.open_account(session, pot.ledger_account, clock)
        if read_from <= day:
            moved = await ledger_statements.account_movements(
                session, account.id, date_from=read_from, date_to=day
            )
            for movement in moved:
                await session.execute(
                    _INSERT_MOVEMENT,
                    {
                        "entry_id": movement.entry_id,
                        "line_no": movement.line_no,
                        "source": pot.source,
                        "account_reference": pot.account_reference,
                        "effective_date": movement.effective_date,
                        "currency": movement.amount.currency.value,
                        "amount_minor": movement.amount.minor,
                        "reference": movement.source_id or "",
                        "entry_type": movement.entry_type,
                        "now": now,
                    },
                )
        lines, movements = await _open_items(session, pot, day)
        for pair in match(lines, movements).matches:
            entry_id, line_no = _movement_key(pair.movement)
            await session.execute(
                _INSERT_MATCH,
                {
                    "statement_line_id": pair.statement_line.fingerprint,
                    "entry_id": entry_id,
                    "line_no": line_no,
                    "how": "reference",
                    "matched_by": staff.id,
                    "reason": None,
                    "now": now,
                },
            )


async def open_exceptions(
    session: AsyncSession, pot: Pot, day: date
) -> tuple[ReconciliationException, ...]:
    """What is still unpaired on the pot up to `day`, grouped as the matching rules group it.

    In what comes back, a statement line's `fingerprint` is its line ID and a movement's
    `line_id` is "<entry ID>:<line number>", which is what `match_by_hand` takes.
    """
    lines, movements = await _open_items(session, pot, day)
    return match(lines, movements).exceptions


async def match_by_hand(
    session: AsyncSession,
    staff: Actor,
    *,
    statement_line_id: str,
    entry_id: str,
    line_no: int,
    reason: str,
    clock: Clock,
) -> None:
    """A person pairs a line with a movement that the references did not pair.

    The database still insists on the same pot, amount and direction. A reason is required.
    """
    _staff_only(staff)
    if not reason.strip():
        raise ApplicationError("validation_failed")
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_MATCH,
                {
                    "statement_line_id": statement_line_id,
                    "entry_id": entry_id,
                    "line_no": line_no,
                    "how": "staff",
                    "matched_by": staff.id,
                    "reason": reason.strip(),
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        name = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        if name in _MATCH_REFUSALS:
            raise MatchRefused(_MATCH_REFUSALS[name]) from None
        raise
    await _audit(
        session,
        staff,
        "reconciliation.matched_by_hand",
        "reconciliation_statement_line",
        statement_line_id,
        clock,
        reason=reason.strip(),
    )


async def positions(
    session: AsyncSession, day: date, clock: Clock, *, pots: Sequence[Pot] = CASH_POTS
) -> tuple[PotPosition, ...]:
    """Each pot at the end of `day`: statement, ledger, and what the open exceptions explain."""
    found: list[PotPosition] = []
    for pot in pots:
        account = await ledger.open_account(session, pot.ledger_account, clock)
        statement = await statements.get_statement(session, pot.source, pot.account_reference, day)
        exceptions = await open_exceptions(session, pot, day)
        total = Money.zero(pot.currency)
        for exception in exceptions:
            total = total + exception.difference(pot.currency)
        found.append(
            PotPosition(
                source=pot.source,
                account_reference=pot.account_reference,
                statement_closing=None if statement is None else statement.closing_balance,
                ledger_closing=await ledger_statements.balance_as_of(session, account.id, day),
                open_exceptions_total=total,
                open_exceptions_count=len(exceptions),
            )
        )
    return tuple(found)


async def prepare(
    session: AsyncSession,
    staff: Actor,
    day: date,
    clock: Clock,
    *,
    pots: Sequence[Pot] = CASH_POTS,
) -> DayCloseView:
    """The first signature. Raises `CloseError` with the first thing that blocks the close."""
    _staff_only(staff)
    assert staff.id is not None
    if day >= business_date(clock.now()):
        raise CloseError("day_not_ended")
    if (await session.execute(_GET_CLOSE, {"day": day})).one_or_none() is not None:
        raise CloseError("close_not_open")
    await refresh(session, staff, day, clock, pots=pots)
    found = await positions(session, day, clock, pots=pots)
    blocking = await _blockers(session, found, day, pots)
    rules.prepare(rules.DayClose(day), staff.id, blocking)

    close_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_CLOSE,
                {"id": close_id, "business_date": day, "prepared_by": staff.id, "now": clock.now()},
            )
            for position in found:
                assert position.statement_closing is not None  # a missing statement blocks
                await session.execute(
                    _INSERT_POSITION,
                    {
                        "close_id": close_id,
                        "source": position.source,
                        "account_reference": position.account_reference,
                        "currency": position.ledger_closing.currency.value,
                        "statement_closing_minor": position.statement_closing.minor,
                        "ledger_closing_minor": position.ledger_closing.minor,
                        "open_total_minor": position.open_exceptions_total.minor,
                        "open_count": position.open_exceptions_count,
                    },
                )
    except IntegrityError as error:
        name = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        if name == "uq_reconciliation_day_closes_live":
            raise CloseError("close_not_open") from None
        if name == "ck_reconciliation_day_closes_next_day":
            raise CloseError("previous_day_not_closed") from None
        raise
    await _audit(
        session,
        staff,
        "reconciliation.day_close_prepared",
        "reconciliation_day_close",
        close_id,
        clock,
        details={"business_date": day.isoformat()},
    )
    return DayCloseView(close_id, day, CloseStatus.PREPARED, staff.id, None, found)


async def approve(
    session: AsyncSession,
    staff: Actor,
    day: date,
    clock: Clock,
    *,
    pots: Sequence[Pot] = CASH_POTS,
) -> DayCloseView:
    """The second signature, by a different person. It closes the ledger through `day`.

    The ledger is closed first and the figures are read after it, so nothing can be posted
    into the day behind the check. If the figures are not the ones that were prepared, this
    raises `CloseError("close_changed_since_prepared")` and the ledger close is undone with
    it. The close is then sent back and prepared again.
    """
    _staff_only(staff)
    assert staff.id is not None
    row = (await session.execute(_LOCK_CLOSE, {"day": day})).one_or_none()
    if row is None:
        raise CloseError("close_not_prepared")
    prepared = rules.DayClose(day, CloseStatus(row.status), row.prepared_by, row.decided_by)
    rules.approve(prepared, staff.id, ())  # the person and the status, before anything is closed
    if await session.scalar(_IMPORTED_BY, {"day": day, "staff_id": staff.id}):
        raise CloseError("close_needs_someone_who_did_not_import")

    # One savepoint: if the figures have changed, the ledger close is undone with the rest,
    # whatever the caller does next.
    async with session.begin_nested():
        await ledger_closing.close_through(session, staff, day, clock)
        await refresh(session, staff, day, clock, pots=pots)
        found = await positions(session, day, clock, pots=pots)
        blocking = list(await _blockers(session, found, day, pots))
        if _by_pot(found) != _by_pot(await _saved_positions(session, row.id)):
            blocking.append("close_changed_since_prepared")
        rules.approve(prepared, staff.id, blocking)
        await session.execute(
            _DECIDE,
            {
                "id": row.id,
                "status": "closed",
                "decided_by": staff.id,
                "now": clock.now(),
                "reason": None,
            },
        )
    await _audit(
        session,
        staff,
        "reconciliation.day_closed",
        "reconciliation_day_close",
        row.id,
        clock,
        category="approval",
        reason="Second signature on the day close",
        details={"business_date": day.isoformat()},
    )
    return DayCloseView(row.id, day, CloseStatus.CLOSED, row.prepared_by, staff.id, found)


async def send_back(
    session: AsyncSession, staff: Actor, day: date, reason: str, clock: Clock
) -> None:
    """Decline a prepared close, or withdraw it, so it can be prepared again. Needs a reason."""
    _staff_only(staff)
    if not reason.strip():
        raise ApplicationError("validation_failed")
    row = (await session.execute(_LOCK_CLOSE, {"day": day})).one_or_none()
    if row is None:
        raise CloseError("close_not_prepared")
    rules.send_back(rules.DayClose(day, CloseStatus(row.status), row.prepared_by))
    await session.execute(
        _DECIDE,
        {
            "id": row.id,
            "status": "sent_back",
            "decided_by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
        },
    )
    await _audit(
        session,
        staff,
        "reconciliation.day_close_sent_back",
        "reconciliation_day_close",
        row.id,
        clock,
        category="approval",
        reason=reason.strip(),
    )


@dataclass(frozen=True, slots=True)
class PreparedClose:
    """A day someone prepared to close, waiting for a different person to approve it."""

    id: str
    business_date: date
    prepared_by: str
    prepared_at: datetime


_PREPARED = text(
    "SELECT id, business_date, prepared_by, prepared_at FROM reconciliation_day_closes "
    "WHERE status = 'prepared' ORDER BY business_date, id"
)


async def prepared_closes(session: AsyncSession) -> list[PreparedClose]:
    """Closes waiting for a second person, oldest day first."""
    rows = await session.execute(_PREPARED)
    return [
        PreparedClose(row.id, row.business_date, row.prepared_by, row.prepared_at)
        for row in rows.all()
    ]


async def get_close(session: AsyncSession, day: date) -> DayCloseView | None:
    """The prepared or closed close for `day`, with the figures that were signed."""
    row = (await session.execute(_GET_CLOSE, {"day": day})).one_or_none()
    if row is None:
        return None
    return DayCloseView(
        row.id,
        row.business_date,
        CloseStatus(row.status),
        row.prepared_by,
        row.decided_by,
        await _saved_positions(session, row.id),
    )


# --- Helpers ----------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


async def _read_from(session: AsyncSession) -> date:
    """Movements up to the last close are already recorded and can no longer change."""
    closed = await last_closed(session)
    return _EPOCH if closed is None else closed + timedelta(days=1)


async def _blockers(
    session: AsyncSession,
    found: Sequence[PotPosition],
    day: date,
    pots: Sequence[Pot],
) -> tuple[str, ...]:
    closed = await last_closed(session)
    drift = await ledger.find_balance_drift(session)
    return rules.blockers(
        found,
        balance_drift_accounts=len(drift),
        # The first close ever has no day before it. The database holds the order after that.
        previous_day_closed=closed is None or rules.next_day_to_close(closed, day) == day,
        required=[pot.key for pot in pots],
    )


async def _open_items(
    session: AsyncSession, pot: Pot, day: date
) -> tuple[list[StatementLine], list[LedgerMovement]]:
    where = {"source": pot.source, "account_reference": pot.account_reference, "day": day}
    lines = [
        StatementLine(
            value_date=row.value_date,
            direction=Direction(row.direction),
            amount=Money(int(row.amount_minor), Currency(row.currency)),
            reference=row.reference,
            narrative=row.narrative,
            # The line's own ID stands in for the fingerprint: unique across every statement.
            fingerprint=row.id,
        )
        for row in (await session.execute(_OPEN_LINES, where)).all()
    ]
    movements = [
        LedgerMovement(
            line_id=f"{row.entry_id}:{row.line_no}",
            effective_date=row.effective_date,
            # A debit to an asset account is money arriving in the pot.
            direction=Direction.IN if row.amount_minor > 0 else Direction.OUT,
            amount=Money(abs(int(row.amount_minor)), Currency(row.currency)),
            reference=row.reference,
        )
        for row in (await session.execute(_OPEN_MOVEMENTS, where)).all()
    ]
    return lines, movements


def _by_pot(found: Sequence[PotPosition]) -> list[PotPosition]:
    return sorted(found, key=lambda position: (position.source, position.account_reference))


def _movement_key(movement: LedgerMovement) -> tuple[str, int]:
    entry_id, _, line_no = movement.line_id.rpartition(":")
    return entry_id, int(line_no)


async def _saved_positions(session: AsyncSession, close_id: str) -> tuple[PotPosition, ...]:
    rows = (await session.execute(_GET_POSITIONS, {"close_id": close_id})).all()
    return tuple(
        PotPosition(
            source=StatementSource(row.source),
            account_reference=row.account_reference,
            statement_closing=Money(int(row.statement_closing_minor), Currency(row.currency)),
            ledger_closing=Money(int(row.ledger_closing_minor), Currency(row.currency)),
            open_exceptions_total=Money(int(row.open_total_minor), Currency(row.currency)),
            open_exceptions_count=row.open_count,
        )
        for row in rows
    )


async def _audit(
    session: AsyncSession,
    staff: Actor,
    action: str,
    resource_type: str,
    resource_id: str,
    clock: Clock,
    *,
    category: AuditCategory = "change",
    reason: str | None = None,
    details: dict[str, str] | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor=AuditActor(staff.kind, staff.id),
            reason=reason,
            details=details or {},
        ),
        clock,
    )
