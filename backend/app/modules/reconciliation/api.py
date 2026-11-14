"""Staff routes for reconciliation: statements, matching and the two-person day close.

Reading needs `reconciliation.view`. Importing a statement, pairing by hand, refreshing and both
signatures on a day close need `reconciliation.change`. Approving a day close also needs the
second step in the last few minutes. Nothing here moves money: a difference is put right by an
entry made by the owning module, which then shows up as a movement and is matched.

A statement line's narrative carries names and phone numbers, so it is shown to staff only as
`counterparty_hint` and never logged.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.reconciliation import day_close, statements
from app.modules.reconciliation.domain.day_close import CloseError, PotPosition
from app.modules.reconciliation.domain.formats import FORMATS
from app.modules.reconciliation.domain.matching import (
    ExceptionKind,
    LedgerMovement,
    ReconciliationException,
    suggestions,
)
from app.modules.reconciliation.domain.pots import CASH_POTS, Pot
from app.modules.reconciliation.domain.statements import (
    InvalidStatement,
    StatementLine,
    StatementSource,
    parse_amount,
    parse_csv,
)
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Money

MAX_STATEMENT_BYTES = 2 * 1024 * 1024
_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

staff_router = APIRouter(
    prefix="/staff/reconciliation", tags=["staff reconciliation"], responses=_REFUSALS
)
routers = [staff_router]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
Staff = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
# Importing, pairing and signing confirm money, so they need more than seeing.
Changer = Annotated[StaffSession, Depends(require_permission("reconciliation.change"))]
# The second signature needs a passkey or app code from the last few minutes.
Approver = Annotated[StaffSession, Depends(require_confirmed("reconciliation.change"))]

Source = Literal["iotec", "bank"]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class MoneyOut(Contract):
    minor: int
    currency: Literal["UGX", "USD"]


def _money(value: Money) -> MoneyOut:
    return MoneyOut(minor=value.minor, currency=value.currency.value)


class PotOut(Contract):
    """A place real money sits, with its last imported statement day."""

    source: Source
    account_reference: str
    name: str
    currency: Literal["UGX", "USD"]
    last_statement_date: date | None
    last_closing: MoneyOut | None


class StatementOut(Contract):
    id: PushId
    source: Source
    account_reference: str
    business_date: date
    opening: MoneyOut
    closing: MoneyOut
    line_count: int


class LineOut(Contract):
    """A statement line still unpaired. `counterparty_hint` is the bank's narrative."""

    id: PushId
    value_date: date
    direction: Literal["in", "out"]
    amount: MoneyOut
    reference: str
    counterparty_hint: str
    # Movements with the same direction and amount within three days, for a person to pick.
    suggested_movements: list[str]


class MovementOut(Contract):
    """A ledger movement on the pot still unpaired. `id` is "<entry ID>:<line number>"."""

    id: str
    entry_id: PushId
    line_no: int
    effective_date: date
    direction: Literal["in", "out"]
    amount: MoneyOut
    reference: str
    entry_type: str


class ExceptionOut(Contract):
    kind: Literal[
        "missing_in_ledger",
        "missing_in_statement",
        "amount_differs",
        "direction_differs",
        "duplicate_reference",
    ]
    # How far this puts the statement above the books; negative if below.
    difference: MoneyOut
    statement_lines: list[LineOut]
    movements: list[MovementOut]


class PositionOut(Contract):
    source: Source
    account_reference: str
    name: str
    statement_closing: MoneyOut | None
    ledger_closing: MoneyOut
    open_exceptions_total: MoneyOut
    open_exceptions_count: int
    # What no open exception accounts for. Zero to close; None until the statement is in.
    unexplained: MoneyOut | None
    exceptions: list[ExceptionOut]


class CloseOut(Contract):
    id: PushId
    status: Literal["prepared", "closed"]
    prepared_by: PushId
    decided_by: PushId | None


class DayOut(Contract):
    """One business day. While it is open, the figures are worked out now; once prepared or
    closed, `close` holds who signed. `blockers` is every reason it cannot be prepared yet."""

    business_date: date
    last_closed: date | None
    close: CloseOut | None
    blockers: list[str]
    positions: list[PositionOut]


class MatchIn(Contract):
    statement_line_id: PushId
    entry_id: PushId
    line_no: Annotated[int, Field(ge=1)]
    reason: Reason


class ReasonIn(Contract):
    reason: Reason


def _refused(
    status: Literal["conflict", "validation_failed"], code: str, *path: str | int
) -> ApplicationError:
    return ApplicationError(status, fields=[FieldProblem(field_path=["body", *path], code=code)])


def _source(source: StatementSource) -> Source:
    if source is StatementSource.IOTEC:
        return "iotec"
    if source is StatementSource.BANK:
        return "bank"
    raise ValueError("Only cash pots are reconciled here")


def _pot(source: str, account_reference: str) -> Pot:
    for pot in CASH_POTS:
        if pot.key == (source, account_reference):
            return pot
    raise _refused("validation_failed", "pot_unknown", "account_reference")


@staff_router.get("/pots")
async def pots(staff: Staff, database: Db) -> list[PotOut]:
    """Every pot closed each day, with its last imported statement."""
    found: list[PotOut] = []
    async with database.actor_transaction(staff.actor) as session:
        for pot in CASH_POTS:
            last = await statements.last_statement(session, pot.source, pot.account_reference)
            found.append(
                PotOut(
                    source=_source(pot.source),
                    account_reference=pot.account_reference,
                    name=pot.name,
                    currency=pot.currency.value,
                    last_statement_date=None if last is None else last.business_date,
                    last_closing=None if last is None else _money(last.closing_balance),
                )
            )
    return found


@staff_router.get("/statements")
async def list_statements(
    staff: Staff,
    database: Db,
    source: Source,
    account_reference: Annotated[str, Query(min_length=1, max_length=40)],
    date_from: date,
    date_to: date,
) -> list[StatementOut]:
    """One pot's statements in a range of days, at most 100, oldest first. 422
    `date_range_reversed` when `date_to` is before `date_from`."""
    pot = _pot(source, account_reference)
    if date_to < date_from:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["query", "date_to"], code="date_range_reversed")],
        )
    async with database.actor_transaction(staff.actor) as session:
        found = await statements.list_statements(
            session, pot.source, pot.account_reference, date_from=date_from, date_to=date_to
        )
    return [_statement(each) for each in found]


@staff_router.post("/statements", status_code=201)
async def import_statement(
    staff: Changer,
    database: Db,
    clock: Time,
    source: Annotated[Source, Form()],
    account_reference: Annotated[str, Form(min_length=1, max_length=40)],
    statement_date: Annotated[date, Form()],
    opening_balance: Annotated[str, Form(min_length=1, max_length=32)],
    closing_balance: Annotated[str, Form(min_length=1, max_length=32)],
    file: Annotated[UploadFile, File(description="CSV in a known layout, at most 2 MiB")],
    csv_format: Annotated[Literal["standard"], Form(alias="format")] = "standard",
) -> StatementOut:
    """Import one pot's statement for one ended day, whole or not at all.

    The balances are as the statement prints them, such as `1,500,000`. 422 names the problem
    and, for a row, its number, never its contents. 409 `statement_already_imported`,
    `statement_day_out_of_order`, `statement_opening_balance_differs` or
    `statement_day_not_over`.
    """
    pot = _pot(source, account_reference)
    content = await file.read(MAX_STATEMENT_BYTES + 1)
    if not 0 < len(content) <= MAX_STATEMENT_BYTES:
        raise _refused("validation_failed", "statement_file_size", "file")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _refused("validation_failed", "statement_not_text", "file") from None
    try:
        opening = parse_amount(opening_balance, pot.currency)
    except InvalidStatement as error:
        raise _refused("validation_failed", error.code, "opening_balance") from None
    try:
        closing = parse_amount(closing_balance, pot.currency)
    except InvalidStatement as error:
        raise _refused("validation_failed", error.code, "closing_balance") from None
    try:
        parsed = parse_csv(
            text,
            FORMATS[csv_format],
            source=pot.source,
            account_reference=pot.account_reference,
            statement_date=statement_date,
            opening_balance=opening,
            closing_balance=closing,
        )
    except InvalidStatement as error:
        path: tuple[str | int, ...] = ("file",) if error.row is None else ("file", error.row)
        raise _refused("validation_failed", error.code, *path) from None
    try:
        async with database.actor_transaction(staff.actor) as session:
            imported = await statements.import_statement(
                session, staff.actor, parsed, statements.file_digest(content), clock
            )
    except statements.StatementRefused as error:
        raise _refused("conflict", error.code) from None
    return _statement(imported)


@staff_router.get("/days/{day}")
async def get_day(day: date, staff: Staff, database: Db, clock: Time) -> DayOut:
    """Each pot at the end of the day, with every exception still open on it."""
    async with database.actor_transaction(staff.actor) as session:
        last = await day_close.last_closed(session)
        close = await day_close.get_close(session, day)
        found = await day_close.positions(session, day, clock)
        blocking = await day_close.blockers(session, day, found)
        positions: list[PositionOut] = []
        for pot, position in zip(CASH_POTS, found, strict=True):
            exceptions = await day_close.open_exceptions(session, pot, day)
            positions.append(_position(pot, position, exceptions))
    return DayOut(
        business_date=day,
        last_closed=last,
        close=None
        if close is None
        else CloseOut(
            id=close.id,
            status="closed" if close.status.value == "closed" else "prepared",
            prepared_by=close.prepared_by,
            decided_by=close.decided_by,
        ),
        blockers=list(blocking) if close is None else [],
        positions=positions,
    )


@staff_router.post("/days/{day}/refresh", status_code=204)
async def refresh(day: date, staff: Changer, database: Db, clock: Time) -> None:
    """Bring in the ledger's movements up to the day and pair what the records and references
    pair. Safe to run again."""
    if day >= business_date(clock.now()):
        raise _refused("conflict", "day_not_ended")
    async with database.actor_transaction(staff.actor) as session:
        await day_close.refresh(session, staff.actor, day, clock)


@staff_router.post("/matches", status_code=201)
async def match_by_hand(body: MatchIn, staff: Changer, database: Db, clock: Time) -> None:
    """A person pairs a line with a movement, with a reason. 409 `line_already_matched`,
    `movement_already_matched`, `match_not_found` or `match_not_same_money`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            await day_close.match_by_hand(
                session,
                staff.actor,
                statement_line_id=body.statement_line_id,
                entry_id=body.entry_id,
                line_no=body.line_no,
                reason=body.reason,
                clock=clock,
            )
    except day_close.MatchRefused as error:
        raise _refused("conflict", error.code) from None


@staff_router.post("/days/{day}/prepare")
async def prepare(day: date, staff: Changer, database: Db, clock: Time) -> CloseOut:
    """The first signature. 409 with the first thing that blocks the close."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            prepared = await day_close.prepare(session, staff.actor, day, clock)
    except CloseError as error:
        raise _refused("conflict", error.code) from None
    return CloseOut(
        id=prepared.id, status="prepared", prepared_by=prepared.prepared_by, decided_by=None
    )


@staff_router.post("/days/{day}/approve")
async def approve(day: date, staff: Approver, database: Db, clock: Time) -> CloseOut:
    """The second signature, by someone who neither prepared the close nor imported one of the
    day's statements. It closes the ledger through the day. 409 `close_not_prepared`,
    `close_needs_second_person`, `close_needs_someone_who_did_not_import` or
    `close_changed_since_prepared`, after which the close is sent back to be prepared again."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            closed = await day_close.approve(session, staff.actor, day, clock)
    except CloseError as error:
        raise _refused("conflict", error.code) from None
    return CloseOut(
        id=closed.id, status="closed", prepared_by=closed.prepared_by, decided_by=closed.decided_by
    )


@staff_router.post("/days/{day}/send-back", status_code=204)
async def send_back(day: date, body: ReasonIn, staff: Changer, database: Db, clock: Time) -> None:
    """Decline or withdraw a prepared close, with a reason, so it can be prepared again."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            await day_close.send_back(session, staff.actor, day, body.reason, clock)
    except CloseError as error:
        raise _refused("conflict", error.code) from None


def _statement(imported: statements.ImportedStatement) -> StatementOut:
    return StatementOut(
        id=imported.id,
        source=_source(imported.source),
        account_reference=imported.account_reference,
        business_date=imported.business_date,
        opening=_money(imported.opening_balance),
        closing=_money(imported.closing_balance),
        line_count=imported.line_count,
    )


def _position(
    pot: Pot, position: PotPosition, exceptions: tuple[ReconciliationException, ...]
) -> PositionOut:
    unexplained = position.unexplained
    open_movements = [movement for each in exceptions for movement in each.movements]
    return PositionOut(
        source=_source(pot.source),
        account_reference=pot.account_reference,
        name=pot.name,
        statement_closing=None
        if position.statement_closing is None
        else _money(position.statement_closing),
        ledger_closing=_money(position.ledger_closing),
        open_exceptions_total=_money(position.open_exceptions_total),
        open_exceptions_count=position.open_exceptions_count,
        unexplained=None if unexplained is None else _money(unexplained),
        exceptions=[
            ExceptionOut(
                kind=each.kind.value,
                difference=_money(each.difference(pot.currency)),
                statement_lines=[
                    _line(line, each.kind, open_movements) for line in each.statement_lines
                ],
                movements=[_movement(movement) for movement in each.movements],
            )
            for each in exceptions
        ],
    )


def _line(
    line: StatementLine, kind: ExceptionKind, open_movements: list[LedgerMovement]
) -> LineOut:
    offered = suggestions(line, open_movements) if kind is ExceptionKind.MISSING_IN_LEDGER else ()
    return LineOut(
        id=line.fingerprint,
        value_date=line.value_date,
        direction=line.direction.value,
        amount=_money(line.amount),
        reference=line.reference,
        counterparty_hint=line.narrative,
        suggested_movements=[movement.line_id for movement in offered],
    )


def _movement(movement: LedgerMovement) -> MovementOut:
    entry_id, _, line_no = movement.line_id.rpartition(":")
    return MovementOut(
        id=movement.line_id,
        entry_id=entry_id,
        line_no=int(line_no),
        effective_date=movement.effective_date,
        direction=movement.direction.value,
        amount=_money(movement.amount),
        reference=movement.reference,
        entry_type=movement.entry_type,
    )
