"""Report schedules: one person proposes a report, how often and who gets it, and a different
person approves it before anything is sent.

A schedule sends each recipient an email that says the report is ready, with a link to the
console's Reports page. The file is never attached: the person signs in and downloads it, and
the download is checked and audited like any other. Each recipient must be an active member of
staff whose role may download that report, when it is proposed, when it is approved and when
each email goes.

Stopping a running schedule sends less, so it takes one person. Every step is on the audit
trail with the schedule's ID, never a recipient's address.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity import service as identity
from app.modules.identity.domain.permissions import permissions_for
from app.modules.identity.service import StaffSession
from app.modules.reports.domain.catalogue import BY_CURRENCY, REPORTS, ReportKey, may_download
from app.modules.reports.domain.schedules import MAX_RECIPIENTS, Frequency, allowed
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorCode, FieldProblem
from app.shared.push_ids import push_id

ScheduleStatus = Literal["proposed", "active", "declined", "withdrawn", "stopped"]
PROPOSE_OPERATION = "reports.propose_schedule"
APPROVE_OPERATION = "reports.approve_schedule"
LIST_LIMIT = 200


class ScheduleRefused(ApplicationError):
    def __init__(self, code: str, *, status: ErrorCode = "conflict", field: str = "body") -> None:
        path: list[str | int] = ["body"] if field == "body" else ["body", field]
        super().__init__(status, fields=[FieldProblem(field_path=path, code=code)])
        self.refusal = code


@dataclass(frozen=True, slots=True)
class ScheduleView:
    id: str
    report_key: ReportKey
    frequency: Frequency
    # Only meaningful for a report in one currency. Others store UGX and show none.
    currency: str
    recipients: tuple[str, ...]
    reason: str
    status: ScheduleStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    stopped_by: str | None
    stopped_at: datetime | None
    # The last email sent for it, to anyone.
    last_sent_at: datetime | None = None

    @property
    def shown_currency(self) -> str | None:
        return self.currency if self.report_key in BY_CURRENCY else None


_COLUMNS = (
    "s.id, s.report_key, s.frequency, s.currency, s.recipients, s.reason, s.status, "
    "s.proposed_by, s.proposed_at, s.decided_by, s.decided_at, s.decision_reason, "
    "s.stopped_by, s.stopped_at, "
    "(SELECT max(d.finished_at) FROM report_sends d WHERE d.schedule_id = s.id "
    "AND d.status = 'sent') AS last_sent_at"
)
_GET = text(f"SELECT {_COLUMNS} FROM report_schedules s WHERE s.id = :id")  # noqa: S608
_GET_FOR_UPDATE = text(
    f"SELECT {_COLUMNS} FROM report_schedules s WHERE s.id = :id FOR UPDATE OF s"  # noqa: S608
)
# Waiting and running first, then the rest newest first.
_LIST = text(
    f"SELECT {_COLUMNS} FROM report_schedules s "  # noqa: S608
    "ORDER BY (s.status IN ('proposed', 'active')) DESC, s.proposed_at DESC, s.id LIMIT :limit"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM report_schedules s WHERE s.status = 'proposed' "  # noqa: S608
    "ORDER BY s.proposed_at, s.id"
)
_ACTIVE = text(
    f"SELECT {_COLUMNS} FROM report_schedules s WHERE s.status = 'active' "  # noqa: S608
    "ORDER BY s.id"
)
_INSERT = text(
    "INSERT INTO report_schedules (id, report_key, frequency, currency, recipients, reason, "
    "status, proposed_by, proposed_at) VALUES (:id, :report_key, :frequency, :currency, "
    "CAST(:recipients AS push_id[]), :reason, 'proposed', :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE report_schedules SET status = :status, decided_by = :by, decided_at = :now, "
    "decision_reason = :reason WHERE id = :id AND status = 'proposed'"
)
_STOP = text(
    "UPDATE report_schedules SET status = 'stopped', stopped_by = :by, stopped_at = :now "
    "WHERE id = :id AND status = 'active'"
)


def _view(row: Row[tuple[object, ...]]) -> ScheduleView:
    data = row._mapping
    return ScheduleView(
        id=cast(str, data["id"]),
        report_key=cast(ReportKey, data["report_key"]),
        frequency=cast(Frequency, data["frequency"]),
        currency=cast(str, data["currency"]),
        recipients=tuple(cast(Sequence[str], data["recipients"])),
        reason=cast(str, data["reason"]),
        status=cast(ScheduleStatus, data["status"]),
        proposed_by=cast(str, data["proposed_by"]),
        proposed_at=cast(datetime, data["proposed_at"]),
        decided_by=cast(str | None, data["decided_by"]),
        decided_at=cast(datetime | None, data["decided_at"]),
        decision_reason=cast(str | None, data["decision_reason"]),
        stopped_by=cast(str | None, data["stopped_by"]),
        stopped_at=cast(datetime | None, data["stopped_at"]),
        last_sent_at=cast(datetime | None, data["last_sent_at"]),
    )


async def get_schedule(session: AsyncSession, schedule_id: str) -> ScheduleView:
    row = (await session.execute(_GET, {"id": schedule_id})).first()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def list_schedules(session: AsyncSession) -> list[ScheduleView]:
    rows = await session.execute(_LIST, {"limit": LIST_LIMIT})
    return [_view(row) for row in rows.all()]


async def waiting(session: AsyncSession) -> list[ScheduleView]:
    """Proposed schedules, for the approvals inbox, oldest first."""
    return [_view(row) for row in (await session.execute(_WAITING)).all()]


async def active(session: AsyncSession) -> list[ScheduleView]:
    """Running schedules, for the worker's morning run."""
    return [_view(row) for row in (await session.execute(_ACTIVE)).all()]


async def _check_recipients(
    session: AsyncSession, key: ReportKey, recipients: Sequence[str]
) -> None:
    """Every recipient is an active member of staff whose role may download the report."""
    report = REPORTS[key]
    contacts = await identity.staff_contacts(session, recipients)
    for staff_id in recipients:
        contact = contacts.get(staff_id)
        if contact is None or not contact.active:
            raise ScheduleRefused(
                "recipient_not_active", status="validation_failed", field="recipients"
            )
        if not may_download(report, permissions_for(contact.role)):
            raise ScheduleRefused(
                "recipient_may_not_download", status="validation_failed", field="recipients"
            )


def _check_reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned:
        raise ScheduleRefused("reason_required", status="validation_failed", field="reason")
    return cleaned


def _may_handle(staff: StaffSession, key: ReportKey) -> None:
    # Nobody proposes, approves or stops a schedule for a report they could not download.
    if not may_download(REPORTS[key], staff.permissions):
        raise ApplicationError("forbidden")


async def propose(
    session: AsyncSession,
    staff: StaffSession,
    *,
    report_key: ReportKey,
    frequency: Frequency,
    currency: str,
    recipients: Sequence[str],
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> ScheduleView:
    """The first person proposes a schedule. A different person must approve it.

    The same key with the same request returns the first proposal, not a second one.
    """
    _may_handle(staff, report_key)
    cleaned = _check_reason(reason)
    if frequency not in allowed(report_key):
        raise ScheduleRefused(
            "frequency_not_allowed", status="validation_failed", field="frequency"
        )
    chosen = sorted(set(recipients))
    if not chosen or len(chosen) > MAX_RECIPIENTS:
        raise ScheduleRefused("recipients_count", status="validation_failed", field="recipients")
    stored_currency = currency if report_key in BY_CURRENCY else "UGX"
    now = clock.now()

    async def command() -> Receipt:
        await _check_recipients(session, report_key, chosen)
        schedule_id = push_id()
        await session.execute(
            _INSERT,
            {
                "id": schedule_id,
                "report_key": report_key,
                "frequency": frequency,
                "currency": stored_currency,
                "recipients": chosen,
                "reason": cleaned,
                "proposed_by": staff.staff_id,
                "now": now,
            },
        )
        await _audit(session, staff, "reports.schedule_proposed", schedule_id, clock, cleaned)
        return Receipt(201, {"schedule_id": schedule_id})

    outcome = await run_once(
        session,
        actor=staff.actor,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={
            "report_key": report_key,
            "frequency": frequency,
            "currency": stored_currency,
            "recipients": chosen,
            "reason": cleaned,
        },
        now=now,
        command=command,
    )
    return await get_schedule(session, str(outcome.receipt.response["schedule_id"]))


async def _locked(session: AsyncSession, schedule_id: str) -> ScheduleView:
    row = (await session.execute(_GET_FOR_UPDATE, {"id": schedule_id})).first()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def _proposed(session: AsyncSession, staff: StaffSession, schedule_id: str) -> ScheduleView:
    schedule = await _locked(session, schedule_id)
    _may_handle(staff, schedule.report_key)
    if schedule.status != "proposed":
        raise ScheduleRefused("not_waiting")
    return schedule


async def approve(
    session: AsyncSession,
    staff: StaffSession,
    schedule_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> ScheduleView:
    """A different person approves the schedule, which starts it. The recipients are checked
    again, because a role may have changed since it was proposed."""
    cleaned = _check_reason(reason)
    now = clock.now()

    async def command() -> Receipt:
        schedule = await _proposed(session, staff, schedule_id)
        if schedule.proposed_by == staff.staff_id:
            raise ScheduleRefused("proposed_by_you", status="forbidden")
        await _check_recipients(session, schedule.report_key, schedule.recipients)
        await session.execute(
            _DECIDE,
            {
                "id": schedule_id,
                "status": "active",
                "by": staff.staff_id,
                "now": now,
                "reason": cleaned,
            },
        )
        await _audit(session, staff, "reports.schedule_approved", schedule_id, clock, cleaned)
        return Receipt(200, {"schedule_id": schedule_id})

    await run_once(
        session,
        actor=staff.actor,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"schedule_id": schedule_id, "reason": cleaned},
        now=now,
        command=command,
    )
    return await get_schedule(session, schedule_id)


async def decline(
    session: AsyncSession, staff: StaffSession, schedule_id: str, *, reason: str, clock: Clock
) -> ScheduleView:
    """A different person declines it. Nothing is ever sent for it."""
    cleaned = _check_reason(reason)
    schedule = await _proposed(session, staff, schedule_id)
    if schedule.proposed_by == staff.staff_id:
        raise ScheduleRefused("proposed_by_you", status="forbidden")
    await session.execute(
        _DECIDE,
        {
            "id": schedule_id,
            "status": "declined",
            "by": staff.staff_id,
            "now": clock.now(),
            "reason": cleaned,
        },
    )
    await _audit(session, staff, "reports.schedule_declined", schedule_id, clock, cleaned)
    return await get_schedule(session, schedule_id)


async def withdraw(
    session: AsyncSession, staff: StaffSession, schedule_id: str, *, reason: str, clock: Clock
) -> ScheduleView:
    """The proposer takes it back before anyone decides."""
    cleaned = _check_reason(reason)
    schedule = await _proposed(session, staff, schedule_id)
    if schedule.proposed_by != staff.staff_id:
        raise ScheduleRefused("not_proposed_by_you", status="forbidden")
    await session.execute(
        _DECIDE,
        {
            "id": schedule_id,
            "status": "withdrawn",
            "by": staff.staff_id,
            "now": clock.now(),
            "reason": cleaned,
        },
    )
    await _audit(session, staff, "reports.schedule_withdrawn", schedule_id, clock, cleaned)
    return await get_schedule(session, schedule_id)


async def stop(
    session: AsyncSession, staff: StaffSession, schedule_id: str, *, reason: str, clock: Clock
) -> ScheduleView:
    """Anyone who may download the report stops a running schedule. Emails already queued
    are not sent."""
    cleaned = _check_reason(reason)
    schedule = await _locked(session, schedule_id)
    _may_handle(staff, schedule.report_key)
    if schedule.status != "active":
        raise ScheduleRefused("not_running")
    await session.execute(_STOP, {"id": schedule_id, "by": staff.staff_id, "now": clock.now()})
    await _audit(session, staff, "reports.schedule_stopped", schedule_id, clock, cleaned)
    return await get_schedule(session, schedule_id)


async def _audit(
    session: AsyncSession,
    staff: StaffSession,
    action: str,
    schedule_id: str,
    clock: Clock,
    reason: str,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "declined")) else "change",
            action=action,
            resource_type="report_schedule",
            resource_id=schedule_id,
            actor=AuditActor.staff(staff.staff_id),
            reason=reason,
        ),
        clock,
    )
