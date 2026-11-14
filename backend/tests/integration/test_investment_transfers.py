"""Sending an investment to another client, against real PostgreSQL: the request, the two
people who let a large one through, the worker's single ledger entry, and the fences."""

import asyncio
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.audit import service as audit
from app.modules.closures import service as closures
from app.modules.identity import client_locks
from app.modules.investments import accrual, transfers
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.investments.domain.transfers import (
    NOT_POSSIBLE,
    HeldFor,
    TransferPurpose,
    TransferStatus,
)
from app.modules.investments.holdings import HOLDING
from app.modules.ledger import service as ledger
from app.modules.screening import contract as screening
from app.modules.screening import monitoring
from app.modules.screening.domain.monitoring import Kind, Transaction
from app.modules.screening.monitor_jobs import MonitoringJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import (
    PRESENT,
    account_balance,
    approved_destination,
    at,
    pay,
    request,
    today,
)
from tests.support.jobs import run_due
from tests.support.kyc import verify
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


async def person(app_database: Database, *, verified: bool = True) -> tuple[Actor, str]:
    """A client with a sign-in account and, unless told otherwise, an approved identity check.
    Returns the client and their mobile number."""
    client_id = push_id()
    phone = f"+256772{secrets.randbelow(1_000_000):06d}"
    async with app_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO identity_clients (id, phone, password_hash, password_changed_at, "
                "created_at) VALUES (:id, :phone, 'x', now(), now())"
            ),
            {"id": client_id, "phone": phone},
        )
    client = Actor("client", client_id)
    if verified:
        await verify(app_database, client, PRESENT)
    return client, phone


async def number_of(app_database: Database, client: Actor) -> str:
    async with app_database.actor_transaction(client) as session:
        found = await session.scalar(
            text("SELECT account_number FROM kyc_checks WHERE client_id = :id"), {"id": client.id}
        )
    assert isinstance(found, str)
    return found


async def running(
    app_database: Database, worker_database: Database, client: Actor, amount: int = 5_000_000
) -> investments.Investment:
    investment = await request(app_database, client, PRESENT, amount=ugx(amount))
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment.id, confirmed_on=today(), clock=PRESENT
        )


async def earn(worker_database: Database, investment_id: str) -> investments.Investment:
    """Interest posted for the next few days. The database only knows today, so the run is
    dated ahead rather than the investment started in the past."""
    day = today() + timedelta(days=3)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.accrue_investment(session, investment_id, day, at(day))
        return await investments.get_investment(session, investment_id)


async def send(
    app_database: Database,
    client: Actor,
    investment_id: str,
    to: str,
    amount: Money,
    *,
    idempotency_key: str | None = None,
    restricted_until: object = None,
    whole: bool = False,
) -> transfers.Transfer:
    async with app_database.actor_transaction(client) as session:
        return await transfers.send_investment(
            session,
            client,
            investment_id,
            to_account_number=to,
            amount=amount,
            message="For the wedding",
            purpose=TransferPurpose.GIFT,
            purpose_note=None,
            restricted_until=restricted_until,  # type: ignore[arg-type]
            idempotency_key=idempotency_key or key(),
            clock=PRESENT,
            whole=whole,
        )


async def carry_out(
    worker_database: Database, transfer_id: str, clock: FakeClock = PRESENT
) -> transfers.Transfer:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await transfers.carry_out(session, transfer_id, clock)


async def investment(worker_database: Database, investment_id: str) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.get_investment(session, investment_id)


async def interest_owed(worker_database: Database, item: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(session, accrual.interest_account(item), FakeClock())
        return (await ledger.get_balance(session, account.id)).minor


async def notices(app_database: Database, client: Actor, event: str) -> list[str]:
    async with app_database.actor_transaction(client) as session:
        rows = await session.execute(
            text("SELECT body FROM notifications WHERE client_id = :id AND event = :event"),
            {"id": client.id, "event": event},
        )
        return [row.body for row in rows.all()]


async def jobs_for(worker_database: Database, transfer_id: str) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        found = await session.scalar(
            text("SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :id"),
            {"kind": transfers.CARRY_OUT_KIND, "id": transfer_id},
        )
    return int(found or 0)


async def test_part_of_an_investment_moves_to_the_recipient_in_one_entry(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await earn(worker_database, (await running(app_database, worker_database, sender)).id)
    earned = source.accrued_net.minor
    assert earned > 0
    value = 5_000_000 + earned

    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    assert (asked.status, asked.held, asked.whole) == (TransferStatus.WAITING, False, False)
    assert (asked.sender_name, asked.recipient_name) == ("Amara N.", "Amara N.")
    assert await jobs_for(worker_database, asked.id) == 1

    done = await carry_out(worker_database, asked.id)
    assert done.status is TransferStatus.DONE
    assert done.sent == ugx(1_000_000)
    assert done.received_investment_id is not None and done.kept_investment_id is not None

    closed = await investment(worker_database, source.id)
    received = await investment(worker_database, done.received_investment_id)
    kept = await investment(worker_database, done.kept_investment_id)
    assert closed.status is InvestmentStatus.TRANSFERRED
    assert (received.client_id, received.amount) == (recipient.id, ugx(1_000_000))
    assert (kept.client_id, kept.amount) == (sender.id, ugx(value - 1_000_000))
    for opened in (received, kept):
        assert opened.status is InvestmentStatus.ACTIVE
        assert opened.start_date == today()
        assert opened.maturity_date == source.maturity_date
        assert (opened.terms.annual_rate_bps, opened.terms.tax_bps) == (
            source.terms.annual_rate_bps,
            source.terms.tax_bps,
        )
        assert opened.accrued_gross.is_zero
    # The old holding and its interest are empty; the new holdings hold exactly the split.
    assert await account_balance(worker_database, closed, HOLDING) == 0
    assert await interest_owed(worker_database, closed) == 0
    assert await account_balance(worker_database, received, HOLDING) == 1_000_000
    assert await account_balance(worker_database, kept, HOLDING) == value - 1_000_000
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await ledger.find_balance_drift(session) == []
    # Running it again changes nothing.
    assert await carry_out(worker_database, asked.id) == done

    # Both are told, without the amount.
    assert await notices(app_database, sender, "investment_sent") == [
        f"Your {source.product_name} went to Amara N.. See what you kept in the app."
    ]
    received_notice = await notices(app_database, recipient, "investment_received")
    assert len(received_notice) == 1 and "1,000,000" not in received_notice[0]

    async with app_database.actor_transaction(recipient) as session:
        seen = await transfers.client_transfers(session, recipient)
        theirs = await investments.client_investment(
            session, recipient, done.received_investment_id, PRESENT
        )
    assert [(t.direction, t.investment_id, t.amount) for t in seen] == [
        ("received", done.received_investment_id, ugx(1_000_000))
    ]
    assert theirs.value == ugx(1_000_000)
    async with app_database.actor_transaction(new_staff()) as session:
        events = await audit.events_for_resource(session, "investment_transfer", asked.id)
    assert [event.action for event in events][:2] == [
        "investments.transfer.done",
        "investments.transfer.requested",
    ]


async def test_sending_the_whole_value_leaves_the_sender_nothing(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await earn(worker_database, (await running(app_database, worker_database, sender)).id)
    value = source.amount + source.accrued_net

    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), value
    )
    assert asked.whole
    done = await carry_out(worker_database, asked.id)
    assert done.kept_investment_id is None and done.received_investment_id is not None
    received = await investment(worker_database, done.received_investment_id)
    assert received.amount == value


async def test_sending_everything_includes_interest_added_since_the_client_looked(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    started = await running(app_database, worker_database, sender)
    # The client saw the principal alone; interest was added before they confirmed.
    seen = started.amount
    source = await earn(worker_database, started.id)
    assert source.accrued_net.is_positive

    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), seen, whole=True
    )
    assert asked.whole and asked.amount == source.amount + source.accrued_net
    done = await carry_out(worker_database, asked.id)
    assert done.kept_investment_id is None


async def test_a_large_transfer_waits_for_two_members_of_staff(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender, amount=12_000_000)

    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(11_000_000)
    )
    assert (asked.status, asked.held) == (TransferStatus.HELD, True)
    assert await jobs_for(worker_database, asked.id) == 0
    assert len(await notices(app_database, sender, "investment_transfer_held")) == 1

    first, second = new_staff(), new_staff()
    async with app_database.actor_transaction(first) as session:
        proposed = await transfers.propose_release(
            session, first, asked.id, "Known family", PRESENT
        )
    assert proposed.status is TransferStatus.RELEASE_PROPOSED
    async with app_database.actor_transaction(first) as session:
        with pytest.raises(ApplicationError) as refused:
            await transfers.approve_release(session, first, asked.id, "Mine", PRESENT)
    assert refused.value.code == "forbidden"
    # Nor can the proposer get round it in the database.
    async with app_database.actor_transaction(first) as session:
        with pytest.raises(DBAPIError, match="second member of staff"):
            await session.execute(
                text(
                    "UPDATE investment_transfers SET status = 'waiting', released_by = :me, "
                    "released_at = now() WHERE id = :id"
                ),
                {"me": first.id, "id": asked.id},
            )
    async with app_database.actor_transaction(second) as session:
        released = await transfers.approve_release(session, second, asked.id, "Checked", PRESENT)
    assert released.status is TransferStatus.WAITING
    assert await jobs_for(worker_database, asked.id) == 1
    done = await carry_out(worker_database, asked.id)
    assert done.status is TransferStatus.DONE


async def test_small_transfers_on_one_day_add_up_to_the_review(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    to = await number_of(app_database, recipient)
    first = await running(app_database, worker_database, sender, amount=6_000_000)
    second = await running(app_database, worker_database, sender, amount=6_000_000)

    assert (await send(app_database, sender, first.id, to, ugx(6_000_000))).held is False
    assert (await send(app_database, sender, second.id, to, ugx(6_000_000))).held is True


async def test_a_held_transfer_can_be_declined_or_cancelled_and_nothing_moves(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    to = await number_of(app_database, recipient)
    source = await running(app_database, worker_database, sender, amount=12_000_000)

    asked = await send(app_database, sender, source.id, to, ugx(11_000_000))
    reviewer = new_staff()
    async with app_database.actor_transaction(reviewer) as session:
        declined = await transfers.decline_transfer(
            session, reviewer, asked.id, "Could not confirm the recipient", PRESENT
        )
    assert declined.status is TransferStatus.DECLINED
    assert len(await notices(app_database, sender, "investment_transfer_declined")) == 1
    assert (await investment(worker_database, source.id)).status is InvestmentStatus.ACTIVE

    again = await send(app_database, sender, source.id, to, ugx(11_000_000))
    async with app_database.actor_transaction(sender) as session:
        cancelled = await transfers.cancel_transfer(session, sender, again.id, PRESENT)
    assert cancelled.status is TransferStatus.CANCELLED
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(ApplicationError) as refused:
            await transfers.cancel_transfer(session, sender, again.id, PRESENT)
    assert refused.value.code == "conflict"


async def test_a_transfer_that_can_no_longer_be_made_fails_and_moves_nothing(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    assert source.maturity_date is not None

    # The worker reaches it only on the maturity date: the maturity run decides it now.
    failed = await carry_out(worker_database, asked.id, at(source.maturity_date))
    assert (failed.status, failed.failure_code) == (TransferStatus.FAILED, "matures_today")
    assert (await investment(worker_database, source.id)).status is InvestmentStatus.ACTIVE
    assert await account_balance(worker_database, source, HOLDING) == 5_000_000
    assert len(await notices(app_database, sender, "investment_transfer_failed")) == 1


async def test_what_a_client_cannot_send(app_database: Database, worker_database: Database) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    stranger, _ = await person(app_database, verified=False)
    to = await number_of(app_database, recipient)
    source = await running(app_database, worker_database, sender)

    async def refused(**changes: object) -> ApplicationError:
        arguments: dict[str, object] = {"to": to, "amount": ugx(1_000_000)}
        arguments.update(changes)
        with pytest.raises(ApplicationError) as raised:
            await send(
                app_database,
                changes.pop("client", sender),  # type: ignore[arg-type]
                source.id,
                arguments["to"],  # type: ignore[arg-type]
                arguments["amount"],  # type: ignore[arg-type]
                restricted_until=changes.get("restricted_until"),
            )
        return raised.value

    def field(error: ApplicationError) -> tuple[str, str]:
        problem = error.fields[0]
        return str(problem.field_path[-1]), problem.code

    assert field(await refused(amount=ugx(50_000))) == ("amount_minor", "below_product_minimum")
    assert field(await refused(amount=ugx(4_950_000))) == (
        "amount_minor",
        "remainder_below_product_minimum",
    )
    assert field(await refused(to=await number_of(app_database, sender))) == ("to", "to_yourself")
    assert field(await refused(to="1009999990")) == ("to", "no_such_client")
    restricted = await refused(restricted_until=PRESENT.now() + timedelta(hours=3))
    assert (restricted.code, field(restricted)) == (
        "forbidden",
        ("body", "restricted_after_code_sign_in"),
    )
    # Someone else's investment is not found.
    async with app_database.actor_transaction(recipient) as session:
        with pytest.raises(ApplicationError) as hidden:
            await transfers.send_investment(
                session,
                recipient,
                source.id,
                to_account_number=to,
                amount=ugx(1_000_000),
                message=None,
                purpose=TransferPurpose.FAMILY_SUPPORT,
                purpose_note=None,
                restricted_until=None,
                idempotency_key=key(),
                clock=PRESENT,
            )
    assert hidden.value.code == "not_found"
    # A client who is not approved is not found as a recipient.
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(ApplicationError) as missing:
            await transfers.find_recipient(session, sender, "0" + "7" * 9)
    assert missing.value.code == "not_found"
    assert stranger.id is not None


async def test_a_recipient_is_found_by_number_or_phone_and_shown_by_first_name(
    app_database: Database, worker_database: Database
) -> None:
    sender, sender_phone = await person(app_database)
    recipient, phone = await person(app_database)
    number = await number_of(app_database, recipient)
    async with app_database.actor_transaction(sender) as session:
        by_number = await transfers.find_recipient(session, sender, f"{number[:4]} {number[4:]}")
        by_phone = await transfers.find_recipient(session, sender, "0" + phone[4:])
        with pytest.raises(ApplicationError) as yourself:
            await transfers.find_recipient(session, sender, sender_phone)
    assert by_number == by_phone == transfers.RecipientView(number, "Amara N.")
    assert yourself.value.fields[0].code == "to_yourself"


async def test_an_open_transfer_stops_an_early_withdrawal_and_a_second_transfer(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    to = await number_of(app_database, recipient)
    source = await running(app_database, worker_database, sender, amount=12_000_000)
    destination_id = await approved_destination(app_database, sender, PRESENT)
    await send(app_database, sender, source.id, to, ugx(11_000_000))

    with pytest.raises(ApplicationError) as second:
        await send(app_database, sender, source.id, to, ugx(1_000_000))
    assert second.value.code == "conflict"
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(ApplicationError) as withdrawal:
            await investments.request_withdrawal(
                session,
                sender,
                source.id,
                destination_id=destination_id,
                idempotency_key=key(),
                clock=FakeClock(PRESENT.now() + timedelta(days=2)),
            )
    assert withdrawal.value.code == "conflict"


async def test_a_repeated_request_is_the_same_transfer(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    to = await number_of(app_database, recipient)
    source = await running(app_database, worker_database, sender)
    same = key()
    first = await send(app_database, sender, source.id, to, ugx(1_000_000), idempotency_key=same)
    again = await send(app_database, sender, source.id, to, ugx(1_000_000), idempotency_key=same)
    assert again.id == first.id
    with pytest.raises(ApplicationError):
        await send(app_database, sender, source.id, to, ugx(2_000_000), idempotency_key=same)


async def test_two_transfers_of_one_investment_at_once_make_one(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    to = await number_of(app_database, recipient)
    source = await running(app_database, worker_database, sender)
    barrier = AsyncBarrier(2)

    async def attempt() -> str:
        await barrier.wait()
        try:
            await send(app_database, sender, source.id, to, ugx(1_000_000))
        except ApplicationError as error:
            return error.code
        return "sent"

    outcomes = sorted(await asyncio.gather(attempt(), attempt()))
    assert outcomes == ["conflict", "sent"]


async def test_the_database_fences_who_does_what(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    to = await number_of(app_database, recipient)
    source = await running(app_database, worker_database, sender)
    asked = await send(app_database, sender, source.id, to, ugx(1_000_000))

    # A client cannot record an outcome, nor change what is sent.
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "UPDATE investment_transfers SET status = 'cancelled', cancelled_at = now() "
                    "WHERE id = :id"
                ),
                {"id": asked.id},
            )
    # The recipient sees a transfer only once it is done, and cannot change it.
    async with app_database.actor_transaction(recipient) as session:
        with pytest.raises(ApplicationError):
            await transfers.client_transfer(session, recipient, asked.id)
    # Nobody but the worker opens an investment for a transfer, or closes one as transferred.
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "UPDATE investments SET status = 'transferred', transferred_at = now(), "
                    "transfer_entry_id = :id WHERE id = :investment"
                ),
                {"id": push_id(), "investment": source.id},
            )
    # A client cannot ask on someone else's behalf.
    async with app_database.actor_transaction(recipient) as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO investment_transfers (id, investment_id, sender_client_id, "
                    "recipient_client_id, sender_name, recipient_name, currency, amount_minor, "
                    "whole, purpose, status, held, requested_at) VALUES (:id, :investment, "
                    ":sender, :recipient, 'A.', 'B.', 'UGX', 100000, false, 'gift', 'waiting', "
                    "false, now())"
                ),
                {
                    "id": push_id(),
                    "investment": source.id,
                    "sender": sender.id,
                    "recipient": recipient.id,
                },
            )


async def hold(worker_database: Database, client: Actor) -> None:
    """Put the client on a screening hold: a screen asked for and not yet back."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await screening.request_screening(
            session,
            client_id=str(client.id),
            reason=screening.Reason.RESCREEN,
            idempotency_key=key(),
            now=PRESENT.now(),
        )


async def test_a_sender_on_a_screening_hold_is_held_for_staff_and_told_nothing_more(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    await hold(worker_database, sender)

    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    # Small, but held, and the sender hears what they would for a large one.
    assert (asked.status, asked.held, asked.held_for) == (
        TransferStatus.HELD,
        True,
        HeldFor.SCREENING,
    )
    assert await jobs_for(worker_database, asked.id) == 0
    assert len(await notices(app_database, sender, "investment_transfer_held")) == 1

    first, second = new_staff(), new_staff()
    async with app_database.actor_transaction(first) as session:
        await transfers.propose_release(session, first, asked.id, "Looks fine", PRESENT)
    async with app_database.actor_transaction(second) as session:
        with pytest.raises(ApplicationError) as refused:
            await transfers.approve_release(session, second, asked.id, "Checked", PRESENT)
    assert refused.value.fields[0].code == "screening_hold"
    # Nor around the service.
    async with app_database.actor_transaction(second) as session:
        with pytest.raises(DBAPIError, match="held by screening"):
            await session.execute(
                text(
                    "UPDATE investment_transfers SET status = 'waiting', released_by = :me, "
                    "released_at = now() WHERE id = :id"
                ),
                {"me": second.id, "id": asked.id},
            )
    async with app_database.actor_transaction(second) as session:
        declined = await transfers.decline_transfer(
            session, second, asked.id, "Compliance hold", PRESENT
        )
    assert declined.status is TransferStatus.DECLINED


async def test_a_sender_on_hold_cannot_insert_an_unheld_transfer(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    await hold(worker_database, sender)
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(DBAPIError, match="asked for by the client signed in"):
            await session.execute(
                text(
                    "INSERT INTO investment_transfers (id, investment_id, sender_client_id, "
                    "recipient_client_id, sender_name, recipient_name, currency, amount_minor, "
                    "whole, purpose, status, held, requested_at) VALUES (:id, :investment, "
                    ":sender, :recipient, 'Amara N.', 'Brian K.', 'UGX', 1000000, false, 'gift', "
                    "'waiting', false, clock_timestamp())"
                ),
                {
                    "id": push_id(),
                    "investment": source.id,
                    "sender": sender.id,
                    "recipient": recipient.id,
                },
            )


async def test_a_recipient_on_a_screening_hold_fails_the_transfer_without_saying_why(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    assert asked.status is TransferStatus.WAITING
    await hold(worker_database, recipient)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await transfers.for_monitoring(session, asked.id) is None
    failed = await carry_out(worker_database, asked.id)
    assert (failed.status, failed.failure_code) == (TransferStatus.FAILED, NOT_POSSIBLE)
    assert (await investment(worker_database, source.id)).status is InvestmentStatus.ACTIVE
    assert len(await notices(app_database, sender, "investment_transfer_failed")) == 1


async def test_monitoring_reads_a_transfer_once_it_is_made(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    done = await carry_out(worker_database, asked.id)
    async with worker_database.actor_transaction(SYSTEM) as session:
        seen = await transfers.for_monitoring(session, asked.id)
    assert seen is not None
    assert (seen.sender_client_id, seen.recipient_client_id) == (sender.id, recipient.id)
    assert (seen.amount_minor, seen.currency.value) == (1_000_000, "UGX")
    assert seen.finished_at == done.finished_at


async def active_staff(app_database: Database, role: str = "operations") -> Actor:
    """A member of staff with an active account, for steps whose records name real staff."""
    staff_id = push_id()
    async with app_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO identity_staff (id, email, name, role, password_hash, status, "
                "created_at) VALUES (:id, :email, 'Grace Auma', :role, 'x', 'active', now())"
            ),
            {"id": staff_id, "email": f"staff.{staff_id.lower()}@finvestug.com", "role": role},
        )
    return Actor("staff", staff_id)


async def test_a_sender_locked_before_the_move_fails_the_transfer(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    proposer, decider = await active_staff(app_database), await active_staff(app_database)
    async with app_database.actor_transaction(proposer) as session:
        lock = await client_locks.propose(
            session, proposer, str(sender.id), "lock", "Phone stolen", PRESENT
        )
    async with app_database.actor_transaction(decider) as session:
        await client_locks.decide(session, decider, lock.id, "approved", "Confirmed", PRESENT)

    failed = await carry_out(worker_database, asked.id)
    assert (failed.status, failed.failure_code) == (TransferStatus.FAILED, "sender_not_available")
    assert (await investment(worker_database, source.id)).status is InvestmentStatus.ACTIVE
    assert await account_balance(worker_database, source, HOLDING) == 5_000_000


async def test_account_closure_and_transfers(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    # The sender cannot close while a transfer is open: the investment it is sent from still
    # runs until the money moves, so the running investment blocker already stops it.
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(ApplicationError) as blocked:
            await closures.ask(session, sender, "Moving abroad", PRESENT)
    assert "investments_running" in {problem.code for problem in blocked.value.fields}

    # The recipient, with nothing yet, may close; the transfer then fails and nothing moves.
    async with app_database.actor_transaction(recipient) as session:
        request = await closures.ask(session, recipient, "Moving abroad", PRESENT)
    preparer, closer = await active_staff(app_database), await active_staff(app_database)
    async with app_database.actor_transaction(preparer) as session:
        await closures.prepare(session, preparer, request.id, "Checked", PRESENT)
    async with app_database.actor_transaction(closer) as session:
        await closures.approve(session, closer, request.id, "Second check", PRESENT)
    failed = await carry_out(worker_database, asked.id)
    assert (failed.status, failed.failure_code) == (
        TransferStatus.FAILED,
        "recipient_not_available",
    )
    assert (await investment(worker_database, source.id)).status is InvestmentStatus.ACTIVE


async def test_a_client_who_asked_to_close_cannot_send(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    # A waiting request, saved straight away. Asking through the app is refused while an
    # investment runs, and a waiting request refuses a new one, so this reaches the guard
    # that sits behind those checks.
    request_id = push_id()
    async with app_database.actor_transaction(sender) as session:
        await session.execute(
            text(
                "INSERT INTO closure_requests (id, client_id, requested_at) "
                "VALUES (:id, :client_id, now())"
            ),
            {"id": request_id, "client_id": sender.id},
        )
    # Nothing should leave by a send while staff decide.
    with pytest.raises(ApplicationError) as refused:
        await send(
            app_database,
            sender,
            source.id,
            await number_of(app_database, recipient),
            ugx(1_000_000),
        )
    assert (refused.value.fields[0].field_path, refused.value.fields[0].code) == (
        ["account"],
        "closing",
    )
    # Leave no open request behind for the staff queue other tests read.
    async with app_database.actor_transaction(sender) as session:
        await closures.cancel(session, sender, request_id, PRESENT)


async def test_nothing_is_sent_to_a_client_whose_request_to_close_waits(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    to = await number_of(app_database, recipient)
    asked = await send(app_database, sender, source.id, to, ugx(1_000_000))
    # The recipient asks to close before the money moves: a waiting request, saved straight
    # away, as in the test above.
    request_id = push_id()
    async with app_database.actor_transaction(recipient) as session:
        await session.execute(
            text(
                "INSERT INTO closure_requests (id, client_id, requested_at) "
                "VALUES (:id, :client_id, now())"
            ),
            {"id": request_id, "client_id": recipient.id},
        )
    failed = await carry_out(worker_database, asked.id)
    assert (failed.status, failed.failure_code) == (
        TransferStatus.FAILED,
        "recipient_not_available",
    )
    assert (await investment(worker_database, source.id)).status is InvestmentStatus.ACTIVE
    assert await account_balance(worker_database, source, HOLDING) == 5_000_000
    # A new send to them is refused as if there were no such client.
    with pytest.raises(ApplicationError) as refused:
        await send(app_database, sender, source.id, to, ugx(1_000_000))
    assert (refused.value.fields[0].field_path, refused.value.fields[0].code) == (
        ["to"],
        "no_such_client",
    )
    async with app_database.actor_transaction(recipient) as session:
        await closures.cancel(session, recipient, request_id, PRESENT)


async def monitored(worker_database: Database, transfer_id: str) -> list[tuple[str, str, int]]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT client_id, kind, amount_minor FROM monitoring_transactions "
                "WHERE source_id = :id ORDER BY kind"
            ),
            {"id": transfer_id},
        )
    return [(row.client_id, row.kind, int(row.amount_minor)) for row in rows]


async def monitoring_cases(worker_database: Database, client_id: str) -> list[str]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.scalars(
            text("SELECT code FROM compliance_cases WHERE client_id = :id ORDER BY code"),
            {"id": client_id},
        )
    return list(rows)


async def test_a_made_transfer_is_monitored_for_both_clients(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    assert sender.id is not None and recipient.id is not None
    source = await running(app_database, worker_database, sender)
    # What came in for the sender over the last week, as monitoring saw it.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await monitoring.record(
            session,
            client_id=sender.id,
            transaction=Transaction(
                push_id(), Kind.DEPOSIT, 5_000_000, Currency.UGX, PRESENT.now() - timedelta(days=1)
            ),
            clock=PRESENT,
        )
    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(3_000_000)
    )
    assert (await carry_out(worker_database, asked.id)).status is TransferStatus.DONE
    worker = Worker(
        worker_database,
        MonitoringJobs(worker_database, PRESENT).handlers(),
        WorkerConfig(),
        PRESENT,
    )
    await run_due(worker)

    assert sorted(await monitored(worker_database, asked.id)) == sorted(
        [(sender.id, "transfer_sent", 3_000_000), (recipient.id, "transfer_received", 3_000_000)]
    )
    # Most of what came in sent straight on: a case for the sender, and none for the recipient.
    assert await monitoring_cases(worker_database, sender.id) == ["quick_withdrawal"]
    assert await monitoring_cases(worker_database, recipient.id) == []

    # Asked again, nothing is recorded twice and no case opens twice.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await screening.watch_transfer(session, asked.id, PRESENT.now())
    await run_due(worker)
    assert len(await monitored(worker_database, asked.id)) == 2
    assert await monitoring_cases(worker_database, sender.id) == ["quick_withdrawal"]


async def test_a_transfer_that_failed_is_not_monitored(
    app_database: Database, worker_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    asked = await send(
        app_database, sender, source.id, await number_of(app_database, recipient), ugx(1_000_000)
    )
    assert source.maturity_date is not None
    failed = await carry_out(worker_database, asked.id, at(source.maturity_date))
    assert failed.status is TransferStatus.FAILED
    async with worker_database.actor_transaction(SYSTEM) as session:
        # Nothing asked for, and a stray request records nothing.
        assert (
            await session.scalar(
                text("SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :id"),
                {"kind": monitoring.MONITOR_TRANSFER_KIND, "id": asked.id},
            )
            == 0
        )
        await screening.watch_transfer(session, asked.id, PRESENT.now())
    await run_due(
        Worker(
            worker_database,
            MonitoringJobs(worker_database, PRESENT).handlers(),
            WorkerConfig(),
            PRESENT,
        )
    )
    assert await monitored(worker_database, asked.id) == []


async def test_a_sender_says_why_and_the_reason_never_changes(
    app_database: Database, worker_database: Database, migrator_database: Database
) -> None:
    sender, _ = await person(app_database)
    recipient, _ = await person(app_database)
    source = await running(app_database, worker_database, sender)
    to = await number_of(app_database, recipient)

    async def asked(purpose: TransferPurpose, note: str | None) -> transfers.Transfer:
        async with app_database.actor_transaction(sender) as session:
            return await transfers.send_investment(
                session,
                sender,
                source.id,
                to_account_number=to,
                amount=ugx(1_000_000),
                message=None,
                purpose=purpose,
                purpose_note=note,
                restricted_until=None,
                idempotency_key=key(),
                clock=PRESENT,
            )

    async def refusal(purpose: TransferPurpose, note: str | None) -> tuple[str, str]:
        with pytest.raises(ApplicationError) as refused:
            await asked(purpose, note)
        problem = refused.value.fields[0]
        return (str(problem.field_path[0]), problem.code)

    assert await refusal(TransferPurpose.OTHER, None) == ("purpose_note", "required")
    assert await refusal(TransferPurpose.OTHER, "   ") == ("purpose_note", "required")
    assert await refusal(TransferPurpose.OTHER, "x" * 141) == ("purpose_note", "too_long")
    assert await refusal(TransferPurpose.GIFT, "Birthday") == ("purpose_note", "only_for_other")

    sent = await asked(TransferPurpose.OTHER, "  School fees for my niece ")
    staff = await active_staff(app_database)
    async with app_database.actor_transaction(staff) as session:
        seen = await transfers.get_transfer(session, staff, sent.id)
    assert (seen.purpose, seen.purpose_note) == (TransferPurpose.OTHER, "School fees for my niece")

    # The database refuses a transfer without a reason, and any change to one.
    async with app_database.actor_transaction(sender) as session:
        with pytest.raises(DBAPIError, match="says why it is sent"):
            await session.execute(
                text(
                    "INSERT INTO investment_transfers (id, investment_id, sender_client_id, "
                    "recipient_client_id, sender_name, recipient_name, currency, amount_minor, "
                    "whole, status, held, requested_at) VALUES (:id, :investment, :sender, "
                    ":recipient, 'Amara N.', 'Brian K.', 'UGX', 1000000, false, 'waiting', "
                    "false, clock_timestamp())"
                ),
                {
                    "id": push_id(),
                    "investment": source.id,
                    "sender": sender.id,
                    "recipient": recipient.id,
                },
            )
    # The 0241 guard already refuses every change but a status step. With it set aside, as
    # the owner of the tables could, the reason's own guard still refuses.
    async with migrator_database.actor_transaction(SYSTEM) as session:
        await session.execute(
            text("ALTER TABLE investment_transfers DISABLE TRIGGER investment_transfers_guard")
        )
        with pytest.raises(DBAPIError, match="why a transfer is sent never changes"):
            await session.execute(
                text("UPDATE investment_transfers SET purpose = 'gift' WHERE id = :id"),
                {"id": sent.id},
            )
