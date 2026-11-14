"""Staff writing to a client first, against real PostgreSQL: the client's open chat is used or
one is opened, the client is told, and a client who closed their account is never written to."""

import asyncio
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.identity.service import create_unclaimed_client
from app.modules.notifications import inbox
from app.modules.support import conversations
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier
from tests.support.investments import PRESENT
from tests.support.ledger import client as unknown_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

FOLLOW_UP = "Your investment has ended. Tell us where to send the money and we will send it."


async def approved_client(app_database: Database, _: object) -> Actor:
    """A client with a sign-in: one carried over from Finvest 1.0, which counts as a client."""
    staff = new_staff()
    phone = f"+2567720{secrets.randbelow(100_000):05d}"
    async with app_database.actor_transaction(staff) as session:
        client_id = await create_unclaimed_client(session, PRESENT, staff, phone=phone, email=None)
    return Actor("client", client_id)


def key() -> str:
    return f"msg-{push_id()}"


async def write(
    app_database: Database, client_id: str, body: str = FOLLOW_UP, message_key: str | None = None
) -> conversations.MessageView:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        return await conversations.write_to_client(
            session,
            staff,
            "Grace Auma",
            client_id,
            body,
            message_key or key(),
            PRESENT.now(),
            topic="withdrawal",
        )


async def test_staff_open_a_chat_and_the_client_is_told(app_database: Database) -> None:
    client = await approved_client(app_database, PRESENT)
    assert client.id is not None

    message = await write(app_database, client.id)

    assert (message.author_kind, message.author_name, message.body) == (
        "staff",
        "Grace",
        FOLLOW_UP,
    )
    async with app_database.actor_transaction(client) as session:
        view = await conversations.conversation_for_client(session, client.id)
        messages, unread = await conversations.messages_for_client(session, client.id)
        told = list((await inbox.page_for_client(session, client.id)).items)
    assert view is not None and view.id == message.conversation_id
    # Nobody is waiting on staff: staff wrote first.
    assert (view.topic, view.waiting_since) == ("withdrawal", None)
    assert [m.body for m in messages] == [FOLLOW_UP] and unread == 1
    assert [n.event for n in told] == ["support_reply"]


async def test_staff_write_into_the_chat_the_client_already_has(app_database: Database) -> None:
    client = await approved_client(app_database, PRESENT)
    assert client.id is not None
    async with app_database.actor_transaction(client) as session:
        theirs = await conversations.send_as_client(session, client, "Hello", key(), PRESENT.now())

    message = await write(app_database, client.id)

    assert message.conversation_id == theirs.conversation_id


async def test_the_same_message_sent_twice_is_stored_once(app_database: Database) -> None:
    client = await approved_client(app_database, PRESENT)
    assert client.id is not None
    once = key()
    staff = new_staff()
    for _ in range(2):
        async with app_database.actor_transaction(staff) as session:
            stored = await conversations.write_to_client(
                session, staff, "Grace Auma", client.id, FOLLOW_UP, once, PRESENT.now()
            )
    async with app_database.actor_transaction(client) as session:
        messages, _ = await conversations.messages_for_client(session, client.id)
    assert [m.id for m in messages] == [stored.id]


async def test_staff_and_the_client_writing_first_at_once_open_one_chat(
    app_database: Database, concurrency_barrier: AsyncBarrier
) -> None:
    client = await approved_client(app_database, PRESENT)
    assert client.id is not None
    client_id = client.id

    async def by_client() -> str:
        async with app_database.actor_transaction(client) as session:
            await concurrency_barrier.wait()
            sent = await conversations.send_as_client(session, client, "Hi", key(), PRESENT.now())
        return sent.conversation_id

    async def by_staff() -> str:
        staff = new_staff()
        async with app_database.actor_transaction(staff) as session:
            await concurrency_barrier.wait()
            sent = await conversations.write_to_client(
                session, staff, "Grace Auma", client_id, FOLLOW_UP, key(), PRESENT.now()
            )
        return sent.conversation_id

    assert len(set(await asyncio.gather(by_client(), by_staff()))) == 1


async def test_nobody_is_written_to_who_is_not_a_client(app_database: Database) -> None:
    stranger = unknown_client()
    assert stranger.id is not None
    with pytest.raises(ApplicationError) as refused:
        await write(app_database, stranger.id)
    assert refused.value.code == "not_found"


async def test_a_chat_staff_open_cannot_jump_the_queue_or_start_solved(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, PRESENT)
    staff = new_staff()
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO support_conversations (id, client_id, created_at, "
                    "last_message_at, waiting_since) VALUES (:id, :client, now(), now(), now())"
                ),
                {"id": push_id(), "client": client.id},
            )
