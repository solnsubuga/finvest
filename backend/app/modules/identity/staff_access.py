"""Keeping the Cloudflare Access list for the console in step with the team.

The API asks, in the transaction that approves a change to someone, for the list to be brought in
line: it marks them `waiting` and queues a job. The worker (staff_access_jobs.py) does the
Cloudflare calls with no transaction open and records here what the list said. A daily check
queues the same job for everyone, so anyone added before this existed, or changed by hand in
Cloudflare, is put right. See docs/console-admin.md#cloudflare-access.

Job payloads carry the staff ID only. The worker reads the address when it runs, and logs never
carry it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, RetryableJobError, enqueue
from app.modules.identity.domain.staff_access import (
    AccessState,
    Target,
    belongs_on_list,
    settled_state,
)

SYNC_KIND = "identity.staff_access.sync"
CHECK_KIND = "identity.staff_access.check"
# Eight attempts back off from seconds to fifteen minutes, well inside the day.
_MAX_ATTEMPTS = 8
_MAX_AGE = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class AccessStatus:
    state: AccessState
    error_code: str | None
    requested_at: datetime
    synced_at: datetime | None


_MARK_WAITING = text(
    "INSERT INTO identity_staff_access (staff_id, state, requested_at, updated_at) "
    "VALUES (:staff_id, 'waiting', :now, :now) "
    "ON CONFLICT (staff_id) DO UPDATE SET state = 'waiting', requested_at = :now, "
    "error_code = NULL, updated_at = :now"
)
_ADD_IF_NEW = text(
    "INSERT INTO identity_staff_access (staff_id, state, requested_at, updated_at) "
    "VALUES (:staff_id, 'waiting', :now, :now) ON CONFLICT (staff_id) DO NOTHING"
)


async def request_sync(
    session: AsyncSession,
    staff_id: str,
    *,
    key: str,
    now: datetime,
    mark_waiting: bool = True,
) -> None:
    """Queue bringing the list in line for one member of staff, in the caller's transaction.

    `key` names what asked, such as the approved change, so each ask is its own job. The daily
    check leaves a settled state as it is (`mark_waiting=False`) and only adds missing rows.
    """
    await session.execute(
        _MARK_WAITING if mark_waiting else _ADD_IF_NEW, {"staff_id": staff_id, "now": now}
    )
    await enqueue(
        session,
        JobRequest(
            kind=SYNC_KIND,
            idempotency_key=key,
            payload={"staff_id": staff_id},
            max_attempts=_MAX_ATTEMPTS,
            max_age=_MAX_AGE,
        ),
        now=now,
    )


async def statuses(session: AsyncSession) -> dict[str, AccessStatus]:
    """Where each member of staff stands, for the team view. Missing: never asked yet."""
    rows = await session.execute(
        text(
            "SELECT staff_id, state, error_code, requested_at, synced_at FROM identity_staff_access"
        )
    )
    return {
        row.staff_id: AccessStatus(
            state=AccessState(row.state),
            error_code=row.error_code,
            requested_at=row.requested_at,
            synced_at=row.synced_at,
        )
        for row in rows
    }


async def status_of(session: AsyncSession, staff_id: str) -> AccessStatus | None:
    return (await statuses(session)).get(staff_id)


async def target(session: AsyncSession, staff_id: str) -> Target | None:
    """What the list should say about them now. Worker only."""
    row = (
        await session.execute(
            text("SELECT id, email, status FROM identity_staff WHERE id = :id"), {"id": staff_id}
        )
    ).one_or_none()
    if row is None:
        return None
    return Target(staff_id=row.id, email=row.email, on_list=belongs_on_list(row.status))


async def everyone(session: AsyncSession) -> list[str]:
    """Every member of staff ever added. Worker only."""
    return list((await session.scalars(text("SELECT id FROM identity_staff ORDER BY id"))).all())


async def record_settled(
    session: AsyncSession, staff_id: str, *, on_list: bool, now: datetime
) -> AccessState:
    """Record that the list says what the team said when the attempt started.

    If the team changed in the meantime the list may be wrong again, so the job runs again
    rather than record a state that is no longer true. The row lock orders this against the API
    marking the member waiting in the approval that changed them.
    """
    await session.execute(
        text("SELECT staff_id FROM identity_staff_access WHERE staff_id = :id FOR UPDATE"),
        {"id": staff_id},
    )
    now_target = await target(session, staff_id)
    if now_target is None or now_target.on_list != on_list:
        raise RetryableJobError(code="team_changed_meanwhile")
    state = settled_state(on_list)
    await session.execute(
        text(
            "UPDATE identity_staff_access SET state = :state, synced_at = :now, "
            "error_code = NULL, updated_at = :now WHERE staff_id = :id"
        ),
        {"state": state.value, "now": now, "id": staff_id},
    )
    return state


async def record_problem(
    session: AsyncSession,
    staff_id: str,
    *,
    state: AccessState,
    error_code: str | None,
    now: datetime,
) -> None:
    """Note why the list is not in line yet: still trying, failed, or not set up."""
    if state in (AccessState.IN_ACCESS, AccessState.REMOVED):
        raise ValueError("A settled state is recorded with record_settled")
    await session.execute(
        text(
            "UPDATE identity_staff_access SET state = :state, error_code = :code, "
            "updated_at = :now WHERE staff_id = :id"
        ),
        {"state": state.value, "code": error_code, "now": now, "id": staff_id},
    )
