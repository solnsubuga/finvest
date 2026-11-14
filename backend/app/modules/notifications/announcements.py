"""Announcements: words staff send to every active client, agreed by two people.

One member of staff proposes the words with a reason. A different one approves or rejects
them, and the proposer may withdraw their own. Approving queues the sending in the same
transaction. The worker then tells every active client, a page at a time, as an ordinary
`announcement` notification, so each client's channel choices for news apply.

Migration 0102 holds the rules PostgreSQL enforces: the person who decides is never the person
who proposed, a rejection says why, and a decided announcement never changes.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    PermanentJobError,
    enqueue,
)
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity.service import active_client_ids, count_active_clients
from app.modules.notifications import service
from app.modules.notifications.domain.events import Category, Channel
from app.modules.notifications.domain.preferences import DEFAULTS
from app.modules.notifications.domain.wording import InvalidWording, check_announcement
from app.modules.notifications.inbox import notify
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "notification_announcement"
SEND = "notifications.announcement"
# Clients told in one job. Each page commits with its job, so a crash repeats one page at most,
# and repeating it tells nobody twice.
PAGE = 500
SEND_MAX_AGE = timedelta(days=2)
SEND_ATTEMPTS = 12
MAX_REASON = 500
LIST_LIMIT = 50
Decision = Literal["approved", "rejected", "withdrawn"]


@dataclass(frozen=True, slots=True)
class Announcement:
    id: str
    title: str
    message: str
    reason: str
    status: str
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    # Clients told so far. Zero until the worker starts on an approved one.
    told: int


_COLUMNS = (
    "a.id, a.title, a.message, a.reason, a.status, a.proposed_by, a.proposed_at, "
    "a.decided_by, a.decided_at, a.decision_reason"
)
_TOLD = (
    "(SELECT count(*) FROM notifications n WHERE n.event = 'announcement' "
    "AND n.idempotency_key = a.id) AS told"
)
_LIST = text(
    f"SELECT {_COLUMNS}, {_TOLD} FROM notification_announcements a "  # noqa: S608 - constants
    "ORDER BY a.proposed_at DESC, a.id DESC LIMIT :limit"
)
_OPEN_BY_ID = text(
    f"SELECT {_COLUMNS}, 0 AS told FROM notification_announcements a "  # noqa: S608 - constants
    "WHERE a.id = :id AND a.status = 'proposed' FOR UPDATE"
)
_ONE = text(
    f"SELECT {_COLUMNS}, {_TOLD} FROM notification_announcements a "  # noqa: S608 - constants
    "WHERE a.id = :id"
)
_INSERT = text(
    "INSERT INTO notification_announcements (id, title, message, reason, proposed_by, "
    "proposed_at) VALUES (:id, :title, :message, :reason, :staff, :now)"
)
_DECIDE = text(
    "UPDATE notification_announcements SET status = :status, decided_by = :staff, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)
_APPROVED = text("SELECT title, message FROM notification_announcements WHERE id = :id")


@dataclass(frozen=True, slots=True)
class Reach:
    """Who an announcement approved now would reach, so the approver knows before sending."""

    # Active clients. Every one gets it in the app, and by push if they have the app.
    clients: int
    # Of those, how many also get it by SMS and by email, as their choices for news say.
    sms: int
    email: int


# Clients who saved a choice for news, with whether it includes the channel.
_NEWS_CHOICES = text(
    "SELECT client_id, CAST(:channel AS text) = ANY(channels) AS chosen "
    "FROM notification_preferences WHERE category = :category"
)


async def reach(session: AsyncSession) -> Reach:
    """How many clients an announcement approved now would go to, and by SMS and email."""
    clients = await count_active_clients(session)

    async def by(channel: Channel) -> int:
        rows = (
            await session.execute(
                _NEWS_CHOICES, {"channel": channel.value, "category": Category.NEWS.value}
            )
        ).all()
        if channel in DEFAULTS[Category.NEWS]:
            # Everyone, less those who switched it off.
            off = [row.client_id for row in rows if not row.chosen]
            return clients - (await count_active_clients(session, off) if off else 0)
        on = [row.client_id for row in rows if row.chosen]
        return await count_active_clients(session, on) if on else 0

    return Reach(clients=clients, sms=await by(Channel.SMS), email=await by(Channel.EMAIL))


async def list_announcements(session: AsyncSession) -> list[Announcement]:
    """The most recent announcements, newest first, with how many clients each has reached."""
    rows = await session.execute(_LIST, {"limit": LIST_LIMIT})
    return [_announcement(row) for row in rows]


PROPOSE_OPERATION = "notifications.announcement_propose"


async def propose(
    session: AsyncSession,
    staff: Actor,
    title: str,
    message: str,
    reason: str,
    clock: Clock,
    *,
    idempotency_key: str,
) -> Announcement:
    """Put an announcement forward. Nothing is sent until someone else approves it.

    The same key with the same words answers with the announcement already proposed, so a
    retry after a lost answer never puts two forward.
    """

    async def command() -> Receipt:
        made = await _propose(session, staff, title, message, reason, clock)
        return Receipt(201, {"announcement_id": made.id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={"title": title, "message": message, "reason": reason},
        now=clock.now(),
        command=command,
    )
    found = (
        await session.execute(_ONE, {"id": str(outcome.receipt.response["announcement_id"])})
    ).one()
    return _announcement(found)


async def _propose(
    session: AsyncSession, staff: Actor, title: str, message: str, reason: str, clock: Clock
) -> Announcement:
    reason = _reason(reason, "reason")
    try:
        check_announcement(title, message)
    except InvalidWording as error:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=[field], code=code) for field, code in error.problems],
        ) from None
    announcement = Announcement(
        id=push_id(clock),
        title=title,
        message=message,
        reason=reason,
        status="proposed",
        proposed_by=_staff_id(staff),
        proposed_at=clock.now(),
        decided_by=None,
        decided_at=None,
        decision_reason=None,
        told=0,
    )
    await session.execute(
        _INSERT,
        {
            "id": announcement.id,
            "title": title,
            "message": message,
            "reason": reason,
            "staff": announcement.proposed_by,
            "now": announcement.proposed_at,
        },
    )
    await _audit(session, staff, "notifications.announcement_proposed", announcement, reason, clock)
    return announcement


async def decide(
    session: AsyncSession,
    staff: Actor,
    announcement_id: str,
    decision: Decision,
    reason: str | None,
    clock: Clock,
) -> Announcement:
    """Approve or reject someone else's announcement, or withdraw your own.

    Approving queues the sending in this transaction, so an approved announcement is always
    sent and a rolled-back approval sends nothing.
    """
    cleaned = _reason(reason or "", "reason") if decision == "rejected" or reason else None
    row = (await session.execute(_OPEN_BY_ID, {"id": announcement_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    proposed = _announcement(row)
    own = proposed.proposed_by == _staff_id(staff)
    if own != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    now = clock.now()
    await session.execute(
        _DECIDE,
        {"status": decision, "staff": staff.id, "now": now, "reason": cleaned, "id": row.id},
    )
    decided = replace(
        proposed, status=decision, decided_by=staff.id, decided_at=now, decision_reason=cleaned
    )
    if decision == "approved":
        await _queue_page(session, decided.id, None, now)
    # An approval with no words of its own is recorded with the reason it was proposed for.
    await _audit(
        session,
        staff,
        f"notifications.announcement_{decision}",
        decided,
        cleaned or proposed.reason,
        clock,
    )
    return decided


async def send_page(
    session: AsyncSession, announcement_id: str, after: str | None, clock: Clock
) -> int:
    """Tell one page of active clients, in the worker's transaction. Returns how many were told.

    A full page queues the next one in the same transaction. The notification key is the
    announcement's ID, so a page run twice tells nobody twice.
    """
    row = (await session.execute(_APPROVED, {"id": announcement_id})).one_or_none()
    if row is None:
        # Only an approved announcement is visible to the worker.
        raise ApplicationError("not_found")
    now = clock.now()
    clients = await active_client_ids(session, after=after, limit=PAGE)
    told = 0
    for client_id in clients:
        written = await notify(
            session,
            event_name="announcement",
            client_id=client_id,
            idempotency_key=announcement_id,
            variables={"title": row.title, "message": row.message},
            now=now,
        )
        told += written is not None
    if len(clients) == PAGE:
        await _queue_page(session, announcement_id, clients[-1], now)
    return told


class AnnouncementJobs:
    """The worker's side: tell one page of clients per job. Nothing here calls a provider."""

    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {SEND: self.send}

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        announcement_id, after = job.payload.get("announcement_id"), job.payload.get("after")
        if not isinstance(announcement_id, str) or not (after is None or isinstance(after, str)):
            raise PermanentJobError(code="announcement_missing")

        async def effect(session: AsyncSession) -> dict[str, object]:
            try:
                told = await send_page(session, announcement_id, after, self._clock)
            except ApplicationError:
                raise PermanentJobError(code="announcement_missing") from None
            return {"told": told}

        await context.apply(effect)


async def _queue_page(
    session: AsyncSession, announcement_id: str, after: str | None, now: datetime
) -> None:
    await enqueue(
        session,
        JobRequest(
            kind=SEND,
            idempotency_key=f"announcement:{announcement_id}:{after or 'start'}",
            payload={"announcement_id": announcement_id, "after": after},
            queue=service.MESSAGE_QUEUE,
            max_age=SEND_MAX_AGE,
            max_attempts=SEND_ATTEMPTS,
        ),
        now=now,
    )


async def _audit(
    session: AsyncSession,
    staff: Actor,
    action: str,
    announcement: Announcement,
    reason: str,
    clock: Clock,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "rejected")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=announcement.id,
            actor=AuditActor.staff(_staff_id(staff)),
            reason=reason or None,
            details={},
        ),
        clock,
    )


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _reason(reason: str, field: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=[field], code="required")]
        )
    return cleaned


def _announcement(row: Row[Any]) -> Announcement:
    return Announcement(
        id=row.id,
        title=row.title,
        message=row.message,
        reason=row.reason,
        status=row.status,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        told=row.told,
    )
