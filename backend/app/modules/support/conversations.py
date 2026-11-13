"""Help chat commands and queries. Every function works inside the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here. The row
policies of migration 0100 then hold whatever this code does: a client reaches only their own
chat and writes only under their own name.

There is no live connection. The app and the console ask for what is new every few seconds.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications import contract as notifications
from app.modules.support.domain.chat import (
    MAX_SAVED_TITLE,
    TOPICS,
    AuthorKind,
    ConversationStatus,
    InvalidMessage,
    Topic,
    check_key,
    clean_message,
    first_name,
)
from app.shared.actors import Actor
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200
PREVIEW = 140


@dataclass(frozen=True, slots=True)
class MessageView:
    id: str
    conversation_id: str
    author_kind: AuthorKind
    # A member of staff's first name. None for the client's own messages.
    author_name: str | None
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationView:
    id: str
    client_id: str
    topic: Topic
    status: ConversationStatus
    created_at: datetime
    last_message_at: datetime
    # Since when the client has been waiting for an answer. None when staff wrote last.
    waiting_since: datetime | None
    solved_at: datetime | None


@dataclass(frozen=True, slots=True)
class InboxItem:
    conversation: ConversationView
    last_message: str
    last_author: AuthorKind
    unread: int


@dataclass(frozen=True, slots=True)
class SavedReply:
    id: str
    title: str
    body: str


_CONVERSATION = (
    "id, client_id, topic, status, created_at, last_message_at, waiting_since, solved_at"
)
_MESSAGE = "id, conversation_id, author_kind, author_name, body, created_at"
_OPEN_FOR_CLIENT = text(
    f"SELECT {_CONVERSATION} FROM support_conversations "  # noqa: S608 - constant column list
    "WHERE client_id = :client_id AND status = 'open' FOR UPDATE"
)
_LATEST_FOR_CLIENT = text(
    f"SELECT {_CONVERSATION} FROM support_conversations WHERE client_id = :client_id "  # noqa: S608
    "ORDER BY (status = 'open') DESC, created_at DESC, id DESC LIMIT 1"
)
_BY_ID = text(f"SELECT {_CONVERSATION} FROM support_conversations WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(
    f"SELECT {_CONVERSATION} FROM support_conversations WHERE id = :id FOR UPDATE"  # noqa: S608
)
_OPEN_CONVERSATION = text(
    "INSERT INTO support_conversations (id, client_id, topic, created_at, last_message_at) "
    "VALUES (:id, :client_id, :topic, :now, :now)"
)
_BY_KEY = text(
    f"SELECT {_MESSAGE} FROM support_messages "  # noqa: S608 - constant column list
    "WHERE author_id = :author AND client_key = :key"
)
_INSERT_MESSAGE = text(
    "INSERT INTO support_messages (id, conversation_id, author_kind, author_id, author_name, "
    "body, client_key, created_at) VALUES (:id, :conversation, :kind, :author, :name, :body, "
    ":key, :now)"
)
_CLIENT_WROTE = text(
    "UPDATE support_conversations SET last_message_at = :now, client_read_at = :now, "
    "waiting_since = COALESCE(waiting_since, :now) WHERE id = :id"
)
_STAFF_WROTE = text(
    "UPDATE support_conversations SET last_message_at = :now, staff_read_at = :now, "
    "waiting_since = NULL WHERE id = :id"
)
_CLIENT_MESSAGES = text(
    "SELECT m.id, m.conversation_id, m.author_kind, m.author_name, m.body, m.created_at "
    "FROM support_messages m JOIN support_conversations c ON c.id = m.conversation_id "
    "WHERE c.client_id = :client_id "
    "AND (CAST(:after AS text) IS NULL OR (m.created_at, m.id) > "
    "(SELECT created_at, id FROM support_messages WHERE id = :after)) "
    "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
)
_CLIENT_UNREAD = text(
    "SELECT count(*) FROM support_messages m JOIN support_conversations c "
    "ON c.id = m.conversation_id WHERE c.client_id = :client_id AND m.author_kind = 'staff' "
    "AND (c.client_read_at IS NULL OR m.created_at > c.client_read_at)"
)
_CLIENT_READ = text(
    "UPDATE support_conversations SET client_read_at = :now "
    "WHERE client_id = :client_id AND status = 'open'"
)
_STAFF_READ = text(
    "UPDATE support_conversations SET staff_read_at = :now WHERE id = :id AND status = 'open'"
)
_CONVERSATION_MESSAGES = text(
    f"SELECT {_MESSAGE} FROM support_messages WHERE conversation_id = :id "  # noqa: S608
    "AND (CAST(:after AS text) IS NULL OR (created_at, id) > "
    "(SELECT created_at, id FROM support_messages WHERE id = :after)) "
    "ORDER BY created_at, id LIMIT :limit"
)
_INBOX = text(
    "SELECT c.id, c.client_id, c.topic, c.status, c.created_at, c.last_message_at, "
    "c.waiting_since, c.solved_at, last.body AS last_body, last.author_kind AS last_author, "
    "(SELECT count(*) FROM support_messages m WHERE m.conversation_id = c.id "
    "AND m.author_kind = 'client' AND (c.staff_read_at IS NULL OR m.created_at > c.staff_read_at)"
    ") AS unread FROM support_conversations c JOIN LATERAL (SELECT body, author_kind "
    "FROM support_messages m WHERE m.conversation_id = c.id "
    "ORDER BY m.created_at DESC, m.id DESC LIMIT 1) last ON true WHERE c.status = :status "
    # Whoever has waited longest is first. Chats that staff answered last come after.
    "ORDER BY c.waiting_since ASC NULLS LAST, c.last_message_at DESC, c.id LIMIT :limit"
)
_SOLVE = text(
    "UPDATE support_conversations SET status = 'solved', solved_at = :now, solved_by = :staff, "
    "waiting_since = NULL WHERE id = :id"
)
_SET_TOPIC = text("UPDATE support_conversations SET topic = :topic WHERE id = :id")
_SAVED = text(
    "SELECT id, title, body FROM support_saved_replies WHERE retired_at IS NULL ORDER BY title, id"
)
_SAVE_REPLY = text(
    "INSERT INTO support_saved_replies (id, title, body, created_by, created_at) "
    "VALUES (:id, :title, :body, :staff, :now)"
)
_RETIRE_REPLY = text(
    "UPDATE support_saved_replies SET retired_at = :now, retired_by = :staff "
    "WHERE id = :id AND retired_at IS NULL"
)


async def send_as_client(
    session: AsyncSession,
    client: Actor,
    body: str,
    key: str,
    now: datetime,
    *,
    topic: Topic = "other",
) -> MessageView:
    """Add the client's message to their open chat, opening one if there is none.

    Sending the same `key` again returns the message already stored.
    """
    client_id = _person(client, "client")
    cleaned, key = _checked(body, key)
    if topic not in TOPICS:
        raise _invalid("topic", "unknown")
    repeat = (await session.execute(_BY_KEY, {"author": client_id, "key": key})).one_or_none()
    if repeat is not None:
        return _message(repeat)
    conversation = (await session.execute(_OPEN_FOR_CLIENT, {"client_id": client_id})).one_or_none()
    if conversation is None:
        try:
            async with session.begin_nested():
                await session.execute(
                    _OPEN_CONVERSATION,
                    {"id": push_id(), "client_id": client_id, "topic": topic, "now": now},
                )
        except IntegrityError:
            # Another request of this client opened the chat a moment ago. Use that one.
            pass
        conversation = (await session.execute(_OPEN_FOR_CLIENT, {"client_id": client_id})).one()
    message = await _add(session, conversation.id, "client", client_id, None, cleaned, key, now)
    await session.execute(_CLIENT_WROTE, {"id": conversation.id, "now": now})
    return message


async def send_as_staff(
    session: AsyncSession,
    staff: Actor,
    staff_name: str,
    conversation_id: str,
    body: str,
    key: str,
    now: datetime,
) -> MessageView:
    """Answer a client in their open chat, and tell them there is a reply."""
    staff_id = _person(staff, "staff")
    cleaned, key = _checked(body, key)
    repeat = (await session.execute(_BY_KEY, {"author": staff_id, "key": key})).one_or_none()
    if repeat is not None:
        return _message(repeat)
    conversation = await _open(session, conversation_id)
    message = await _add(
        session, conversation.id, "staff", staff_id, first_name(staff_name), cleaned, key, now
    )
    await session.execute(_STAFF_WROTE, {"id": conversation.id, "now": now})
    # The notification says only that there is a reply. The words stay in the chat.
    await notifications.notify(
        session,
        event_name="support_reply",
        client_id=conversation.client_id,
        idempotency_key=message.id,
        variables={},
        link=notifications.Link("conversation", conversation.id),
        now=now,
    )
    return message


async def conversation_for_client(session: AsyncSession, client_id: str) -> ConversationView | None:
    """The client's open chat, or their last one if none is open."""
    row = (await session.execute(_LATEST_FOR_CLIENT, {"client_id": client_id})).one_or_none()
    return _conversation(row) if row is not None else None


async def messages_for_client(
    session: AsyncSession, client_id: str, *, after: str | None = None, limit: int | None = None
) -> tuple[list[MessageView], int]:
    """The client's messages across all their chats, oldest first, and how many are unread.

    With `after`, only messages newer than that one: what the app asks for when it polls.
    Without it, the most recent `limit`.
    """
    rows = await session.execute(
        _CLIENT_MESSAGES, {"client_id": client_id, "after": after, "limit": _bounded(limit)}
    )
    unread = (await session.execute(_CLIENT_UNREAD, {"client_id": client_id})).scalar_one()
    return [_message(row) for row in reversed(rows.all())], int(unread)


async def mark_read_by_client(session: AsyncSession, client: Actor, now: datetime) -> None:
    await session.execute(_CLIENT_READ, {"client_id": _person(client, "client"), "now": now})


async def inbox(
    session: AsyncSession, *, status: ConversationStatus = "open", limit: int | None = None
) -> list[InboxItem]:
    """Chats for staff, the longest wait first."""
    rows = await session.execute(_INBOX, {"status": status, "limit": _bounded(limit)})
    return [
        InboxItem(
            conversation=_conversation(row),
            last_message=row.last_body[:PREVIEW],
            last_author=row.last_author,
            unread=int(row.unread),
        )
        for row in rows.all()
    ]


async def conversation_for_staff(
    session: AsyncSession,
    staff: Actor,
    conversation_id: str,
    now: datetime,
    *,
    after: str | None = None,
    limit: int | None = None,
) -> tuple[ConversationView, list[MessageView]]:
    """One chat and its messages, oldest first. Opening it marks it read for staff."""
    _person(staff, "staff")
    row = (await session.execute(_BY_ID, {"id": conversation_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    rows = await session.execute(
        _CONVERSATION_MESSAGES, {"id": conversation_id, "after": after, "limit": _bounded(limit)}
    )
    await session.execute(_STAFF_READ, {"id": conversation_id, "now": now})
    return _conversation(row), [_message(message) for message in rows.all()]


async def solve(
    session: AsyncSession, staff: Actor, conversation_id: str, now: datetime
) -> ConversationView:
    """Close the chat. If the client writes again, a new chat opens."""
    staff_id = _person(staff, "staff")
    conversation = await _open(session, conversation_id)
    await session.execute(_SOLVE, {"id": conversation.id, "staff": staff_id, "now": now})
    return _conversation((await session.execute(_BY_ID, {"id": conversation.id})).one())


async def set_topic(
    session: AsyncSession, staff: Actor, conversation_id: str, topic: Topic
) -> ConversationView:
    _person(staff, "staff")
    if topic not in TOPICS:
        raise _invalid("topic", "unknown")
    conversation = await _open(session, conversation_id)
    await session.execute(_SET_TOPIC, {"id": conversation.id, "topic": topic})
    return _conversation((await session.execute(_BY_ID, {"id": conversation.id})).one())


async def saved_replies(session: AsyncSession) -> list[SavedReply]:
    rows = await session.execute(_SAVED)
    return [SavedReply(row.id, row.title, row.body) for row in rows.all()]


async def save_reply(
    session: AsyncSession, staff: Actor, title: str, body: str, now: datetime
) -> SavedReply:
    staff_id = _person(staff, "staff")
    title = " ".join(title.split())
    if not title or len(title) > MAX_SAVED_TITLE:
        raise _invalid("title", "required" if not title else "too_long")
    try:
        cleaned = clean_message(body)
    except InvalidMessage as error:
        raise _invalid(error.field, error.code) from None
    reply = SavedReply(push_id(), title, cleaned)
    await session.execute(
        _SAVE_REPLY,
        {"id": reply.id, "title": title, "body": cleaned, "staff": staff_id, "now": now},
    )
    return reply


async def retire_reply(session: AsyncSession, staff: Actor, reply_id: str, now: datetime) -> None:
    result = await session.execute(
        _RETIRE_REPLY, {"id": reply_id, "staff": _person(staff, "staff"), "now": now}
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise ApplicationError("not_found")


async def _open(session: AsyncSession, conversation_id: str) -> Row[Any]:
    row = (await session.execute(_BY_ID_LOCKED, {"id": conversation_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    if row.status != "open":
        # Solved. The client's next message opens a new chat.
        raise ApplicationError("conflict")
    return row


async def _add(
    session: AsyncSession,
    conversation_id: str,
    kind: AuthorKind,
    author_id: str,
    name: str | None,
    body: str,
    key: str,
    now: datetime,
) -> MessageView:
    message = MessageView(push_id(), conversation_id, kind, name, body, now)
    await session.execute(
        _INSERT_MESSAGE,
        {
            "id": message.id,
            "conversation": conversation_id,
            "kind": kind,
            "author": author_id,
            "name": name,
            "body": body,
            "key": key,
            "now": now,
        },
    )
    return message


def _checked(body: str, key: str) -> tuple[str, str]:
    try:
        return clean_message(body), check_key(key)
    except InvalidMessage as error:
        raise _invalid(error.field, error.code) from None


def _person(actor: Actor, kind: AuthorKind) -> str:
    if actor.kind != kind or actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _bounded(limit: int | None) -> int:
    return max(1, min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE))


def _conversation(row: Row[Any]) -> ConversationView:
    return ConversationView(
        id=row.id,
        client_id=row.client_id,
        topic=row.topic,
        status=row.status,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
        waiting_since=row.waiting_since,
        solved_at=row.solved_at,
    )


def _message(row: Row[Any]) -> MessageView:
    return MessageView(
        id=row.id,
        conversation_id=row.conversation_id,
        author_kind=row.author_kind,
        author_name=row.author_name,
        body=row.body,
        created_at=row.created_at,
    )
