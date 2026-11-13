"""Closing the ledger through a day, inside the caller's transaction.

The database is the authority (migration 0014): it refuses to close today or a future day, to
move the closed day backwards, and to accept an entry dated in a closed day. Who may sign a day
off, and how many signatures that takes, is the reconciliation module's business. This module
records who made the call.
"""

from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.ledger.domain.closing import ClosedDay, check_can_close, closing_staff_id
from app.modules.ledger.domain.errors import DayNotEnded
from app.shared.actors import Actor
from app.shared.clock import Clock

_SELECT_LATEST = text(
    "SELECT closed_through, closed_by, closed_at FROM ledger_closed_days "
    "ORDER BY closed_through DESC LIMIT 1"
)
_INSERT = text(
    "INSERT INTO ledger_closed_days (closed_through, closed_by, closed_at) "
    "VALUES (:closed_through, :closed_by, :closed_at)"
)
_ALREADY_CLOSED = "ledger_closed_days_forward"
_NOT_ENDED = "ledger_closed_days_ended"


async def latest_close(session: AsyncSession) -> ClosedDay | None:
    """The most recent close, or None if no day has been closed.

    Staff and the worker can read it. A client transaction always gets None.
    """
    row = (await session.execute(_SELECT_LATEST)).one_or_none()
    if row is None:
        return None
    return ClosedDay(row.closed_through, row.closed_by, row.closed_at)


async def closed_through(session: AsyncSession) -> date | None:
    """The last day that is closed, or None."""
    latest = await latest_close(session)
    return latest.closed_through if latest is not None else None


async def close_through(
    session: AsyncSession,
    closer: Actor,
    day: date,
    clock: Clock,
    *,
    correlation_id: str | None = None,
) -> ClosedDay:
    """Close the ledger through `day`. From then on no entry can be dated on or before it.

    `closer` is the staff member whose transaction this is. Closing a day that is already closed
    changes nothing and returns the close that stands. Closing today or a future day raises
    `DayNotEnded`. This waits for other transactions that have posted entries to finish.
    """
    closed_by = closing_staff_id(closer)
    check_can_close(day, clock.now())
    for attempt in range(2):
        latest = await latest_close(session)
        if latest is not None and latest.closed_through >= day:
            return latest
        closed = ClosedDay(day, closed_by, clock.now())
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT,
                    {
                        "closed_through": closed.closed_through,
                        "closed_by": closed.closed_by,
                        "closed_at": closed.closed_at,
                    },
                )
        except IntegrityError as error:
            constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
            if constraint == _NOT_ENDED:
                raise DayNotEnded("Only a day that has ended can be closed") from None
            # Someone else closed this day or a later one while we waited: read it and return.
            if attempt == 0 and constraint in (_ALREADY_CLOSED, "pk_ledger_closed_days"):
                continue
            raise
        await audit.record(
            session,
            AuditEvent(
                category="operator_control",
                action="ledger.day_closed",
                resource_type="ledger_closed_day",
                resource_id=day.isoformat(),
                actor=AuditActor.staff(closed_by),
                reason="The day was signed off",
                correlation_id=correlation_id,
                details={"closed_through": day.isoformat()},
            ),
            clock,
        )
        return closed
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["ClosedDay", "close_through", "closed_through", "latest_close"]
