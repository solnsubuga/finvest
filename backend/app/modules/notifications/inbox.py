"""The notification centre: telling a client something, and what the client reads back.

`notify` runs inside the transaction of the thing it reports, so a client is told about a
payment exactly when the payment is recorded, and never about one that rolled back. The words
are fixed when the notification is written: a later change of wording does not rewrite what a
client was already told. Sending it by push, SMS or email is queued with it (see delivery.py).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args

from sqlalchemy import CursorResult, Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.delivery import queue_fan_out
from app.modules.notifications.domain.events import Category, event
from app.modules.notifications.domain.wording import Value, render
from app.modules.notifications.wording_store import active_wording
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id, validate_push_id

# The screen the app opens from a notification. The ID belongs to the module that owns it.
LinkKind = Literal[
    "deposit",
    "payout",
    "investment",
    "identity_check",
    "payout_destination",
    "statement",
    "security",
    "conversation",
    "complaint",
    "goal",
    "autoinvest_plan",
]
LINK_KINDS: tuple[LinkKind, ...] = get_args(LinkKind)
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
MAX_MARKED = 200


@dataclass(frozen=True, slots=True)
class Link:
    kind: LinkKind
    id: str


@dataclass(frozen=True, slots=True)
class NotificationView:
    id: str
    event: str
    category: Category
    title: str
    body: str
    link: Link | None
    created_at: datetime
    read_at: datetime | None


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[NotificationView, ...]
    unread: int
    # Pass as `before` for the next page. None on the last page.
    next_before: str | None


_INSERT = text(
    "INSERT INTO notifications (id, client_id, event, category, idempotency_key, title, body, "
    "sms, link_kind, link_id, created_at) VALUES (:id, :client_id, :event, :category, :key, "
    ":title, :body, :sms, :link_kind, :link_id, :now) "
    "ON CONFLICT ON CONSTRAINT uq_notifications_client_event_key DO NOTHING"
)
_COLUMNS = "id, event, category, title, body, link_kind, link_id, created_at, read_at"
_PAGE = text(
    f"SELECT {_COLUMNS} FROM notifications WHERE client_id = :client_id "  # noqa: S608
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)
_PAGE_BEFORE = text(
    f"SELECT {_COLUMNS} FROM notifications WHERE client_id = :client_id "  # noqa: S608
    "AND (created_at, id) < (SELECT created_at, id FROM notifications "
    "WHERE id = :before AND client_id = :client_id) "
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)
_UNREAD = text(
    "SELECT count(*) FROM notifications WHERE client_id = :client_id AND read_at IS NULL"
)
_MARK_ALL = text(
    "UPDATE notifications SET read_at = :now WHERE client_id = :client_id AND read_at IS NULL"
)
_MARK = text(
    "UPDATE notifications SET read_at = :now WHERE client_id = :client_id AND read_at IS NULL "
    "AND id = ANY(CAST(:ids AS text[]))"
)


async def notify(
    session: AsyncSession,
    *,
    event_name: str,
    client_id: str,
    idempotency_key: str,
    variables: Mapping[str, Value],
    now: datetime,
    link: Link | None = None,
) -> str | None:
    """Tell one client that `event_name` happened. Call it in the transaction where it did.

    Returns the notification's ID, or None when this client was already told this event with
    this key: calling again is safe and writes nothing. The key is scoped to the client and
    the event, so a key such as a day or a statement period still tells every client.
    Raises `UnknownEvent` or `InvalidVariables` for a programming error, before anything is
    written.

    The transaction must act for that client, for a member of staff, or for the system in the
    worker. The row policies refuse anything else.

    Text values come from fixed lists or Finvest's own records, never from what a request
    sent: a device is "a new phone", not a user agent. A link in a value is removed, and
    nothing a value holds can make the insert fail, because that would roll back the caller.
    """
    if not session.in_transaction():
        raise RuntimeError("A notification is written inside the business transaction")
    spec = event(event_name)
    validate_push_id(client_id)
    if link is not None and link.kind not in LINK_KINDS:
        raise ValueError("Unknown link kind")
    rendered = render(spec, await active_wording(session, event_name), variables)
    notification_id = push_id()
    # No RETURNING: an insert that is skipped as a repeat returns no row either way.
    result: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
        _INSERT,
        {
            "id": notification_id,
            "client_id": client_id,
            "event": spec.name,
            "category": spec.category.value,
            "key": idempotency_key,
            "title": rendered.title,
            "body": rendered.body,
            "sms": rendered.sms,
            "link_kind": link.kind if link else None,
            "link_id": link.id if link else None,
            "now": now,
        },
    )
    if result.rowcount != 1:
        return None
    # Push, SMS and email follow from the worker, as the client chose. Only a new
    # notification is sent, so calling again never sends anything twice.
    await queue_fan_out(session, notification_id, now)
    return notification_id


async def page_for_client(
    session: AsyncSession, client_id: str, *, before: str | None = None, limit: int | None = None
) -> Page:
    """A client's notifications, newest first, with how many are unread in all."""
    size = max(1, min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE))
    parameters: dict[str, object] = {"client_id": client_id, "limit": size + 1}
    if before is None:
        rows = (await session.execute(_PAGE, parameters)).all()
    else:
        rows = (await session.execute(_PAGE_BEFORE, {**parameters, "before": before})).all()
    unread = (await session.execute(_UNREAD, {"client_id": client_id})).scalar_one()
    items = tuple(_view(row) for row in rows[:size])
    return Page(items, int(unread), items[-1].id if len(rows) > size else None)


async def mark_read(
    session: AsyncSession, client: Actor, ids: Sequence[str] | None, now: datetime
) -> int:
    """Mark the named notifications read, or all of them when `ids` is None.

    Returns how many are still unread. IDs that are not this client's are ignored.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    if ids is None:
        await session.execute(_MARK_ALL, {"client_id": client.id, "now": now})
    elif ids:
        await session.execute(
            _MARK, {"client_id": client.id, "now": now, "ids": list(ids)[:MAX_MARKED]}
        )
    return int((await session.execute(_UNREAD, {"client_id": client.id})).scalar_one())


def _view(row: Row[Any]) -> NotificationView:
    return NotificationView(
        id=row.id,
        event=row.event,
        category=Category(row.category),
        title=row.title,
        body=row.body,
        link=Link(row.link_kind, row.link_id) if row.link_kind else None,
        created_at=row.created_at,
        read_at=row.read_at,
    )
