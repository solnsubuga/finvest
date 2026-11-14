"""The Reports screen's routes: the catalogue, and one CSV per report and period.

`GET /staff/reports` lists the reports the reader may download, with the period each takes and
the one the screen offers first. `GET /staff/reports/{key}.csv` builds one. Every download is
written to the audit trail with the report and the period, never its contents.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.modules.reports import building
from app.modules.reports.domain.catalogue import (
    REPORTS,
    BadPeriod,
    PeriodKind,
    ReportKey,
    TooManyRows,
    check_period,
    default_period,
    file_name,
    may_download,
    to_csv,
)
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency

Reader = Annotated[StaffSession, Depends(require_permission("reports.view"))]
CurrencyCode = Literal["UGX", "USD"]
# The reports whose figures are in one currency, so the screen asks which.
BY_CURRENCY: frozenset[ReportKey] = frozenset(
    {"daily_position", "deposits_and_withdrawals", "interest_and_tax", "maturity_ladder"}
)


class ReportOut(Contract):
    key: ReportKey
    name: str
    about: str
    period: PeriodKind
    by_currency: bool
    # The period the screen offers first.
    date_from: date
    date_to: date


class CatalogueOut(Contract):
    today: date
    # Only the reports this reader may download.
    reports: list[ReportOut]


router = APIRouter(
    prefix="/staff/reports",
    tags=["staff reports"],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
routers = [router]


def _refused(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


@router.get("")
async def catalogue(request: Request, staff: Reader) -> CatalogueOut:
    """The reports this member of staff may download."""
    clock: Clock = request.app.state.clock
    today = business_date(clock.now())
    reports: list[ReportOut] = []
    for report in REPORTS.values():
        if not may_download(report, staff.permissions):
            continue
        first, last = default_period(report.period, today)
        reports.append(
            ReportOut(
                key=report.key,
                name=report.name,
                about=report.about,
                period=report.period,
                by_currency=report.key in BY_CURRENCY,
                date_from=first,
                date_to=last,
            )
        )
    return CatalogueOut(today=today, reports=reports)


@router.get(
    "/{key}.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def download(
    request: Request,
    staff: Reader,
    key: ReportKey,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    currency: Annotated[CurrencyCode, Query()] = "UGX",
) -> Response:
    """One report for one period, as CSV. Recorded on the audit trail."""
    report = REPORTS[key]
    if not may_download(report, staff.permissions):
        raise ApplicationError("forbidden")
    clock: Clock = request.app.state.clock
    now = clock.now()
    try:
        check_period(report.period, date_from, date_to, today=business_date(now))
    except BadPeriod as bad:
        raise _refused(bad.field, bad.code) from None
    database: Database = request.app.state.database
    try:
        async with database.actor_transaction(staff.actor) as session:
            table = await building.build(
                session,
                staff,
                key,
                Currency(currency),
                date_from=date_from,
                date_to=date_to,
                now=now,
            )
            content = to_csv(table)
            await audit.record(
                session,
                AuditEvent(
                    category="access",
                    action="reports.downloaded",
                    resource_type="report",
                    resource_id=key,
                    actor=AuditActor.staff(staff.staff_id),
                    details={
                        "date_from": date_from.isoformat(),
                        "date_to": date_to.isoformat(),
                        "currency": currency if key in BY_CURRENCY else None,
                        "rows": len(table.rows),
                    },
                ),
                clock,
            )
    except TooManyRows:
        raise _refused("date_to", "too_many_rows") from None
    name = file_name(key, currency if key in BY_CURRENCY else None, date_from, date_to)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
