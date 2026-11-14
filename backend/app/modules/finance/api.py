"""Finance over HTTP, for staff with `finance.view`.

`GET /staff/finance` is the screen for one currency and month: each account code's opening
balance, debits, credits and closing balance, Finvest's own income and costs, how far the
ledger is closed, and the journals already sent to the accounting system.

`POST /staff/finance/journal.csv` is the daily journal for closed days, added up by account
code, as a file for the accounting system: Finvest's generic CSV, or the journal import of
QuickBooks Online, Xero, Sage Accounting or Zoho Books. It moves no money, so one person
exports it. Each export is recorded, with an audit event, so the screen can say what was sent
last. It is a POST because it records.

`/staff/finance/account-codes` is each accounting system's account code map, on the console's
Settings screen. A change takes two people: one proposes a whole map, a different one approves.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.finance import books, code_maps, month_close
from app.modules.finance.domain.books import BadMonth, month_end, month_label, parse_month
from app.modules.finance.domain.code_maps import DEFAULT_XERO_TAX_RATE, MAX_CODES, MAX_REASON
from app.modules.finance.domain.formats import (
    BOOKS_CURRENCY,
    SYSTEM_NAMES,
    CodesNotMapped,
    Format,
    System,
)
from app.modules.finance.domain.journal import MAX_DAYS, ExportRefused
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession, staff_names
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 409, 422)
}


class AmountOut(Contract):
    minor: int
    currency: Currency


class BookLineOut(Contract):
    code: str
    name: str
    kind: str
    # Opening and closing are in the account's normal direction. Debits and credits are each
    # zero or more.
    opening: AmountOut
    debits: AmountOut
    credits: AmountOut
    closing: AmountOut


class IncomeLineOut(Contract):
    code: str
    name: str
    amount: AmountOut


class IncomeOut(Contract):
    income: list[IncomeLineOut]
    costs: list[IncomeLineOut]
    income_total: AmountOut
    cost_total: AmountOut
    net: AmountOut


class DaysOut(Contract):
    date_from: date
    date_to: date


class ExportOut(Contract):
    id: str
    date_from: date
    date_to: date
    lines: int
    debit_total: AmountOut
    exported_by: str
    exported_by_name: str | None
    exported_at: datetime
    format: Format


class FormatOut(Contract):
    """A file the journal can be downloaded as, and whether it can be now."""

    format: Format
    name: str
    # False while the system has no approved code map. The generic CSV is always ready.
    ready: bool
    # Ledger codes the system's map does not name yet. An export that meets one is refused.
    unmapped: list[str]
    # The currencies the format is written in. The systems take shillings only.
    currencies: list[Currency]


class FinanceScreenOut(Contract):
    currency: Currency
    month: str  # YYYY-MM
    # The days the figures cover: the month, up to today when it is this month.
    date_from: date
    date_to: date
    accounts: list[BookLineOut]
    income: IncomeOut
    closed_through: date | None
    sent_through: date | None
    # Closed days not yet sent, at most MAX_DAYS of them, or null when there are none.
    next_export: DaysOut | None
    exports: list[ExportOut]
    formats: list[FormatOut]


# JSON carries dates and names as strings, so these are read from them.
DateIn = Annotated[date, Field(strict=False)]


class JournalRequest(Contract):
    currency: Annotated[Currency, Field(strict=False)] = Currency.UGX
    date_from: DateIn
    date_to: DateIn
    format: Annotated[Format, Field(strict=False)] = Format.CSV


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
FinanceStaff = Annotated[StaffSession, Depends(require_permission("finance.view"))]
# The code maps sit on the console's Settings screen. Opening it shows them. Changing them is a
# permission of its own, so read-only access to Settings is never one of the two people.
SettingsStaff = Annotated[StaffSession, Depends(require_permission("settings.view"))]
CodeMapStaff = Annotated[StaffSession, Depends(require_permission("account_codes.change"))]
# Deciding needs a passkey or app code from the last few minutes.
CodeMapDecider = Annotated[StaffSession, Depends(require_confirmed("account_codes.change"))]
# Closing a month needs a passkey or app code from the last few minutes.
FinanceApprover = Annotated[StaffSession, Depends(require_confirmed("finance.view"))]
CloseReason = Annotated[str, Field(min_length=1, max_length=500)]


class SignedLineOut(Contract):
    currency: Currency
    code: str
    kind: str
    opening: int
    debits: int
    credits: int
    closing: int


class MonthCloseOut(Contract):
    id: PushId
    month: str  # YYYY-MM
    status: Literal["proposed", "approved", "declined", "withdrawn"]
    # The books by code and currency as they stood when it was proposed, in minor units.
    books: list[SignedLineOut]
    books_sha256: str
    reason: str
    proposed_by: PushId
    proposed_by_name: str | None
    proposed_at: datetime
    decided_by: PushId | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    yours: bool


class MonthToCloseOut(Contract):
    month: str  # YYYY-MM
    closed_through: date | None
    # Null when it can be proposed now. Otherwise month_not_ended, close_months_in_order,
    # days_not_closed, books_do_not_balance or close_already_proposed.
    refusal: str | None


class MonthClosesOut(Contract):
    last_closed: str | None
    next: MonthToCloseOut
    closes: list[MonthCloseOut]


class ProposeCloseIn(Contract):
    month: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}$")]
    reason: CloseReason


class DecideCloseIn(Contract):
    reason: CloseReason


def _close_out(
    view: month_close.MonthCloseView, names: dict[str, str], staff: StaffSession
) -> MonthCloseOut:
    return MonthCloseOut(
        id=view.id,
        month=month_label(view.month),
        status=view.status,
        books=[
            SignedLineOut(
                currency=Currency(line.currency),
                code=line.code,
                kind=line.kind,
                opening=line.opening,
                debits=line.debits,
                credits=line.credits,
                closing=line.closing,
            )
            for line in view.books
        ],
        books_sha256=view.books_sha256,
        reason=view.reason,
        proposed_by=view.proposed_by,
        proposed_by_name=names.get(view.proposed_by),
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_by_name=names.get(view.decided_by) if view.decided_by else None,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
        yours=view.proposed_by == staff.staff_id,
    )


async def _one_close(
    session: AsyncSession, view: month_close.MonthCloseView, staff: StaffSession
) -> MonthCloseOut:
    names = await staff_names(
        session, {view.proposed_by, *([view.decided_by] if view.decided_by else [])}
    )
    return _close_out(view, names, staff)


def _amount(money: Money) -> AmountOut:
    return AmountOut(minor=money.minor, currency=money.currency)


def _refused(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def next_export(closed: date | None, sent: date | None) -> DaysOut | None:
    """The closed days after the last one sent. The first export is the last closed day."""
    if closed is None:
        return None
    start = sent + timedelta(days=1) if sent is not None else closed
    if start > closed:
        return None
    return DaysOut(date_from=start, date_to=min(closed, start + timedelta(days=MAX_DAYS - 1)))


async def _screen(
    session: AsyncSession, currency: Currency, month: date, today: date
) -> FinanceScreenOut:
    date_to = min(month_end(month), today)
    found = await books.books(session, currency, date_from=month, date_to=date_to)
    closed = await books.closed_through(session)
    sent = await books.sent_through(session, currency)
    exports = await books.recent_exports(session, currency)
    names = await staff_names(session, {e.exported_by for e in exports})
    formats = await _formats(session)
    income = found.income
    return FinanceScreenOut(
        currency=currency,
        month=month_label(month),
        date_from=month,
        date_to=date_to,
        accounts=[
            BookLineOut(
                code=line.code,
                name=line.name,
                kind=line.kind,
                opening=_amount(line.opening),
                debits=_amount(line.debits),
                credits=_amount(line.credits),
                closing=_amount(line.closing),
            )
            for line in found.lines
        ],
        income=IncomeOut(
            income=[
                IncomeLineOut(code=i.code, name=i.name, amount=_amount(i.amount))
                for i in income.income
            ],
            costs=[
                IncomeLineOut(code=c.code, name=c.name, amount=_amount(c.amount))
                for c in income.costs
            ],
            income_total=_amount(income.income_total),
            cost_total=_amount(income.cost_total),
            net=_amount(income.net),
        ),
        closed_through=closed,
        sent_through=sent,
        next_export=next_export(closed, sent),
        exports=[
            ExportOut(
                id=e.id,
                date_from=e.date_from,
                date_to=e.date_to,
                lines=e.lines,
                debit_total=_amount(Money(e.debit_total_minor, e.currency)),
                exported_by=e.exported_by,
                exported_by_name=names.get(e.exported_by),
                exported_at=e.exported_at,
                format=e.format,
            )
            for e in exports
        ],
        formats=formats,
    )


async def _formats(session: AsyncSession) -> list[FormatOut]:
    codes = [code.code for code in await code_maps.ledger_codes(session)]
    listed = [
        FormatOut(
            format=Format.CSV,
            name="Finvest CSV",
            ready=True,
            unmapped=[],
            currencies=list(Currency),
        )
    ]
    for system in System:
        active = await code_maps.active_map(session, system)
        listed.append(
            FormatOut(
                format=Format(system.value),
                name=SYSTEM_NAMES[system],
                ready=active is not None,
                unmapped=[c for c in codes if active is None or c not in active.accounts],
                currencies=[BOOKS_CURRENCY],
            )
        )
    return listed


def create_router() -> APIRouter:
    router = APIRouter(prefix="/staff/finance", tags=["finance"], responses=_REFUSALS)

    @router.get("", summary="The books by account code for a month, and the journals sent")
    async def get_finance(
        request: Request,
        staff: FinanceStaff,
        clock: AppClock,
        currency: Annotated[Currency, Query()] = Currency.UGX,
        month: Annotated[str | None, Query(pattern=r"^[0-9]{4}-[0-9]{2}$")] = None,
    ) -> FinanceScreenOut:
        today = business_date(clock.now())
        if month is None:
            first = today.replace(day=1)
        else:
            try:
                first = parse_month(month)
            except BadMonth:
                raise _refused("month", "bad_month") from None
            if first > today:
                raise _refused("month", "month_in_future")
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            return await _screen(session, currency, first, today)

    @router.post(
        "/journal.csv",
        response_class=Response,
        responses={200: {"content": {"text/csv": {}}}},
        summary="The daily journal for closed days, by account code, for the accounting system",
    )
    async def export_journal(
        request: Request, body: JournalRequest, staff: FinanceStaff, clock: AppClock
    ) -> Response:
        database: Database = request.app.state.database
        try:
            async with database.actor_transaction(staff.actor) as session:
                journal = await books.export_journal(
                    session,
                    staff.staff_id,
                    body.currency,
                    date_from=body.date_from,
                    date_to=body.date_to,
                    clock=clock,
                    format_=body.format,
                )
        except CodesNotMapped as refused:
            # Name the codes, never the accounts: the console sends the person to Settings.
            raise ApplicationError(
                "conflict",
                fields=[
                    FieldProblem(field_path=["codes", code], code="not_mapped")
                    for code in refused.codes[:31]
                ],
            ) from None
        except ExportRefused as refused:
            if refused.code == "day_not_closed":
                raise ApplicationError(
                    "conflict", fields=[FieldProblem(field_path=["date_to"], code=refused.code)]
                ) from None
            if refused.code == "currency_not_supported":
                raise _refused("format", refused.code) from None
            raise _refused("date_to", refused.code) from None
        name = journal_filename(body.currency, body.format, body.date_from, body.date_to)
        return Response(
            content=journal.content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/account-codes", summary="Each accounting system's account code map")
    async def get_code_maps(request: Request, staff: SettingsStaff) -> CodeMapsOut:
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            return await _code_maps_screen(session, staff)

    @router.post(
        "/account-codes/{system}/proposals",
        status_code=201,
        summary="Propose a whole new account code map for one accounting system",
    )
    async def propose_code_map(
        request: Request, system: System, body: ProposeCodeMap, staff: CodeMapStaff, clock: AppClock
    ) -> CodeMapsOut:
        """Exports keep the map in use until a different member of staff approves this one."""
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            await code_maps.propose(
                session, staff.actor, system, body.accounts, body.tax_rate, body.reason, clock
            )
            return await _code_maps_screen(session, staff)

    @router.post(
        "/account-codes/proposals/{map_id}/decision",
        summary="Approve or reject someone else's code map, or withdraw your own",
    )
    async def decide_code_map(
        request: Request,
        map_id: PushId,
        body: DecideCodeMap,
        staff: CodeMapDecider,
        clock: AppClock,
    ) -> CodeMapsOut:
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            await code_maps.decide(session, staff.actor, map_id, body.decision, body.reason, clock)
            return await _code_maps_screen(session, staff)

    @router.get("/month-closes", summary="The month to close next, and the latest closes")
    async def month_closes(
        request: Request, staff: FinanceStaff, clock: AppClock
    ) -> MonthClosesOut:
        today = business_date(clock.now())
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            last = await month_close.last_closed_month(session)
            offered = month_close.next_month(last, today)
            ready = await month_close.readiness(session, offered, today)
            found = await month_close.recent(session)
            names = await staff_names(
                session,
                {p for c in found for p in (c.proposed_by, c.decided_by) if p is not None},
            )
        refusal = ready.refusal
        if refusal is None and any(c.status == "proposed" for c in found):
            refusal = "close_already_proposed"
        return MonthClosesOut(
            last_closed=month_label(last) if last is not None else None,
            next=MonthToCloseOut(
                month=month_label(offered), closed_through=ready.closed_through, refusal=refusal
            ),
            closes=[_close_out(c, names, staff) for c in found],
        )

    @router.post("/month-closes", status_code=201, summary="Propose closing a month")
    async def propose_month_close(
        request: Request,
        body: ProposeCloseIn,
        staff: FinanceStaff,
        clock: AppClock,
        idempotency_key: IdempotencyKey,
    ) -> MonthCloseOut:
        try:
            month = parse_month(body.month)
        except BadMonth:
            raise _refused("month", "bad_month") from None
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            view = await month_close.propose(
                session,
                staff,
                month,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
            return await _one_close(session, view, staff)

    @router.post("/month-closes/{close_id}/approve", summary="Close the month")
    async def approve_month_close(
        request: Request,
        close_id: PushId,
        body: DecideCloseIn,
        staff: FinanceApprover,
        clock: AppClock,
        idempotency_key: IdempotencyKey,
    ) -> MonthCloseOut:
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            view = await month_close.approve(
                session,
                staff,
                close_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
            return await _one_close(session, view, staff)

    @router.post("/month-closes/{close_id}/decline", summary="Send a month close back")
    async def decline_month_close(
        request: Request,
        close_id: PushId,
        body: DecideCloseIn,
        staff: FinanceStaff,
        clock: AppClock,
    ) -> MonthCloseOut:
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            view = await month_close.decline(
                session, staff, close_id, reason=body.reason, clock=clock
            )
            return await _one_close(session, view, staff)

    @router.post("/month-closes/{close_id}/withdraw", summary="Take back your month close")
    async def withdraw_month_close(
        request: Request,
        close_id: PushId,
        body: DecideCloseIn,
        staff: FinanceStaff,
        clock: AppClock,
    ) -> MonthCloseOut:
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            view = await month_close.withdraw(
                session, staff, close_id, reason=body.reason, clock=clock
            )
            return await _one_close(session, view, staff)

    return router


def journal_filename(currency: Currency, format_: Format, date_from: date, date_to: date) -> str:
    days = date_from.isoformat() + ("" if date_from == date_to else f"-to-{date_to.isoformat()}")
    system = "" if format_ is Format.CSV else f"-{format_.value}"
    return f"finvest-journal{system}-{currency.value.lower()}-{days}.csv"


# The account code maps, on the console's Settings screen ---------------------------------

Reason = Annotated[str, Field(min_length=1, max_length=MAX_REASON)]


class LedgerCodeOut(Contract):
    code: str
    name: str
    kind: str


class MapVersionOut(Contract):
    id: str
    accounts: dict[str, str]
    tax_rate: str | None
    reason: str
    status: Literal["proposed", "approved", "rejected", "withdrawn"]
    proposed_by: str
    proposed_by_name: str | None
    proposed_at: datetime
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    # The reader proposed it: they may withdraw it, never approve it.
    yours: bool


class SystemMapOut(Contract):
    system: System
    name: str
    # What the system calls the value each code is mapped to.
    account_label: str
    needs_tax_rate: bool
    default_tax_rate: str | None
    active: MapVersionOut | None
    proposal: MapVersionOut | None
    # The last decisions, newest first, without their accounts.
    history: list[MapVersionOut]


class CodeMapsOut(Contract):
    codes: list[LedgerCodeOut]
    systems: list[SystemMapOut]


class ProposeCodeMap(Contract):
    # Ledger code to the system's account. A blank account leaves the code unmapped.
    accounts: Annotated[
        dict[str, Annotated[str, Field(max_length=100)]], Field(max_length=MAX_CODES)
    ]
    tax_rate: Annotated[str | None, Field(max_length=50)] = None
    reason: Reason


class DecideCodeMap(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


ACCOUNT_LABELS = {
    System.QUICKBOOKS: "Account name",
    System.XERO: "Account code",
    System.SAGE: "Nominal code",
    System.ZOHO: "Account name",
}


async def _code_maps_screen(session: AsyncSession, staff: StaffSession) -> CodeMapsOut:
    listed = await code_maps.list_maps(session)
    people = set()
    for item in listed:
        for version in [item.active, item.proposal, *item.history]:
            if version is not None:
                people.add(version.proposed_by)
                if version.decided_by:
                    people.add(version.decided_by)
    names = await staff_names(session, people)

    def out(version: code_maps.MapVersion) -> MapVersionOut:
        return MapVersionOut(
            id=version.id,
            accounts=version.accounts,
            tax_rate=version.tax_rate,
            reason=version.reason,
            status=version.status,
            proposed_by=version.proposed_by,
            proposed_by_name=names.get(version.proposed_by),
            proposed_at=version.proposed_at,
            decided_by=version.decided_by,
            decided_by_name=names.get(version.decided_by) if version.decided_by else None,
            decided_at=version.decided_at,
            decision_reason=version.decision_reason,
            yours=version.proposed_by == staff.staff_id,
        )

    return CodeMapsOut(
        codes=[
            LedgerCodeOut(code=c.code, name=c.name, kind=c.kind)
            for c in await code_maps.ledger_codes(session)
        ],
        systems=[
            SystemMapOut(
                system=item.system,
                name=SYSTEM_NAMES[item.system],
                account_label=ACCOUNT_LABELS[item.system],
                needs_tax_rate=item.system is System.XERO,
                default_tax_rate=DEFAULT_XERO_TAX_RATE if item.system is System.XERO else None,
                active=out(item.active) if item.active else None,
                proposal=out(item.proposal) if item.proposal else None,
                history=[out(v) for v in item.history],
            )
            for item in listed
        ],
    )
