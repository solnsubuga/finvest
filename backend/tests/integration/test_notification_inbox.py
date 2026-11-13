"""The notification centre, channel choices and wordings against real PostgreSQL."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.identity.api import COOKIES
from app.modules.identity.domain.permissions import ROLE_PERMISSIONS
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.notifications import contract, inbox, preferences, wording_store
from app.modules.notifications.domain.events import Category, Channel
from app.modules.notifications.domain.preferences import DEFAULTS as CHANNEL_DEFAULTS
from app.modules.notifications.domain.wording import DEFAULTS, InvalidVariables, Wording
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

AMOUNT = Money(1_000_000, Currency.UGX)
PAID: dict[str, contract.Value] = {"amount": AMOUNT, "method": "MTN Mobile Money"}
NEW_WORDS = Wording("Money in", "{amount} arrived from {method}.", "Finvest: {amount} arrived.")


async def tell(
    database: Database,
    actor: Actor,
    client_id: str,
    clock: FakeClock,
    *,
    key: str | None = None,
    event_name: str = "payment_confirmed",
) -> str | None:
    async with database.actor_transaction(actor) as session:
        return await contract.notify(
            session,
            event_name=event_name,
            client_id=client_id,
            idempotency_key=key or push_id(),
            variables=PAID if event_name == "payment_confirmed" else {},
            link=contract.Link("deposit", push_id()),
            now=clock.now(),
        )


async def page(database: Database, client: Actor, **arguments: object) -> inbox.Page:
    assert client.id is not None
    async with database.actor_transaction(client) as session:
        return await inbox.page_for_client(session, client.id, **arguments)  # type: ignore[arg-type]


# Telling a client ---------------------------------------------------------------------------------


async def test_a_client_is_told_in_the_words_of_the_event(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    notification_id = await tell(app_database, client, client.id, fake_clock)
    listed = await page(app_database, client)
    assert listed.unread == 1 and listed.next_before is None
    (item,) = listed.items
    assert item.id == notification_id
    assert (item.event, item.category) == ("payment_confirmed", Category.MONEY)
    assert item.title == "Payment received"
    assert item.body == "We received your payment from MTN Mobile Money."
    assert item.link is not None and item.link.kind == "deposit"
    assert item.created_at == fake_clock.now() and item.read_at is None


async def test_the_same_event_and_key_is_told_once(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    key = push_id()
    assert await tell(app_database, client, client.id, fake_clock, key=key) is not None
    assert await tell(app_database, client, client.id, fake_clock, key=key) is None
    # Another event may use the same key: a deposit ID names its payment and its failure alike.
    async with app_database.actor_transaction(client) as session:
        other = await contract.notify(
            session,
            event_name="payment_failed",
            client_id=client.id,
            idempotency_key=key,
            variables=PAID,
            now=fake_clock.now(),
        )
    assert other is not None
    assert len((await page(app_database, client)).items) == 2


async def test_a_key_shared_by_many_clients_tells_each_of_them(
    worker_database: Database, app_database: Database, fake_clock: FakeClock
) -> None:
    # A daily job keys by the day, and a statement run by the period.
    clients = [new_client() for _ in range(3)]
    key = f"2026-11-13.{push_id()}"
    async with worker_database.actor_transaction(SYSTEM) as session:
        for _ in range(2):
            for client in clients:
                await contract.notify(
                    session,
                    event_name="statement_ready",
                    client_id=client.id or "",
                    idempotency_key=key,
                    variables={"period": "October"},
                    now=fake_clock.now(),
                )
    for client in clients:
        (item,) = (await page(app_database, client)).items
        assert item.title == "October statement is ready"


async def test_no_value_however_long_or_hostile_fails_the_callers_transaction(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear_wordings(migrator_database)
    client, maker, checker = new_client(), new_staff(), new_staff()
    assert client.id is not None
    longest = Wording(
        "{amount}{destination}".ljust(60, "x"),
        "{amount}{destination}".ljust(300, "x"),
        "{amount}{destination}".ljust(300, "x"),
    )
    async with app_database.actor_transaction(maker) as session:
        proposal = await wording_store.propose(
            session, maker, "payout_sent", longest, "Longest allowed", fake_clock
        )
    await decide(app_database, checker, proposal.id, "approved", fake_clock)
    async with app_database.actor_transaction(client) as session:
        told = await contract.notify(
            session,
            event_name="payout_sent",
            client_id=client.id,
            idempotency_key=push_id(),
            variables={
                "amount": Money(2**63 - 1, Currency.UGX),
                "destination": "http://evil.example/x " + "y" * 5000,
            },
            now=fake_clock.now(),
        )
    assert told is not None
    (item,) = (await page(app_database, client)).items
    assert "evil" not in item.body and len(item.body) <= 2000 and len(item.title) <= 120
    await clear_wordings(migrator_database)


async def test_two_transactions_reporting_the_same_thing_tell_the_client_once(
    worker_database: Database, fake_clock: FakeClock, concurrency_barrier: AsyncBarrier
) -> None:
    client = new_client()
    assert client.id is not None
    key = push_id()

    async def report() -> str | None:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await concurrency_barrier.wait()
            return await contract.notify(
                session,
                event_name="payment_confirmed",
                client_id=client.id or "",
                idempotency_key=key,
                variables=PAID,
                now=fake_clock.now(),
            )

    told = await asyncio.gather(report(), report())
    assert sorted(told, key=lambda value: value is None)[1] is None and any(told)
    async with worker_database.actor_transaction(SYSTEM) as session:
        count = await session.scalar(
            text("SELECT count(*) FROM notifications WHERE idempotency_key = :key"), {"key": key}
        )
    assert count == 1


async def test_a_notification_rolls_back_with_the_change_it_reports(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    with pytest.raises(RuntimeError, match="the payment failed to save"):
        async with app_database.actor_transaction(client) as session:
            await contract.notify(
                session,
                event_name="payment_confirmed",
                client_id=client.id,
                idempotency_key=push_id(),
                variables=PAID,
                now=fake_clock.now(),
            )
            raise RuntimeError("the payment failed to save")
    assert (await page(app_database, client)).items == ()


async def test_wrong_variables_or_an_unknown_event_write_nothing(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(InvalidVariables):
            await contract.notify(
                session,
                event_name="payment_confirmed",
                client_id=client.id,
                idempotency_key=push_id(),
                variables={"amount": 1_000_000.0, "method": "MTN"},  # type: ignore[dict-item]
                now=fake_clock.now(),
            )
        with pytest.raises(contract.UnknownEvent):
            await contract.notify(
                session,
                event_name="free_money",
                client_id=client.id,
                idempotency_key=push_id(),
                variables={},
                now=fake_clock.now(),
            )
    assert (await page(app_database, client)).items == ()


# Reading ------------------------------------------------------------------------------------------


async def test_pages_run_newest_first_and_marking_read_counts_down(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    ids: list[str] = []
    for _ in range(5):
        fake_clock.advance(minutes=1)
        ids.append(await tell(app_database, client, client.id, fake_clock) or "")
    first = await page(app_database, client, limit=2)
    assert [item.id for item in first.items] == [ids[4], ids[3]]
    assert first.unread == 5 and first.next_before == ids[3]
    second = await page(app_database, client, before=first.next_before, limit=2)
    assert [item.id for item in second.items] == [ids[2], ids[1]]
    last = await page(app_database, client, before=second.next_before, limit=2)
    assert [item.id for item in last.items] == [ids[0]] and last.next_before is None

    async with app_database.actor_transaction(client) as session:
        assert await inbox.mark_read(session, client, [ids[0], push_id()], fake_clock.now()) == 4
    async with app_database.actor_transaction(client) as session:
        assert await inbox.mark_read(session, client, None, fake_clock.advance(minutes=1)) == 0
    read = {item.id: item.read_at for item in (await page(app_database, client)).items}
    assert read[ids[0]] is not None and read[ids[0]] < read[ids[1]]  # type: ignore[operator]


async def test_notifications_at_the_same_instant_still_page_without_gaps(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    ids = {await tell(app_database, client, client.id, fake_clock) for _ in range(5)}
    seen: list[str] = []
    before = None
    while True:
        listed = await page(app_database, client, before=before, limit=2)
        seen += [item.id for item in listed.items]
        before = listed.next_before
        if before is None:
            break
    assert len(seen) == 5 and set(seen) == ids


# Fences -------------------------------------------------------------------------------------------


async def test_a_client_sees_and_marks_only_their_own(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, other = new_client(), new_client()
    assert client.id is not None and other.id is not None
    notification_id = await tell(app_database, client, client.id, fake_clock)
    assert (await page(app_database, other)).items == ()
    async with app_database.actor_transaction(other) as session:
        # Asking for another client's page by ID shows nothing either: the policy fences rows.
        assert (await inbox.page_for_client(session, client.id)).items == ()
        await session.execute(
            text("UPDATE notifications SET read_at = now() WHERE id = :id"), {"id": notification_id}
        )
    assert (await page(app_database, client)).unread == 1


async def test_a_client_request_cannot_tell_another_client(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, other = new_client(), new_client()
    assert other.id is not None
    with pytest.raises(DBAPIError, match="row-level security"):
        await tell(app_database, client, other.id, fake_clock)


async def test_the_api_is_not_trusted_as_the_system_but_the_worker_is(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    with pytest.raises(DBAPIError, match="row-level security"):
        await tell(app_database, SYSTEM, client.id, fake_clock)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.transaction() as session:
            await contract.notify(
                session,
                event_name="kyc_approved",
                client_id=client.id,
                idempotency_key=push_id(),
                variables={},
                now=fake_clock.now(),
            )
    assert await tell(worker_database, SYSTEM, client.id, fake_clock) is not None
    assert await tell(app_database, new_staff(), client.id, fake_clock, event_name="kyc_approved")
    assert len((await page(app_database, client)).items) == 2


async def test_what_a_client_was_told_cannot_be_rewritten_or_deleted(
    application_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    actor = client if application_database.engine.url.username == "finvest_app" else SYSTEM
    notification_id = await tell(application_database, actor, client.id, fake_clock)
    for statement in (
        "UPDATE notifications SET body = 'Send your PIN' WHERE id = :id",
        "UPDATE notifications SET client_id = client_id WHERE id = :id",
        "DELETE FROM notifications WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with application_database.actor_transaction(actor) as session:
                await session.execute(text(statement), {"id": notification_id})


# Channel choices ----------------------------------------------------------------------------------


async def test_a_client_starts_with_the_defaults_and_changes_only_what_they_name(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    assert client.id is not None
    async with app_database.actor_transaction(client) as session:
        assert await preferences.channels_for_client(session, client.id) == CHANNEL_DEFAULTS
        saved = await preferences.save(
            session,
            client,
            {Category.MONEY: frozenset({Channel.EMAIL}), Category.NEWS: frozenset()},
            fake_clock.now(),
        )
    assert saved[Category.MONEY] == frozenset({Channel.EMAIL})
    assert saved[Category.NEWS] == frozenset()
    assert saved[Category.ACCOUNT] == CHANNEL_DEFAULTS[Category.ACCOUNT]
    async with app_database.actor_transaction(client) as session:
        again = await preferences.save(
            session, client, {Category.NEWS: frozenset({Channel.PUSH})}, fake_clock.now()
        )
    assert again[Category.NEWS] == frozenset({Channel.PUSH})
    assert again[Category.MONEY] == frozenset({Channel.EMAIL})
    async with app_database.actor_transaction(new_client()) as session:
        assert await preferences.channels_for_client(session, client.id) == CHANNEL_DEFAULTS


async def test_security_alerts_cannot_be_silenced_even_by_writing_the_row(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = new_client()
    with pytest.raises(ApplicationError) as raised:
        async with app_database.actor_transaction(client) as session:
            await preferences.save(
                session,
                client,
                {Category.SECURITY: frozenset({Channel.PUSH}), Category.NEWS: frozenset()},
                fake_clock.now(),
            )
    assert raised.value.fields[0].field_path == ["security"]
    assert raised.value.fields[0].code == "security_needs_sms_or_email"
    async with app_database.actor_transaction(client) as session:
        # Nothing was saved, not even the valid half.
        assert await preferences.channels_for_client(session, client.id or "") == CHANNEL_DEFAULTS
    with pytest.raises(DBAPIError, match="ck_notification_preferences_security"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO notification_preferences (client_id, category, channels, "
                    "updated_at) VALUES (:id, 'security', ARRAY['push'], now())"
                ),
                {"id": client.id},
            )


async def test_a_client_cannot_set_another_clients_channels(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client, other = new_client(), new_client()
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO notification_preferences (client_id, category, channels, "
                    "updated_at) VALUES (:id, 'money', '{}', now())"
                ),
                {"id": other.id},
            )


# Wordings -----------------------------------------------------------------------------------------


async def propose(
    database: Database, staff: Actor, clock: FakeClock, event_name: str = "payment_confirmed"
) -> wording_store.Proposal:
    async with database.actor_transaction(staff) as session:
        return await wording_store.propose(
            session, staff, event_name, NEW_WORDS, "Shorter, as agreed with compliance", clock
        )


async def decide(
    database: Database, staff: Actor, proposal_id: str, decision: str, clock: FakeClock
) -> None:
    async with database.actor_transaction(staff) as session:
        await wording_store.decide(
            session,
            staff,
            proposal_id,
            decision,  # type: ignore[arg-type]
            "Read and agreed",
            clock,
        )


async def clear_wordings(migrator_database: Database) -> None:
    # Wordings are shared by every test, so each wording test starts from the built-in words.
    async with migrator_database.transaction() as session:
        await session.execute(text("TRUNCATE notification_wordings"))


async def test_new_words_are_used_only_after_a_second_person_approves(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear_wordings(migrator_database)
    client, maker, checker = new_client(), new_staff(), new_staff()
    assert client.id is not None
    proposal = await propose(app_database, maker, fake_clock)
    await tell(app_database, client, client.id, fake_clock)
    fake_clock.advance(minutes=5)
    await decide(app_database, checker, proposal.id, "approved", fake_clock)
    fake_clock.advance(minutes=5)
    await tell(app_database, client, client.id, fake_clock)
    newest, oldest = (await page(app_database, client)).items
    # What the client was told before the change keeps the words it was told in.
    assert oldest.title == DEFAULTS["payment_confirmed"].title
    assert (newest.title, newest.body) == (
        "Money in",
        "UGX 1,000,000 arrived from MTN Mobile Money.",
    )

    async with app_database.actor_transaction(checker) as session:
        listed = {item.event: item for item in await wording_store.list_wordings(session)}
        trail = await events_for_resource(session, wording_store.RESOURCE, proposal.id)
    assert "announcement" not in listed
    changed = listed["payment_confirmed"]
    assert changed.edited and changed.wording == NEW_WORDS and changed.proposal is None
    assert (
        not listed["payout_sent"].edited
        and listed["payout_sent"].wording == DEFAULTS["payout_sent"]
    )
    assert [(event.action, event.category, event.actor_id) for event in reversed(trail)] == [
        ("notifications.wording_proposed", "change", maker.id),
        ("notifications.wording_approved", "approval", checker.id),
    ]


async def test_nobody_approves_their_own_words_even_by_writing_the_row(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear_wordings(migrator_database)
    maker = new_staff()
    proposal = await propose(app_database, maker, fake_clock)
    for decision in ("approved", "rejected"):
        with pytest.raises(ApplicationError) as raised:
            await decide(app_database, maker, proposal.id, decision, fake_clock)
        assert raised.value.code == "forbidden"
    with pytest.raises(DBAPIError, match="ck_notification_wordings_two_people"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE notification_wordings SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'mine' WHERE id = :id"
                ),
                {"staff": maker.id, "id": proposal.id},
            )
    # Naming a colleague as the approver from your own transaction is refused as well.
    with pytest.raises(DBAPIError, match="decided once, by the acting member of staff"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE notification_wordings SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'theirs' WHERE id = :id"
                ),
                {"staff": new_staff().id, "id": proposal.id},
            )
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, new_staff(), proposal.id, "withdrawn", fake_clock)
    assert raised.value.code == "forbidden"
    await decide(app_database, maker, proposal.id, "withdrawn", fake_clock)
    async with app_database.actor_transaction(maker) as session:
        assert (
            await wording_store.active_wording(session, "payment_confirmed")
            == (DEFAULTS["payment_confirmed"])
        )


async def test_one_change_waits_at_a_time_and_a_decision_is_final(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear_wordings(migrator_database)
    maker, checker = new_staff(), new_staff()
    proposal = await propose(app_database, maker, fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, checker, fake_clock)
    assert raised.value.code == "conflict"
    other = await propose(app_database, checker, fake_clock, "payment_failed")
    await decide(app_database, maker, other.id, "rejected", fake_clock)
    await decide(app_database, checker, proposal.id, "rejected", fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, checker, proposal.id, "approved", fake_clock)
    assert raised.value.code == "not_found"
    for statement in (
        "UPDATE notification_wordings SET status = 'approved' WHERE id = :id",
        "UPDATE notification_wordings SET decision_reason = 'changed my mind' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="a decided wording never changes"):
            async with app_database.actor_transaction(new_staff()) as session:
                await session.execute(text(statement), {"id": proposal.id})
    async with app_database.actor_transaction(maker) as session:
        assert (
            await wording_store.active_wording(session, "payment_confirmed")
            == (DEFAULTS["payment_confirmed"])
        )
    # A rejected change frees the message for the next proposal.
    await propose(app_database, maker, fake_clock)


async def test_two_approvals_racing_decide_once(
    app_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
    concurrency_barrier: AsyncBarrier,
) -> None:
    await clear_wordings(migrator_database)
    proposal = await propose(app_database, new_staff(), fake_clock)

    async def approve() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await concurrency_barrier.wait()
                await wording_store.decide(
                    session, staff, proposal.id, "approved", "Agreed", fake_clock
                )
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(approve(), approve())) == ["approved", "not_found"]
    async with app_database.actor_transaction(new_staff()) as session:
        trail = await events_for_resource(session, wording_store.RESOURCE, proposal.id)
    assert [event.action for event in trail].count("notifications.wording_approved") == 1


async def test_bad_words_and_missing_reasons_are_refused(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    await clear_wordings(migrator_database)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        with pytest.raises(ApplicationError) as raised:
            await wording_store.propose(
                session,
                staff,
                "payment_confirmed",
                Wording("Paid", "Confirm your PIN at https://finvest-ug.com", "Paid {when}"),
                "Reason",
                fake_clock,
            )
        assert [(problem.field_path, problem.code) for problem in raised.value.fields] == [
            (["body"], "outside_link"),
            (["sms"], "unknown_placeholder"),
        ]
        with pytest.raises(ApplicationError) as raised:
            await wording_store.propose(
                session, staff, "payment_confirmed", NEW_WORDS, "  ", fake_clock
            )
        assert raised.value.fields[0].field_path == ["reason"]
        for event_name, code in (
            ("free_money", "not_found"),
            ("announcement", "validation_failed"),
        ):
            with pytest.raises(ApplicationError) as raised:
                await wording_store.propose(
                    session, staff, event_name, NEW_WORDS, "Why", fake_clock
                )
            assert raised.value.code == code
        assert all(item.proposal is None for item in await wording_store.list_wordings(session))


async def test_only_staff_touch_wordings_and_clients_see_only_approved_words(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
) -> None:
    await clear_wordings(migrator_database)
    client = new_client()
    proposal = await propose(app_database, new_staff(), fake_clock)
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError):
            await wording_store.propose(
                session, client, "payout_sent", NEW_WORDS, "Why", fake_clock
            )
        visible = await session.scalar(text("SELECT count(*) FROM notification_wordings"))
    assert visible == 0
    insert = text(
        "INSERT INTO notification_wordings (id, event, title, body, sms, reason, proposed_by, "
        "proposed_at) VALUES (:id, 'payout_sent', 'Sent', 'Sent', 'Sent', 'why', :staff, now())"
    )
    values = {"id": push_id(), "staff": push_id()}
    with pytest.raises(DBAPIError, match="only staff change a wording"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(insert, values)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(insert, values)
    # A member of staff cannot record a proposal in a colleague's name.
    with pytest.raises(DBAPIError, match="starts as a proposal by the acting member of staff"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(insert, values)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text("DELETE FROM notification_wordings WHERE id = :id"), {"id": proposal.id}
            )


# Routes -------------------------------------------------------------------------------------------

CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    database: Database

    async def client(self) -> Actor:
        client_id = await self.identity.create_client(
            phone=f"+2567{push_id().__hash__() % 10**8:08d}", email=None, password=PASSWORD
        )
        actor = Actor("client", client_id)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="token")
        self.http.headers["Authorization"] = f"Bearer {issued.token}"
        return actor

    async def staff(self, role: str = "admin") -> Actor:
        staff_id = await self.identity.invite_staff(
            # Not from a push ID: lower-casing one is not one-to-one, and ids made under a test
            # clock share a millisecond, so two of them can fold to the same address.
            email=f"staff-{uuid.uuid4().hex}@finvestug.com",
            name="Grace Auma",
            role=role,  # type: ignore[arg-type]
            created_by=None,
        )
        # Test set-up only: active, with a password. In the application only enrolment, which
        # also sets a second step, does this.
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                    "password_changed_at = created_at WHERE id = :id"
                ),
                {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
            )
        actor = Actor("staff", staff_id)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="cookie")
        self.http.cookies.set(COOKIES["staff"], issued.token)
        self.http.headers["Origin"] = STAFF_ORIGIN
        return actor


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
        }
    )
    clock = FakeClock()
    app = create_app(settings, app_database, clock, HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, clock, app_database)


async def test_a_client_reads_and_clears_their_notifications_over_http(api: Api) -> None:
    assert (await api.http.get("/client/notifications")).status_code == 401
    client = await api.client()
    assert client.id is not None
    ids = []
    for _ in range(3):
        api.clock.advance(minutes=1)
        ids.append(await tell(api.database, client, client.id, api.clock))
    response = await api.http.get("/client/notifications", params={"limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [ids[2], ids[1]]
    assert body["unread"] == 3 and body["next_before"] == ids[1]
    assert body["items"][0] == {
        "id": ids[2],
        "event": "payment_confirmed",
        "category": "money",
        "title": "Payment received",
        "body": "We received your payment from MTN Mobile Money.",
        "link": body["items"][0]["link"],
        "created_at": body["items"][0]["created_at"],
        "read": False,
    }
    rest = await api.http.get("/client/notifications", params={"before": ids[1]})
    assert [item["id"] for item in rest.json()["items"]] == [ids[0]]
    marked = await api.http.post("/client/notifications/read", json={"ids": [ids[0]]})
    assert marked.json() == {"unread": 2}
    assert (await api.http.post("/client/notifications/read", json={})).json() == {"unread": 0}
    assert (await api.http.get("/client/notifications", params={"limit": 0})).status_code == 422


async def test_a_client_changes_channels_over_http(api: Api) -> None:
    await api.client()
    start = (await api.http.get("/client/notification-preferences")).json()["categories"]
    assert {row["category"]: row["channels"] for row in start} == {
        category.value: [channel.value for channel in Channel if channel in channels]
        for category, channels in CHANNEL_DEFAULTS.items()
    }
    assert [row["category"] for row in start if row["always_on"]] == ["security"]
    saved = await api.http.put(
        "/client/notification-preferences",
        json={"categories": [{"category": "earnings", "channels": ["email", "push"]}]},
    )
    assert saved.status_code == 200
    by_category = {row["category"]: row["channels"] for row in saved.json()["categories"]}
    assert by_category["earnings"] == ["push", "email"]
    assert by_category["money"] == ["push", "sms"]
    refused = await api.http.put(
        "/client/notification-preferences",
        json={"categories": [{"category": "security", "channels": ["push"]}]},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["fields"] == [
        {"field_path": ["security"], "code": "security_needs_sms_or_email"}
    ]
    unknown = await api.http.put(
        "/client/notification-preferences",
        json={"categories": [{"category": "money", "channels": ["fax"]}]},
    )
    assert unknown.status_code == 422


async def test_wordings_are_changed_by_two_admins_over_http(
    api: Api, migrator_database: Database
) -> None:
    await clear_wordings(migrator_database)
    await api.staff("support")
    assert (await api.http.get("/staff/notification-wordings")).status_code == 403
    await api.staff()
    listed = (await api.http.get("/staff/notification-wordings")).json()["wordings"]
    first = next(item for item in listed if item["event"] == "payment_confirmed")
    assert first["variables"] == ["amount", "method"] and first["category"] == "money"
    assert first["preview"]["body"] == "We received your payment from MTN Mobile Money."
    assert not first["edited"] and first["proposal"] is None
    change = {
        "wording": {
            "title": "Money in",
            "body": "{amount} arrived.",
            "sms": "Finvest: {amount} in.",
        },
        "reason": "Shorter",
    }
    bad = await api.http.post(
        "/staff/notification-wordings/payment_confirmed/proposals",
        json={**change, "wording": {**change["wording"], "body": "{amount} at 15%"}},  # type: ignore[dict-item]
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["fields"] == [{"field_path": ["body"], "code": "no_rates"}]
    created = await api.http.post(
        "/staff/notification-wordings/payment_confirmed/proposals", json=change
    )
    assert created.status_code == 201
    proposal = created.json()
    assert proposal["yours"] and proposal["preview"]["body"] == "UGX 1,000,000 arrived."
    decision = f"/staff/notification-wordings/proposals/{proposal['id']}/decision"
    own = await api.http.post(decision, json={"decision": "approved", "reason": "Mine"})
    assert own.status_code == 403

    await api.staff()
    listed = (await api.http.get("/staff/notification-wordings")).json()["wordings"]
    waiting = next(item for item in listed if item["event"] == "payment_confirmed")["proposal"]
    assert waiting["id"] == proposal["id"] and not waiting["yours"]
    approved = await api.http.post(decision, json={"decision": "approved", "reason": "Agreed"})
    assert approved.status_code == 200
    listed = (await api.http.get("/staff/notification-wordings")).json()["wordings"]
    now = next(item for item in listed if item["event"] == "payment_confirmed")
    assert now["edited"] and now["wording"]["title"] == "Money in" and now["proposal"] is None
    assert (
        await api.http.post(decision, json={"decision": "rejected", "reason": "Late"})
    ).status_code == 404


async def test_seeing_settings_is_not_enough_to_change_what_clients_are_told(
    api: Api, migrator_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await clear_wordings(migrator_database)
    # No role has Settings without the right to change wordings today. Give one to support,
    # as the owner might, and it still cannot be either of the two people.
    monkeypatch.setitem(
        ROLE_PERMISSIONS, "support", ROLE_PERMISSIONS["support"] | {"settings.view"}
    )
    change = {
        "wording": {"title": "Money in", "body": "{amount} arrived.", "sms": "Finvest: {amount}."},
        "reason": "Shorter",
    }
    route = "/staff/notification-wordings/payment_confirmed/proposals"
    await api.staff("support")
    assert (await api.http.get("/staff/notification-wordings")).status_code == 200
    assert (await api.http.post(route, json=change)).status_code == 403
    await api.staff()
    proposal = (await api.http.post(route, json=change)).json()
    await api.staff("support")
    decision = await api.http.post(
        f"/staff/notification-wordings/proposals/{proposal['id']}/decision",
        json={"decision": "approved", "reason": "Fine"},
    )
    assert decision.status_code == 403
    await clear_wordings(migrator_database)
