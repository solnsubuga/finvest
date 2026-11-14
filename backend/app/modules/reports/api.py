"""The Reports screen's routes: the catalogue, and one CSV per report and period.

`GET /staff/reports` lists the reports the reader may download, with the period each takes and
the one the screen offers first. `GET /staff/reports/{key}.csv` builds one. Every download is
written to the audit trail with the report and the period, never its contents.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity import service as identity
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.domain.permissions import permissions_for
from app.modules.identity.service import StaffSession, staff_names
from app.modules.reports import building, schedules
from app.modules.reports.domain.catalogue import (
    BY_CURRENCY,
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
from app.modules.reports.domain.schedules import MAX_RECIPIENTS, Frequency, allowed
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency

Reader = Annotated[StaffSession, Depends(require_permission("reports.view"))]
CurrencyCode = Literal["UGX", "USD"]


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


# Schedules. Proposing, deciding and stopping each need the report's own permission too, and
# approving needs a passkey or app code from the last few minutes.
Approver = Annotated[StaffSession, Depends(require_confirmed("reports.view"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class ScheduleOut(Contract):
    id: PushId
    report_key: ReportKey
    report_name: str
    frequency: Frequency
    # Null for a report that is not in one currency.
    currency: CurrencyCode | None
    recipients: list[PushId]
    recipient_names: list[str]
    reason: str
    status: Literal["proposed", "active", "declined", "withdrawn", "stopped"]
    proposed_by: PushId
    proposed_by_name: str | None
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None
    stopped_by: PushId | None
    stopped_at: datetime | None
    last_sent_at: datetime | None
    # True when the reader proposed it: they may withdraw it, never approve it.
    yours: bool


class RecipientOut(Contract):
    id: PushId
    name: str
    # The reports this person's role may download, so may be sent.
    reports: list[ReportKey]


class SchedulesOut(Contract):
    # Waiting and running first, then the rest newest first. Only schedules for reports the
    # reader may download.
    schedules: list[ScheduleOut]
    # How often each report the reader may download can be sent.
    frequencies: dict[ReportKey, list[Frequency]]
    # Active staff who may be sent at least one of those reports.
    people: list[RecipientOut]


class ProposeScheduleIn(Contract):
    report_key: ReportKey
    frequency: Frequency
    currency: CurrencyCode = "UGX"
    recipients: Annotated[list[PushId], Field(min_length=1, max_length=MAX_RECIPIENTS)]
    reason: Reason


class DecideScheduleIn(Contract):
    reason: Reason


schedules_router = APIRouter(
    prefix="/staff/report-schedules",
    tags=["staff reports"],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
routers.append(schedules_router)


def _schedule_out(
    view: schedules.ScheduleView, names: dict[str, str], staff: StaffSession
) -> ScheduleOut:
    shown = view.shown_currency
    return ScheduleOut(
        id=view.id,
        report_key=view.report_key,
        report_name=REPORTS[view.report_key].name,
        frequency=view.frequency,
        currency="USD" if shown == "USD" else ("UGX" if shown == "UGX" else None),
        recipients=list(view.recipients),
        recipient_names=[names.get(staff_id, "") for staff_id in view.recipients],
        reason=view.reason,
        status=view.status,
        proposed_by=view.proposed_by,
        proposed_by_name=names.get(view.proposed_by),
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
        stopped_by=view.stopped_by,
        stopped_at=view.stopped_at,
        last_sent_at=view.last_sent_at,
        yours=view.proposed_by == staff.staff_id,
    )


async def _one(
    session: AsyncSession, view: schedules.ScheduleView, staff: StaffSession
) -> ScheduleOut:
    names = await staff_names(session, {*view.recipients, view.proposed_by})
    return _schedule_out(view, names, staff)


@schedules_router.get("")
async def list_schedules(request: Request, staff: Reader) -> SchedulesOut:
    """Schedules for the reports this member of staff may download, and who may be sent each."""
    database: Database = request.app.state.database
    mine = {key for key, report in REPORTS.items() if may_download(report, staff.permissions)}
    async with database.actor_transaction(staff.actor) as session:
        found = [
            view for view in await schedules.list_schedules(session) if view.report_key in mine
        ]
        everyone = await identity.active_staff_roles(session)
        names = await staff_names(
            session, {person for view in found for person in (*view.recipients, view.proposed_by)}
        )
    people: list[RecipientOut] = []
    for staff_id, name, role in everyone:
        granted = permissions_for(role)
        allowed_reports = [
            key for key in REPORTS if key in mine and may_download(REPORTS[key], granted)
        ]
        if allowed_reports:
            people.append(RecipientOut(id=staff_id, name=name, reports=allowed_reports))
    return SchedulesOut(
        schedules=[_schedule_out(view, names, staff) for view in found],
        frequencies={key: list(allowed(key)) for key in REPORTS if key in mine},
        people=people,
    )


@schedules_router.post("", status_code=201)
async def propose_schedule(
    request: Request, body: ProposeScheduleIn, staff: Reader, idempotency_key: IdempotencyKey
) -> ScheduleOut:
    """The first person proposes a schedule. A different person must approve it."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        view = await schedules.propose(
            session,
            staff,
            report_key=body.report_key,
            frequency=body.frequency,
            currency=body.currency,
            recipients=body.recipients,
            reason=body.reason,
            idempotency_key=idempotency_key,
            clock=request.app.state.clock,
        )
        return await _one(session, view, staff)


@schedules_router.post("/{schedule_id}/approve")
async def approve_schedule(
    request: Request,
    schedule_id: PushId,
    body: DecideScheduleIn,
    staff: Approver,
    idempotency_key: IdempotencyKey,
) -> ScheduleOut:
    """A different person approves the schedule, which starts it."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        view = await schedules.approve(
            session,
            staff,
            schedule_id,
            reason=body.reason,
            idempotency_key=idempotency_key,
            clock=request.app.state.clock,
        )
        return await _one(session, view, staff)


@schedules_router.post("/{schedule_id}/decline")
async def decline_schedule(
    request: Request, schedule_id: PushId, body: DecideScheduleIn, staff: Reader
) -> ScheduleOut:
    """A different person declines it. Nothing is sent."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        view = await schedules.decline(
            session, staff, schedule_id, reason=body.reason, clock=request.app.state.clock
        )
        return await _one(session, view, staff)


@schedules_router.post("/{schedule_id}/withdraw")
async def withdraw_schedule(
    request: Request, schedule_id: PushId, body: DecideScheduleIn, staff: Reader
) -> ScheduleOut:
    """The proposer takes it back."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        view = await schedules.withdraw(
            session, staff, schedule_id, reason=body.reason, clock=request.app.state.clock
        )
        return await _one(session, view, staff)


@schedules_router.post("/{schedule_id}/stop")
async def stop_schedule(
    request: Request, schedule_id: PushId, body: DecideScheduleIn, staff: Reader
) -> ScheduleOut:
    """Stops a running schedule. One person may, because it sends less."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        view = await schedules.stop(
            session, staff, schedule_id, reason=body.reason, clock=request.app.state.clock
        )
        return await _one(session, view, staff)
