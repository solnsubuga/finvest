"""Help chat and the complaints register against real PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.identity.domain.permissions import ROLE_PERMISSIONS
from app.modules.notifications import inbox
from app.modules.support import complaints, conversations
from app.modules.support.domain.complaints import Closure, NewComplaint, Outcome
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

# A Friday afternoon in Kampala.
FRIDAY = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
LATE = NewComplaint("withdrawals", "Payout did not arrive", "Requested on Tuesday.", "app")


def key() -> str:
    return f"msg-{push_id()}"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(FRIDAY)


async def say(database: Database, client: Actor, clock: FakeClock, body: str = "Hello") -> str:
    clock.advance(seconds=30)
    async with database.actor_transaction(client) as session:
        message = await conversations.send_as_client(session, client, body, key(), clock.now())
    return message.conversation_id


async def answer(
    database: Database, staff: Actor, conversation_id: str, clock: FakeClock, body: str = "Hi"
) -> conversations.MessageView:
    clock.advance(seconds=30)
    async with database.actor_transaction(staff) as session:
        return await conversations.send_as_staff(
            session, staff, "Grace Auma", conversation_id, body, key(), clock.now()
        )


async def chat(database: Database, client: Actor) -> tuple[list[conversations.MessageView], int]:
    async with database.actor_transaction(client) as session:
        return await conversations.messages_for_client(session, client.id or "")


async def notifications_of(database: Database, client: Actor) -> list[inbox.NotificationView]:
    async with database.actor_transaction(client) as session:
        return list((await inbox.page_for_client(session, client.id or "")).items)


# Help chat ----------------------------------------------------------------------------------------


async def test_a_client_and_staff_talk_and_the_client_is_told_of_each_reply(
    app_database: Database, clock: FakeClock
) -> None:
    client, staff = new_client(), new_staff()
    conversation_id = await say(app_database, client, clock, "My withdrawal says processing.")
    async with app_database.actor_transaction(staff) as session:
        (waiting,) = [
            item
            for item in await conversations.inbox(session)
            if item.conversation.id == conversation_id
        ]
    assert waiting.unread == 1 and waiting.last_author == "client"
    assert waiting.last_message == "My withdrawal says processing."
    assert waiting.conversation.waiting_since == clock.now()

    reply = await answer(app_database, staff, conversation_id, clock, "I can see it.")
    assert reply.author_name == "Grace"
    messages, unread = await chat(app_database, client)
    assert [(m.author_kind, m.author_name, m.body) for m in messages] == [
        ("client", None, "My withdrawal says processing."),
        ("staff", "Grace", "I can see it."),
    ]
    assert unread == 1
    (told,) = await notifications_of(app_database, client)
    assert (told.event, told.title) == ("support_reply", "New message from Finvest")
    # The notification points at the chat and never repeats what was said.
    assert told.link == inbox.Link("conversation", conversation_id)
    assert "I can see it" not in told.body

    clock.advance(seconds=30)
    async with app_database.actor_transaction(client) as session:
        await conversations.mark_read_by_client(session, client, clock.now())
    assert (await chat(app_database, client))[1] == 0
    async with app_database.actor_transaction(staff) as session:
        (answered,) = [
            item
            for item in await conversations.inbox(session)
            if item.conversation.id == conversation_id
        ]
    assert answered.conversation.waiting_since is None and answered.unread == 0


async def test_the_inbox_puts_the_longest_wait_first_and_answered_chats_last(
    app_database: Database, clock: FakeClock
) -> None:
    staff = new_staff()
    first = await say(app_database, new_client(), clock)
    second = await say(app_database, new_client(), clock)
    third = await say(app_database, new_client(), clock)
    await answer(app_database, staff, first, clock)
    async with app_database.actor_transaction(staff) as session:
        order = [item.conversation.id for item in await conversations.inbox(session, limit=200)]
    mine = [conversation for conversation in order if conversation in {first, second, third}]
    assert mine == [second, third, first]


async def test_a_message_sent_twice_is_stored_once(
    app_database: Database, clock: FakeClock, concurrency_barrier: AsyncBarrier
) -> None:
    client, staff = new_client(), new_staff()
    same = key()

    async def send() -> str:
        async with app_database.actor_transaction(client) as session:
            await concurrency_barrier.wait()
            message = await conversations.send_as_client(
                session, client, "Hello?", same, clock.now()
            )
        return message.id

    results = await asyncio.gather(send(), send(), return_exceptions=True)
    stored = {result for result in results if isinstance(result, str)}
    # Two taps at once: one is stored. The other finds it, or loses the race and is retried.
    assert len(stored) == 1
    async with app_database.actor_transaction(client) as session:
        again = await conversations.send_as_client(session, client, "Hello?", same, clock.now())
    assert {again.id} == stored
    messages, _ = await chat(app_database, client)
    assert len(messages) == 1

    staff_key = key()
    replies = set()
    for _ in range(2):
        async with app_database.actor_transaction(staff) as session:
            reply = await conversations.send_as_staff(
                session, staff, "Grace Auma", again.conversation_id, "Hi", staff_key, clock.now()
            )
        replies.add(reply.id)
    assert len(replies) == 1
    assert len(await notifications_of(app_database, client)) == 1


async def test_two_first_messages_at_once_open_one_chat(
    app_database: Database, clock: FakeClock, concurrency_barrier: AsyncBarrier
) -> None:
    client = new_client()

    async def send(body: str) -> str:
        async with app_database.actor_transaction(client) as session:
            await concurrency_barrier.wait()
            message = await conversations.send_as_client(session, client, body, key(), clock.now())
        return message.conversation_id

    assert len(set(await asyncio.gather(send("One"), send("Two")))) == 1
    assert len((await chat(app_database, client))[0]) == 2


async def test_a_solved_chat_is_final_and_the_next_message_opens_a_new_one(
    app_database: Database, clock: FakeClock
) -> None:
    client, staff = new_client(), new_staff()
    first = await say(app_database, client, clock, "First question")
    async with app_database.actor_transaction(staff) as session:
        await conversations.set_topic(session, staff, first, "withdrawal")
        solved = await conversations.solve(session, staff, first, clock.now())
    assert (solved.status, solved.topic, solved.waiting_since) == ("solved", "withdrawal", None)
    for attempt in ("answer", "solve", "topic"):
        with pytest.raises(ApplicationError) as raised:
            async with app_database.actor_transaction(staff) as session:
                if attempt == "answer":
                    await conversations.send_as_staff(
                        session, staff, "Grace", first, "Late", key(), clock.now()
                    )
                elif attempt == "solve":
                    await conversations.solve(session, staff, first, clock.now())
                else:
                    await conversations.set_topic(session, staff, first, "deposit")
        assert raised.value.code == "conflict"
    with pytest.raises(DBAPIError, match="a solved conversation never changes"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE support_conversations SET status = 'open', solved_at = NULL, "
                    "solved_by = NULL WHERE id = :id"
                ),
                {"id": first},
            )
    second = await say(app_database, client, clock, "Another question")
    assert second != first
    messages, _ = await chat(app_database, client)
    # The app shows one continuous chat across both.
    assert [m.body for m in messages] == ["First question", "Another question"]
    async with app_database.actor_transaction(client) as session:
        current = await conversations.conversation_for_client(session, client.id or "")
    assert current is not None and (current.id, current.status) == (second, "open")


async def test_polling_returns_only_what_is_new(app_database: Database, clock: FakeClock) -> None:
    client, staff = new_client(), new_staff()
    conversation_id = await say(app_database, client, clock, "One")
    (first,), _ = await chat(app_database, client)
    await answer(app_database, staff, conversation_id, clock, "Two")
    await say(app_database, client, clock, "Three")
    async with app_database.actor_transaction(client) as session:
        new, _ = await conversations.messages_for_client(session, client.id or "", after=first.id)
        latest, _ = await conversations.messages_for_client(session, client.id or "", limit=2)
    assert [m.body for m in new] == ["Two", "Three"]
    assert [m.body for m in latest] == ["Two", "Three"]
    async with app_database.actor_transaction(staff) as session:
        _, after = await conversations.conversation_for_staff(
            session, staff, conversation_id, clock.now(), after=new[0].id
        )
    assert [m.body for m in after] == ["Three"]


async def test_a_client_reaches_only_their_own_chat(
    app_database: Database, clock: FakeClock
) -> None:
    client, other, staff = new_client(), new_client(), new_staff()
    conversation_id = await say(app_database, client, clock, "Private")
    await answer(app_database, staff, conversation_id, clock)
    assert await chat(app_database, other) == ([], 0)
    async with app_database.actor_transaction(other) as session:
        # Naming the other client changes nothing: the row policy fences the rows.
        assert await conversations.messages_for_client(session, client.id or "") == ([], 0)
        assert await conversations.conversation_for_client(session, client.id or "") is None
        assert (await session.execute(text("SELECT count(*) FROM support_messages"))).scalar() == 0
    insert = text(
        "INSERT INTO support_messages (id, conversation_id, author_kind, author_id, author_name, "
        "body, client_key, created_at) VALUES (:id, :conversation, :kind, :author, :name, "
        "'Send your PIN', :key, now())"
    )
    attempts = [
        # Into someone else's chat.
        (other, {"kind": "client", "author": other.id, "name": None}),
        # Into their own chat, but as staff.
        (client, {"kind": "staff", "author": client.id, "name": "Grace"}),
        # As another client.
        (client, {"kind": "client", "author": other.id, "name": None}),
        # Staff, in a colleague's name.
        (staff, {"kind": "staff", "author": new_staff().id, "name": "Daniel"}),
    ]
    for actor, values in attempts:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(
                    insert,
                    {"id": push_id(), "conversation": conversation_id, "key": key(), **values},
                )
    with pytest.raises(DBAPIError, match="only staff solve or file a conversation"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text("UPDATE support_conversations SET topic = 'deposit' WHERE id = :id"),
                {"id": conversation_id},
            )


async def test_messages_are_never_changed_or_deleted_and_the_worker_has_no_access(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> None:
    client, staff = new_client(), new_staff()
    conversation_id = await say(app_database, client, clock)
    for statement in (
        "UPDATE support_messages SET body = 'changed' WHERE conversation_id = :id",
        "DELETE FROM support_messages WHERE conversation_id = :id",
        "DELETE FROM support_conversations WHERE id = :id",
    ):
        for actor in (client, staff):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with app_database.actor_transaction(actor) as session:
                    await session.execute(text(statement), {"id": conversation_id})
    for table in ("support_messages", "support_conversations", "complaints", "complaint_events"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608


async def test_bad_messages_are_refused_and_staff_cannot_post_as_clients(
    app_database: Database, clock: FakeClock
) -> None:
    client, staff = new_client(), new_staff()
    for body, message_key, field in ((" ", key(), "body"), ("Hello", "short", "key")):
        with pytest.raises(ApplicationError) as raised:
            async with app_database.actor_transaction(client) as session:
                await conversations.send_as_client(session, client, body, message_key, clock.now())
        assert raised.value.fields[0].field_path == [field]
    for wrong in (staff, SYSTEM):
        with pytest.raises(ApplicationError) as raised:
            async with app_database.actor_transaction(wrong) as session:
                await conversations.send_as_client(session, wrong, "Hello", key(), clock.now())
        assert raised.value.code == "forbidden"
    with pytest.raises(ApplicationError) as raised:
        async with app_database.actor_transaction(staff) as session:
            await conversations.send_as_staff(
                session, staff, "Grace", push_id(), "Hello", key(), clock.now()
            )
    assert raised.value.code == "not_found"


async def test_saved_replies_are_kept_by_staff_and_retired_not_deleted(
    app_database: Database, clock: FakeClock
) -> None:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        reply = await conversations.save_reply(
            session, staff, " Payout is  on its way ", "It was sent again today.", clock.now()
        )
        assert reply.title == "Payout is on its way"
        assert reply.id in {saved.id for saved in await conversations.saved_replies(session)}
        await conversations.retire_reply(session, staff, reply.id, clock.now())
        assert reply.id not in {saved.id for saved in await conversations.saved_replies(session)}
        with pytest.raises(ApplicationError) as raised:
            await conversations.retire_reply(session, staff, reply.id, clock.now())
        assert raised.value.code == "not_found"
        with pytest.raises(ApplicationError):
            await conversations.save_reply(session, staff, " ", "Body", clock.now())
    async with app_database.actor_transaction(new_client()) as session:
        assert await conversations.saved_replies(session) == []


# Complaints ---------------------------------------------------------------------------------------


async def test_a_client_makes_a_complaint_and_is_told_its_reference_and_reply_date(
    app_database: Database, clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        view = await complaints.log_by_client(session, client, LATE, clock)
    assert view.reference.startswith("C-") and int(view.reference[2:]) >= 1001
    assert (view.status, view.received_via, view.owner_id) == ("new", "app", None)
    # Received on Friday 13 November: five working days later is Friday 20 November.
    assert view.due_on == date(2026, 11, 20) and not view.overdue
    (told,) = await notifications_of(app_database, client)
    assert told.title == f"Complaint {view.reference} received"
    assert "20 Nov 2026" in told.body and told.link == inbox.Link("complaint", view.id)
    async with app_database.actor_transaction(client) as session:
        (listed,) = await complaints.complaints_for_client(
            session, client.id or "", date(2026, 11, 13)
        )
        assert listed == view
    async with app_database.actor_transaction(new_staff()) as session:
        _, history = await complaints.complaint_for_staff(session, view.id, date(2026, 11, 13))
        trail = await events_for_resource(session, complaints.RESOURCE, view.id)
    assert [(event.action, event.actor_kind, event.to_status) for event in history] == [
        ("logged", "client", "new")
    ]
    assert [(event.action, event.actor_id) for event in trail] == [("complaints.logged", client.id)]
    # The trail names the complaint. What it says stays in the register.
    assert trail[0].details == {"reference": view.reference, "status": "new"}


async def test_a_complaint_received_at_night_in_kampala_counts_from_the_kampala_day(
    app_database: Database,
) -> None:
    # 22:30 UTC on Friday is 01:30 on Saturday in Kampala. Five working days from Saturday.
    clock = FakeClock(datetime(2026, 11, 13, 22, 30, tzinfo=UTC))
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        view = await complaints.log_by_client(session, client, LATE, clock)
    assert view.due_on == date(2026, 11, 20)
    clock = FakeClock(datetime(2026, 11, 15, 22, 30, tzinfo=UTC))
    async with app_database.actor_transaction(client) as session:
        monday = await complaints.log_by_client(session, client, LATE, clock)
    assert monday.due_on == date(2026, 11, 23)


async def test_staff_work_a_complaint_to_its_outcome(
    app_database: Database, clock: FakeClock
) -> None:
    client, grace, daniel = new_client(), new_staff(), new_staff()
    conversation_id = await say(app_database, client, clock, "I want to complain.")
    new = NewComplaint(
        "payments",
        "Bank deposit not matched",
        "Sent 2m on Monday.",
        "chat",
        Money(2_000_000, Currency.UGX),
    )
    async with app_database.actor_transaction(grace) as session:
        view = await complaints.log_by_staff(
            session, grace, client.id or "", new, clock, conversation_id=conversation_id
        )
        assert view.amount == Money(2_000_000, Currency.UGX)
        assert view.conversation_id == conversation_id
        view = await complaints.assign(session, grace, view.id, daniel.id or "", clock)
        assert view.owner_id == daniel.id
    clock.advance(days=3)
    async with app_database.actor_transaction(daniel) as session:
        view = await complaints.move(
            session, daniel, view.id, "investigating", "Asked the bank for the statement", clock
        )
        view = await complaints.add_note(session, daniel, view.id, "Statement shows it", clock)
        view = await complaints.move(session, daniel, view.id, "escalated", "Needs finance", clock)
        with pytest.raises(ApplicationError) as raised:
            await complaints.move(session, daniel, view.id, "escalated", "Again", clock)
        assert raised.value.code == "conflict"
    clock.advance(days=4)
    async with app_database.actor_transaction(grace) as session:
        today = date(2026, 11, 20)
        assert not (await complaints.complaint_for_staff(session, view.id, today))[0].overdue
        assert (await complaints.complaint_for_staff(session, view.id, date(2026, 11, 21)))[
            0
        ].overdue
        closed = await complaints.close(
            session,
            grace,
            view.id,
            Closure("upheld", "Matched and invested, with interest.", True),
            clock,
        )
        _, history = await complaints.complaint_for_staff(session, view.id, today)
        trail = await events_for_resource(session, complaints.RESOURCE, view.id)
    assert (closed.status, closed.outcome, closed.client_accepted) == ("closed", "upheld", True)
    assert closed.closed_at == clock.now() and not closed.overdue
    assert [(event.action, event.from_status, event.to_status) for event in history] == [
        ("logged", None, "new"),
        ("assigned", None, None),
        ("status", "new", "investigating"),
        ("note", None, None),
        ("escalated", "investigating", "escalated"),
        ("closed", "escalated", "closed"),
    ]
    assert [event.action for event in reversed(trail)] == [
        "complaints.logged",
        "complaints.assigned",
        "complaints.status",
        "complaints.escalated",
        "complaints.closed",
    ]
    titles = [told.title for told in await notifications_of(app_database, client)]
    assert titles == [f"Complaint {view.reference} closed", f"Complaint {view.reference} received"]
    async with app_database.actor_transaction(client) as session:
        mine = await complaints.complaint_for_client(session, client.id or "", view.id, today)
    assert mine.resolution == "Matched and invested, with interest."


async def test_a_closed_complaint_never_changes_and_nothing_is_deleted(
    app_database: Database, clock: FakeClock
) -> None:
    client, staff = new_client(), new_staff()
    async with app_database.actor_transaction(client) as session:
        view = await complaints.log_by_client(session, client, LATE, clock)
    with pytest.raises(DBAPIError, match="permission denied for table complaints"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE complaints SET status = 'investigating', due_on = due_on + 30 "
                    "WHERE id = :id"
                ),
                {"id": view.id},
            )
    with pytest.raises(DBAPIError, match="ck_complaints_closed"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE complaints SET status = 'closed', closed_at = now(), "
                    "closed_by = :staff WHERE id = :id"
                ),
                {"id": view.id, "staff": staff.id},
            )
    with pytest.raises(DBAPIError, match="closed by the acting member of staff"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE complaints SET status = 'closed', closed_at = now(), "
                    "closed_by = :other, outcome = 'not_upheld', resolution = 'No', "
                    "client_accepted = false WHERE id = :id"
                ),
                {"id": view.id, "other": new_staff().id},
            )
    async with app_database.actor_transaction(staff) as session:
        await complaints.close(
            session, staff, view.id, Closure("not_upheld", "Explained.", False), clock
        )
    for call in ("close", "move", "note", "assign"):
        with pytest.raises(ApplicationError) as raised:
            async with app_database.actor_transaction(staff) as session:
                if call == "close":
                    await complaints.close(
                        session, staff, view.id, Closure("upheld", "Changed my mind.", True), clock
                    )
                elif call == "move":
                    await complaints.move(session, staff, view.id, "investigating", "Reopen", clock)
                elif call == "note":
                    await complaints.add_note(session, staff, view.id, "Late note", clock)
                else:
                    await complaints.assign(session, staff, view.id, staff.id or "", clock)
        assert raised.value.code == "conflict"
    with pytest.raises(DBAPIError, match="a closed complaint never changes"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("UPDATE complaints SET outcome = 'upheld' WHERE id = :id"), {"id": view.id}
            )
    for statement in (
        "DELETE FROM complaints WHERE id = :id",
        "DELETE FROM complaint_events WHERE complaint_id = :id",
        "UPDATE complaint_events SET note = 'rewritten' WHERE complaint_id = :id",
        "UPDATE complaints SET summary = 'rewritten' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(staff) as session:
                await session.execute(text(statement), {"id": view.id})


async def test_two_staff_closing_at_once_record_one_outcome(
    app_database: Database, clock: FakeClock, concurrency_barrier: AsyncBarrier
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        view = await complaints.log_by_client(session, client, LATE, clock)

    async def close(outcome: Outcome) -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await concurrency_barrier.wait()
                await complaints.close(
                    session,
                    staff,
                    view.id,
                    Closure(outcome, "Done.", True),
                    clock,
                )
        except ApplicationError as error:
            return error.code
        return outcome

    results = await asyncio.gather(close("upheld"), close("not_upheld"))
    assert sorted(results, key=lambda result: result != "conflict")[0] == "conflict"
    (outcome,) = [result for result in results if result != "conflict"]
    async with app_database.actor_transaction(new_staff()) as session:
        final, history = await complaints.complaint_for_staff(session, view.id, date(2026, 11, 13))
    assert final.outcome == outcome
    assert [event.action for event in history].count("closed") == 1
    closed = [
        told
        for told in await notifications_of(app_database, client)
        if told.event == "complaint_closed"
    ]
    assert len(closed) == 1


async def test_a_client_sees_only_their_own_complaints_and_never_the_staff_notes(
    app_database: Database, clock: FakeClock
) -> None:
    client, other, staff = new_client(), new_client(), new_staff()
    async with app_database.actor_transaction(client) as session:
        view = await complaints.log_by_client(session, client, LATE, clock)
    async with app_database.actor_transaction(staff) as session:
        await complaints.add_note(session, staff, view.id, "Client was rude on the phone", clock)
    async with app_database.actor_transaction(other) as session:
        assert (
            await complaints.complaints_for_client(session, other.id or "", date(2026, 11, 13))
            == []
        )
        assert (
            await complaints.complaints_for_client(session, client.id or "", date(2026, 11, 13))
            == []
        )
        with pytest.raises(ApplicationError) as raised:
            await complaints.complaint_for_client(
                session, other.id or "", view.id, date(2026, 11, 13)
            )
        assert raised.value.code == "not_found"
    async with app_database.actor_transaction(client) as session:
        assert (await session.execute(text("SELECT count(*) FROM complaint_events"))).scalar() == 0
        with pytest.raises(ApplicationError):
            await complaints.add_note(session, client, view.id, "Mine", clock)
    async with app_database.actor_transaction(client) as session:
        moved = await session.execute(
            text("UPDATE complaints SET status = 'investigating' WHERE id = :id RETURNING id"),
            {"id": view.id},
        )
        assert moved.all() == []
        mine = await complaints.complaint_for_client(
            session, client.id or "", view.id, date(2026, 11, 13)
        )
    assert mine.status == "new"


async def test_a_client_cannot_log_a_complaint_for_someone_else_or_as_staff(
    app_database: Database, clock: FakeClock
) -> None:
    client, other, staff = new_client(), new_client(), new_staff()
    insert = text(
        "INSERT INTO complaints (id, client_id, category, received_via, summary, details, "
        "received_at, due_on, logged_by_kind, logged_by, updated_at) VALUES (:id, :client, "
        "'other', :via, 'S', 'D', now(), current_date, :kind, :actor, now())"
    )
    for values in (
        {"client": other.id, "via": "app", "kind": "client", "actor": client.id},
        {"client": client.id, "via": "phone", "kind": "staff", "actor": client.id},
        {"client": client.id, "via": "phone", "kind": "client", "actor": client.id},
    ):
        with pytest.raises(DBAPIError, match=r"row-level security|ck_complaints_logged_by_client"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(insert, {"id": push_id(), **values})
    with pytest.raises(ApplicationError):
        async with app_database.actor_transaction(staff) as session:
            await complaints.log_by_staff(session, staff, client.id or "", LATE, clock)
    with pytest.raises(ApplicationError):
        async with app_database.actor_transaction(client) as session:
            await complaints.log_by_client(
                session, client, NewComplaint("other", "S", "D", "phone"), clock
            )


async def test_the_figures_at_the_top_of_the_register(
    app_database: Database, migrator_database: Database
) -> None:
    # The figures count the whole register, so this test starts from an empty one.
    async with migrator_database.transaction() as session:
        await session.execute(text("TRUNCATE complaint_events, complaints"))
    staff, client = new_staff(), new_client()
    clock = FakeClock(datetime(2026, 9, 28, 9, 0, tzinfo=UTC))

    async def log() -> str:
        async with app_database.actor_transaction(client) as session:
            return (await complaints.log_by_client(session, client, LATE, clock)).id

    async def close(complaint_id: str, outcome: Outcome) -> None:
        async with app_database.actor_transaction(staff) as session:
            await complaints.close(
                session,
                staff,
                complaint_id,
                Closure(outcome, "Done.", True),
                clock,
            )

    last_quarter = await log()  # Monday 28 September, due Monday 5 October
    old_and_open = await log()
    clock.current = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    await close(last_quarter, "upheld")  # closed this quarter, after 3 working days
    clock.current = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    quick, slow, open_now = await log(), await log(), await log()
    clock.current = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    await close(quick, "not_upheld")  # 1 working day
    clock.current = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
    await close(slow, "partly_upheld")  # 4 working days
    async with app_database.actor_transaction(staff) as session:
        figures = await complaints.summary(session, clock)
        listed = await complaints.register(session, date(2026, 11, 13))
        closed = await complaints.register(session, date(2026, 11, 13), show="closed")
        everything = await complaints.register(session, date(2026, 11, 13), show="all")
    assert figures == complaints.Summary(
        open=2,
        overdue=1,
        received_this_quarter=3,
        closed_this_quarter=3,
        upheld_this_quarter=2,
        # (3 + 1 + 4) / 3 = 2.67, shown as 2.7 days.
        average_days_to_close_tenths=27,
    )
    # The complaint past its deadline is first.
    assert [(view.id, view.overdue) for view in listed] == [(old_and_open, True), (open_now, False)]
    assert [view.id for view in closed] == [slow, quick, last_quarter]
    assert [view.id for view in everything][:2] == [old_and_open, open_now] and len(everything) == 5


# Routes -------------------------------------------------------------------------------------------


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = FRIDAY
        yield running


async def test_help_chat_over_http(api: Api) -> None:
    assert (await api.http.get("/client/support/chat")).status_code == 401
    client = await api.client()
    empty = (await api.http.get("/client/support/chat")).json()
    assert empty == {"conversation": None, "messages": [], "unread": 0}
    sent = await api.http.post(
        "/client/support/messages",
        json={
            "body": " My withdrawal says processing. ",
            "key": "client-key-0001",
            "topic": "withdrawal",
        },
    )
    assert sent.status_code == 201
    message = sent.json()
    assert (message["author"], message["author_name"]) == ("client", None)
    assert message["body"] == "My withdrawal says processing."
    again = await api.http.post(
        "/client/support/messages", json={"body": "Retry", "key": "client-key-0001"}
    )
    assert again.json()["id"] == message["id"]
    bad = await api.http.post(
        "/client/support/messages", json={"body": "  ", "key": "client-key-0002"}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["fields"] == [{"field_path": ["body"], "code": "required"}]
    # A client has no way into the staff routes.
    assert (await api.http.get("/staff/support/conversations")).status_code == 401

    await api.staff("finance")
    assert (await api.http.get("/staff/support/conversations")).status_code == 403
    await api.staff("support", name="Grace Auma")
    rows = (await api.http.get("/staff/support/conversations")).json()["conversations"]
    (row,) = [row for row in rows if row["client_id"] == client.id]
    assert row["conversation"]["topic"] == "withdrawal" and row["unread"] == 1
    route = f"/staff/support/conversations/{row['conversation']['id']}"
    opened = (await api.http.get(route)).json()
    assert [m["body"] for m in opened["messages"]] == ["My withdrawal says processing."]
    api.clock.advance(minutes=4)
    reply = await api.http.post(
        f"{route}/messages", json={"body": "I can see it.", "key": "staff-key-0001"}
    )
    assert reply.status_code == 201 and reply.json()["author_name"] == "Grace"
    saved = await api.http.post(
        "/staff/support/saved-replies", json={"title": "On its way", "body": "It was sent again."}
    )
    assert saved.status_code == 201
    replies = (await api.http.get("/staff/support/saved-replies")).json()["replies"]
    assert saved.json() in replies
    assert (
        await api.http.delete(f"/staff/support/saved-replies/{saved.json()['id']}")
    ).status_code == 204
    assert (await api.http.put(f"{route}/topic", json={"topic": "weather"})).status_code == 422
    solved = await api.http.post(f"{route}/solve")
    assert solved.json()["status"] == "solved"
    late = await api.http.post(f"{route}/messages", json={"body": "PS", "key": "staff-key-0002"})
    assert late.status_code == 409

    await api.as_client(client)
    polled = (await api.http.get("/client/support/chat", params={"after": message["id"]})).json()
    assert [m["body"] for m in polled["messages"]] == ["I can see it."]
    assert polled["unread"] == 1 and polled["conversation"]["status"] == "solved"
    told = (await api.http.get("/client/notifications")).json()
    assert [item["event"] for item in told["items"]] == ["support_reply"]
    assert told["items"][0]["link"] == {"kind": "conversation", "id": row["conversation"]["id"]}
    assert (await api.http.post("/client/support/read")).status_code == 204


async def test_complaints_over_http(api: Api) -> None:
    client = await api.client()
    made = await api.http.post(
        "/client/complaints",
        json={
            "category": "withdrawals",
            "summary": "Payout did not arrive",
            "details": "Since Tuesday.",
        },
    )
    assert made.status_code == 201
    complaint = made.json()
    assert complaint["status"] == "new" and complaint["due_on"] == "2026-11-20"
    assert set(complaint) == {
        "id",
        "reference",
        "category",
        "summary",
        "details",
        "status",
        "received_at",
        "due_on",
        "closed_at",
        "outcome",
        "resolution",
    }
    unknown = await api.http.post(
        "/client/complaints", json={"category": "weather", "summary": "S", "details": "D"}
    )
    assert unknown.status_code == 422
    listed = (await api.http.get("/client/complaints")).json()["complaints"]
    assert [item["id"] for item in listed] == [complaint["id"]]
    other = await api.client()
    assert (await api.http.get(f"/client/complaints/{complaint['id']}")).status_code == 404
    assert other.id != client.id

    await api.staff("finance")
    assert (await api.http.get("/staff/complaints")).status_code == 403
    grace = await api.staff("support")
    register = (await api.http.get("/staff/complaints")).json()
    assert register["summary"]["target_working_days"] == 5
    assert complaint["id"] in [item["id"] for item in register["complaints"]]
    logged = await api.http.post(
        "/staff/complaints",
        json={
            "client_id": client.id,
            "category": "fees",
            "received_via": "phone",
            "summary": "Fee was not explained",
            "details": "Called on Friday.",
            "amount": {"minor": 5000, "currency": "UGX"},
        },
    )
    assert logged.status_code == 201 and logged.json()["amount"] == {
        "minor": 5000,
        "currency": "UGX",
    }
    float_amount = await api.http.post(
        "/staff/complaints",
        json={
            "client_id": client.id,
            "category": "fees",
            "received_via": "phone",
            "summary": "S",
            "details": "D",
            "amount": {"minor": 5000.5, "currency": "UGX"},
        },
    )
    assert float_amount.status_code == 422
    route = f"/staff/complaints/{complaint['id']}"
    owned = await api.http.put(f"{route}/owner", json={"owner_id": grace.id})
    assert owned.json()["owner_id"] == grace.id
    moved = await api.http.post(
        f"{route}/status", json={"status": "investigating", "note": "Checking MTN"}
    )
    assert moved.json()["status"] == "investigating"
    assert (
        await api.http.post(f"{route}/status", json={"status": "closed", "note": "x"})
    ).status_code == 422
    assert (
        await api.http.post(f"{route}/notes", json={"note": "MTN confirmed"})
    ).status_code == 200
    # The front line works a complaint but does not close it.
    refused = await api.http.post(
        f"{route}/close",
        json={"outcome": "not_upheld", "resolution": "No.", "client_accepted": False},
    )
    assert refused.status_code == 403
    await api.staff("compliance")
    incomplete = await api.http.post(
        f"{route}/close", json={"outcome": "upheld", "resolution": "Paid."}
    )
    assert incomplete.status_code == 422
    closed = await api.http.post(
        f"{route}/close",
        json={"outcome": "upheld", "resolution": "Paid again.", "client_accepted": True},
    )
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert (await api.http.post(f"{route}/notes", json={"note": "Late"})).status_code == 409
    detail = (await api.http.get(route)).json()
    assert [event["action"] for event in detail["history"]] == [
        "logged",
        "assigned",
        "status",
        "note",
        "closed",
    ]

    await api.as_client(client)
    mine = (await api.http.get(f"/client/complaints/{complaint['id']}")).json()
    assert (mine["status"], mine["outcome"], mine["resolution"]) == (
        "closed",
        "upheld",
        "Paid again.",
    )
    assert "client_accepted" not in mine and "owner_id" not in mine


async def test_seeing_help_and_complaints_is_not_enough_to_act_on_them(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await api.client()
    sent = await api.http.post(
        "/client/support/messages", json={"body": "Hello", "key": "key-viewonly-1"}
    )
    conversation = sent.json()["conversation_id"]
    complaint = (
        await api.http.post(
            "/client/complaints", json={"category": "other", "summary": "S", "details": "D"}
        )
    ).json()["id"]
    await api.staff("support")
    saved = await api.http.post(
        "/staff/support/saved-replies", json={"title": "Hi", "body": "Hello from Finvest"}
    )
    reply = saved.json()["id"]
    # A role that may look at both screens and nothing more, as the owner might give one.
    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        "finance",
        ROLE_PERMISSIONS["finance"] | {"support.view", "complaints.view"},
    )
    await api.staff("finance")
    chat = f"/staff/support/conversations/{conversation}"
    route = f"/staff/complaints/{complaint}"
    assert (await api.http.get("/staff/support/conversations")).status_code == 200
    assert (await api.http.get(chat)).status_code == 200
    assert (await api.http.get("/staff/support/saved-replies")).status_code == 200
    assert (await api.http.get("/staff/complaints")).status_code == 200
    assert (await api.http.get(route)).status_code == 200
    writes = [
        ("post", f"{chat}/messages", {"body": "Hi", "key": "key-viewonly-2"}),
        ("post", f"{chat}/solve", None),
        ("put", f"{chat}/topic", {"topic": "deposit"}),
        ("post", "/staff/support/saved-replies", {"title": "T", "body": "B"}),
        ("delete", f"/staff/support/saved-replies/{reply}", None),
        (
            "post",
            "/staff/complaints",
            {
                "client_id": client.id,
                "category": "fees",
                "received_via": "phone",
                "summary": "S",
                "details": "D",
            },
        ),
        ("put", f"{route}/owner", {"owner_id": push_id()}),
        ("post", f"{route}/status", {"status": "investigating", "note": "Looking"}),
        ("post", f"{route}/notes", {"note": "Note"}),
        (
            "post",
            f"{route}/close",
            {"outcome": "not_upheld", "resolution": "No.", "client_accepted": False},
        ),
    ]
    for verb, path, body in writes:
        response = await api.http.request(verb.upper(), path, json=body)
        assert response.status_code == 403, (verb, path)
    # Nothing changed.
    assert (await api.http.get(route)).json()["complaint"]["status"] == "new"
    assert (await api.http.get(chat)).json()["conversation"]["status"] == "open"


async def test_a_client_cannot_move_their_chat_back_in_the_staff_queue(
    app_database: Database, clock: FakeClock
) -> None:
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        await conversations.send_as_client(session, client, "Hello", "key-queue-0001", clock.now())
    for statement in (
        "UPDATE support_conversations SET waiting_since = waiting_since - interval '1 day'",
        "UPDATE support_conversations SET waiting_since = NULL",
        "UPDATE support_conversations SET last_message_at = last_message_at - interval '1 day'",
    ):
        with pytest.raises(DBAPIError, match="cannot move a conversation back in the queue"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(text(statement))
