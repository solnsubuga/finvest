"""Finance over HTTP, for staff with `finance.view`.

`GET /staff/finance` is the screen for one currency and month: each account code's opening
balance, debits, credits and closing balance, Finvest's own income and costs, how far the
ledger is closed, and the journals already sent to the accounting system.

`GET /staff/finance/journal.csv` is the daily journal for closed days, added up by account code,
as a file for the accounting system. It moves no money, so one person exports it. Each export
is recorded, with an audit event, so the screen can say what was sent last.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.finance import books, month_close
from app.modules.finance.domain.books import BadMonth, month_end, month_label, parse_month
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


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
FinanceStaff = Annotated[StaffSession, Depends(require_permission("finance.view"))]
# Closing a month needs a passkey or app code from the last few minutes.
FinanceApprover = Annotated[StaffSession, Depends(require_confirmed("finance.view"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


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
    reason: Reason


class DecideCloseIn(Contract):
    reason: Reason


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
            )
            for e in exports
        ],
    )


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

    @router.get(
        "/journal.csv",
        response_class=Response,
        responses={200: {"content": {"text/csv": {}}}},
        summary="The daily journal for closed days, by account code, for the accounting system",
    )
    async def export_journal(
        request: Request,
        staff: FinanceStaff,
        clock: AppClock,
        date_from: Annotated[date, Query()],
        date_to: Annotated[date, Query()],
        currency: Annotated[Currency, Query()] = Currency.UGX,
    ) -> Response:
        database: Database = request.app.state.database
        try:
            async with database.actor_transaction(staff.actor) as session:
                journal = await books.export_journal(
                    session,
                    staff.staff_id,
                    currency,
                    date_from=date_from,
                    date_to=date_to,
                    clock=clock,
                )
        except ExportRefused as refused:
            if refused.code == "day_not_closed":
                raise ApplicationError(
                    "conflict", fields=[FieldProblem(field_path=["date_to"], code=refused.code)]
                ) from None
            raise _refused("date_to", refused.code) from None
        name = (
            f"finvest-journal-{currency.value.lower()}-{date_from.isoformat()}"
            + ("" if date_from == date_to else f"-to-{date_to.isoformat()}")
            + ".csv"
        )
        return Response(
            content=journal.content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )

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
