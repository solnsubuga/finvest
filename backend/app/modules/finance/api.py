"""Finance over HTTP, for staff with `finance.view`.

`GET /staff/finance` is the screen for one currency and month: each account code's opening
balance, debits, credits and closing balance, Finvest's own income and costs, how far the
ledger is closed, and the journals already sent to the accounting system.

`GET /staff/finance/journal.csv` is the daily journal for closed days, added up by account code,
as a file for the accounting system. It moves no money, so one person exports it. Each export
is recorded, with an audit event, so the screen can say what was sent last.
"""

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.finance import books
from app.modules.finance.domain.books import BadMonth, month_end, month_label, parse_month
from app.modules.finance.domain.journal import MAX_DAYS, ExportRefused
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession, staff_names
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract
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

    return router
