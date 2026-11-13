"""Routes for the audit trail on the console's Reports and audit screen.

Reading and exporting need `audit.view`. The trail is already redacted when it is written, so
what is shown is what is stored: identifiers, reasons and details, never personal data. Every
export is itself recorded, with the filters it used.
"""

import csv
import io
import json
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import JsonValue

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.audit.domain.events import (
    ActorKind,
    AuditActor,
    AuditCategory,
    AuditEvent,
    AuditOutcome,
)
from app.modules.audit.domain.search import (
    AuditFilter,
    Cursor,
    InvalidFilter,
    csv_cell,
    decode_cursor,
    encode_cursor,
)
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession, staff_names
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
}
router = APIRouter(prefix="/staff/audit", tags=["staff audit trail"], responses=_REFUSALS)
routers = [router]

AuditStaff = Annotated[StaffSession, Depends(require_permission("audit.view"))]
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# An export larger than this is refused: narrow the dates or the filters.
EXPORT_LIMIT = 10_000
EXPORT_COLUMNS = (
    "occurred_at",
    "category",
    "action",
    "outcome",
    "actor_kind",
    "actor_id",
    "actor_name",
    "resource_type",
    "resource_id",
    "reason",
    "correlation_id",
    "details",
)


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


def _filters(
    category: Annotated[AuditCategory | None, Query()] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    outcome: Annotated[AuditOutcome | None, Query()] = None,
    actor_kind: Annotated[ActorKind | None, Query()] = None,
    actor_id: Annotated[str | None, Query(max_length=20)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    resource_id: Annotated[str | None, Query(max_length=200)] = None,
    correlation_id: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> AuditFilter:
    try:
        return AuditFilter(
            category=category,
            action=action,
            outcome=outcome,
            actor_kind=actor_kind,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            correlation_id=correlation_id,
            since=since,
            until=until,
        )
    except InvalidFilter as error:
        raise _invalid(error.field) from None


Filters = Annotated[AuditFilter, Depends(_filters)]


class EventView(Contract):
    id: PushId
    occurred_at: datetime
    category: AuditCategory
    action: str
    outcome: AuditOutcome
    actor_kind: ActorKind
    actor_id: PushId | None
    # The member of staff's name, when the actor is staff. Clients are shown by ID only.
    actor_name: str | None
    resource_type: str
    resource_id: str | None
    reason: str | None
    correlation_id: str | None
    # Already redacted when written.
    details: dict[str, JsonValue]


class EventPage(Contract):
    events: list[EventView]
    # Pass as `cursor` for the next, older page. None on the last page.
    next_cursor: str | None


class Facets(Contract):
    actions: list[str]
    resource_types: list[str]


@router.get("/events")
async def list_events(
    staff: AuditStaff,
    database: Db,
    filters: Filters,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE,
) -> EventPage:
    """The trail, newest first, filtered. Continue with `next_cursor`."""
    after = _cursor(cursor)
    async with database.actor_transaction(staff.actor) as session:
        # One more than asked, to know whether another page follows.
        found = await audit.search_events(session, filters, after=after, limit=limit + 1)
        page = found[:limit]
        names = await staff_names(session, _staff_ids(page))
    following = None
    if len(found) > limit:
        last = page[-1]
        following = encode_cursor(Cursor(last.occurred_at, last.id))
    return EventPage(events=[_view(event, names) for event in page], next_cursor=following)


@router.get("/facets")
async def facets(staff: AuditStaff, database: Db) -> Facets:
    """The actions and record types on the trail, for the filter lists."""
    async with database.actor_transaction(staff.actor) as session:
        actions, kinds = await audit.known_names(session)
    return Facets(actions=actions, resource_types=kinds)


@router.get(
    "/events.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, 422: {"model": ErrorEnvelope}},
)
async def export_events(staff: AuditStaff, database: Db, clock: Time, filters: Filters) -> Response:
    """The filtered trail as a CSV file, newest first. Refused over 10,000 events."""
    async with database.actor_transaction(staff.actor) as session:
        found = await audit.search_events(
            session, filters, limit=EXPORT_LIMIT + 1, max_limit=EXPORT_LIMIT + 1
        )
        if len(found) > EXPORT_LIMIT:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["query"], code="too_many_events")],
            )
        names = await staff_names(session, _staff_ids(found))
        await audit.record(
            session,
            AuditEvent(
                category="access",
                action="audit.trail_exported",
                resource_type="audit_trail",
                actor=AuditActor.staff(staff.staff_id),
                details={"events": len(found), "filters": _described(filters)},
            ),
            clock,
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for event in found:
        writer.writerow(
            [
                csv_cell(value)
                for value in (
                    event.occurred_at.isoformat(),
                    event.category,
                    event.action,
                    event.outcome,
                    event.actor_kind,
                    event.actor_id or "",
                    names.get(event.actor_id or "", ""),
                    event.resource_type,
                    event.resource_id or "",
                    event.reason or "",
                    event.correlation_id or "",
                    json.dumps(event.details, separators=(",", ":"), sort_keys=True),
                )
            ]
        )
    stamp = clock.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="finvest-audit-{stamp}.csv"'},
    )


def _cursor(value: str | None) -> Cursor | None:
    if value is None:
        return None
    try:
        return decode_cursor(value)
    except InvalidFilter:
        raise _invalid("cursor") from None


def _staff_ids(events: list[audit.AuditEventView]) -> set[str]:
    return {event.actor_id for event in events if event.actor_kind == "staff" and event.actor_id}


def _described(filters: AuditFilter) -> dict[str, str]:
    described: dict[str, str] = {}
    for name in AuditFilter.__slots__:
        value = getattr(filters, name)
        if value is not None:
            described[name] = value.isoformat() if isinstance(value, datetime) else str(value)
    return described


def _view(event: audit.AuditEventView, names: dict[str, str]) -> EventView:
    return EventView(
        id=event.id,
        occurred_at=event.occurred_at,
        category=event.category,
        action=event.action,
        outcome=event.outcome,
        actor_kind=event.actor_kind,
        actor_id=event.actor_id,
        actor_name=names.get(event.actor_id)
        if event.actor_kind == "staff" and event.actor_id
        else None,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        reason=event.reason,
        correlation_id=event.correlation_id,
        details=cast(dict[str, JsonValue], event.details),
    )


def _invalid(field: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["query", field], code="invalid")]
    )
