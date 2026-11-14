"""Sending an investment to another client. Every function works inside the caller's transaction.

The client finds the recipient by mobile number or Finvest account number and asks to send part
or all of a running investment. Nothing moves in the client's transaction: it saves the
transfer and, unless the transfer is large, queues the worker. A large one is held until two
members of staff let it through: one proposes, a different one approves. One person may decline
it, and the sender may cancel it while it is held, as for a payout held for review.

The worker then, in one transaction:

1. pays the investment its interest up to today, if the daily run has not yet,
2. opens the recipient's investment for the amount sent and, unless everything was sent, the
   sender's for the rest, both on the old one's product, rate, tax and maturity date,
3. posts one ledger entry (`investment.transferred`):

       debit   client.holding           sender, old investment     principal
       debit   client.interest_accrued  sender, old investment     interest after tax so far
       credit  client.holding           recipient, new investment  the amount sent
       credit  client.holding           sender, new investment     the rest

4. starts both new investments today, closes the old one as `transferred`, and tells both
   clients without naming the amount.

No provider is called and no money leaves Finvest. The pure rules are in
`domain/transfers.py`. The database side is migrations 0240 and 0241. See docs/investing.md.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import JobRequest, enqueue, publish
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.goals.events import FOLLOW_ROLLOVER
from app.modules.identity.client_locks import client_id_for_phone, client_status
from app.modules.investments import accrual
from app.modules.investments.catalogue import invalid, product_from_row, product_row
from app.modules.investments.domain.interest import maturity_date
from app.modules.investments.domain.investments import InvestmentStatus, pay_by
from app.modules.investments.domain.transfers import (
    COUNTED,
    MAX_MESSAGE_LENGTH,
    NOT_POSSIBLE,
    HeldFor,
    TransferRefusal,
    TransferStatus,
    check_transfer,
    display_name,
    needs_review,
    remaining_term,
    split,
)
from app.modules.investments.holdings import (
    INVESTMENT_RESOURCE,
    Investment,
    get_investment,
    holding_account,
    investment_from_row,
    select_investments,
)
from app.modules.investments.maturity import out_of_holding
from app.modules.kyc import service as kyc
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import EntryDraft, credit
from app.modules.notifications import contract as notifications
from app.modules.payouts import payouts
from app.modules.screening import contract as screening
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

SEND_OPERATION = "investments.send_investment"
CARRY_OUT_KIND = "investments.carry_out_transfer"
TRANSFERRED_TOPIC = "investments.transferred"
RESOURCE = "investment_transfer"
MAX_REASON_LENGTH = 500
MAX_LISTED = 100
logger = structlog.get_logger()

_ACCOUNT_NUMBER = re.compile(r"[1-9]\d{9}")
Direction = Literal["sent", "received"]


@dataclass(frozen=True, slots=True)
class RecipientView:
    """Who the client is about to send to, as they are shown: never the full name."""

    account_number: str
    name: str


@dataclass(frozen=True, slots=True)
class Transfer:
    """A transfer as staff and the worker see it."""

    id: str
    investment_id: str
    sender_client_id: str
    recipient_client_id: str
    sender_name: str
    recipient_name: str
    amount: Money
    whole: bool
    message: str | None
    status: TransferStatus
    held: bool
    # Why it was held. Staff only: the client is never told.
    held_for: HeldFor | None
    requested_at: datetime
    release_proposed_by: str | None
    release_proposed_at: datetime | None
    release_reason: str | None
    released_by: str | None
    released_at: datetime | None
    declined_by: str | None
    declined_at: datetime | None
    decision_reason: str | None
    cancelled_at: datetime | None
    finished_at: datetime | None
    sent: Money | None
    received_investment_id: str | None
    kept_investment_id: str | None
    entry_id: str | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class ClientTransfer:
    """What one of the two clients sees of a transfer. No staff names or notes."""

    id: str
    direction: Direction
    # The other client, as first given name and surname initial.
    other_name: str
    status: TransferStatus
    # What was asked for, or for the recipient what they got once it is done.
    amount: Money
    whole: bool
    message: str | None
    requested_at: datetime
    finished_at: datetime | None
    # The sender's: the investment sent from, and the one they kept. The recipient's: the
    # investment they were given, once done.
    investment_id: str | None
    kept_investment_id: str | None
    failure_code: str | None


_COLUMNS = (
    "id, investment_id, sender_client_id, recipient_client_id, sender_name, recipient_name, "
    "currency, amount_minor, whole, message, status, held, held_for, requested_at, "
    "release_proposed_by, "
    "release_proposed_at, release_reason, released_by, released_at, declined_by, declined_at, "
    "decision_reason, cancelled_at, finished_at, sent_minor, received_investment_id, "
    "kept_investment_id, entry_id, failure_code"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM investment_transfers WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(
    f"SELECT {_COLUMNS} FROM investment_transfers WHERE id = :id FOR UPDATE"  # noqa: S608
)
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} FROM investment_transfers "  # noqa: S608
    "WHERE sender_client_id = :client_id "
    "OR (recipient_client_id = :client_id AND status = 'done') "
    "ORDER BY requested_at DESC, id DESC LIMIT :limit"
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM investment_transfers "  # noqa: S608
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:client_id AS text) IS NULL OR :client_id IN (sender_client_id, "
    "recipient_client_id)) "
    "ORDER BY requested_at DESC, id DESC LIMIT :limit"
)
_HELD = text(
    f"SELECT {_COLUMNS} FROM investment_transfers "  # noqa: S608
    "WHERE status IN ('held', 'release_proposed') ORDER BY requested_at, id LIMIT :limit"
)
_SENT_TODAY = text(
    "SELECT coalesce(sum(amount_minor), 0) FROM investment_transfers "
    "WHERE sender_client_id = :client_id AND currency = :currency "
    "AND status = ANY(CAST(:counted AS text[])) "
    "AND (requested_at AT TIME ZONE 'Africa/Kampala')::date = :today"
)
_INSERT = text(
    "INSERT INTO investment_transfers (id, investment_id, sender_client_id, "
    "recipient_client_id, sender_name, recipient_name, currency, amount_minor, whole, message, "
    "status, held, held_for, requested_at) VALUES (:id, :investment_id, :sender, :recipient, "
    ":sender_name, :recipient_name, :currency, :amount_minor, :whole, :message, :status, "
    ":held, :held_for, clock_timestamp())"
)
_PROPOSE = text(
    "UPDATE investment_transfers SET status = 'release_proposed', release_proposed_by = :by, "
    "release_proposed_at = :now, release_reason = :reason WHERE id = :id"
)
_UNPROPOSE = text(
    "UPDATE investment_transfers SET status = 'held', release_proposed_by = NULL, "
    "release_proposed_at = NULL, release_reason = NULL WHERE id = :id"
)
_RELEASE = text(
    "UPDATE investment_transfers SET status = 'waiting', released_by = :by, released_at = :now "
    "WHERE id = :id"
)
_DECLINE = text(
    "UPDATE investment_transfers SET status = 'declined', declined_by = :by, declined_at = :now, "
    "decision_reason = :reason WHERE id = :id"
)
_CANCEL = text(
    "UPDATE investment_transfers SET status = 'cancelled', cancelled_at = :now WHERE id = :id"
)
_FINISH = text(
    "UPDATE investment_transfers SET status = :status, finished_at = :now, sent_minor = :sent, "
    "received_investment_id = :received, kept_investment_id = :kept, entry_id = :entry_id, "
    "failure_code = :failure_code WHERE id = :id"
)
_INVESTMENT_LOCKED = select_investments("WHERE i.id = :id FOR UPDATE OF i")
_DATABASE_NOW = text("SELECT clock_timestamp()")
_INSERT_INVESTMENT = text(
    "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
    "annual_rate_bps, tax_bps, term_days, at_maturity, payout_destination_id, created_at, "
    "pay_by, updated_at, source_type, source_id) SELECT :id, :client_id, product_id, currency, "
    ":principal_minor, annual_rate_bps, tax_bps, :term_days, :at_maturity, :destination_id, "
    ":created_at, :pay_by, :now, :source_type, :transfer_id FROM investments WHERE id = :from_id"
)
_START = text(
    "UPDATE investments SET status = 'active', start_date = :start_date, "
    "maturity_date = :maturity_date, started_at = :now, start_entry_id = :entry_id, "
    "updated_at = :now WHERE id = :id"
)
_CLOSE = text(
    "UPDATE investments SET status = 'transferred', transferred_at = :now, "
    "transfer_entry_id = :entry_id, updated_at = :now WHERE id = :id"
)
# One client's sends run one at a time, so the day's total for the review cannot be raced.
_CLIENT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# The client's commands --------------------------------------------------------------------------


async def find_recipient(session: AsyncSession, client: Actor, to: str) -> RecipientView:
    """Another approved client, by Finvest account number or mobile number, as shown to the
    sender. `not_found` when nobody approved has it, and `to_yourself` for the client's own."""
    client_id = _client(client)
    recipient = await _recipient(session, to)
    if recipient is None:
        raise ApplicationError("not_found")
    if recipient.client_id == client_id:
        raise invalid("to", TransferRefusal.TO_YOURSELF.value)
    return RecipientView(
        recipient.account_number, display_name(recipient.surname, recipient.given_names)
    )


async def send_investment(
    session: AsyncSession,
    client: Actor,
    investment_id: str,
    *,
    to_account_number: str,
    amount: Money,
    message: str | None,
    restricted_until: datetime | None,
    idempotency_key: str,
    clock: Clock,
    whole: bool = False,
) -> Transfer:
    """Ask to send `amount` of a running investment to the client with this account number.

    With `whole`, everything is sent: the value at the request, whatever interest was added
    since the client saw `amount`, and the interest added before it moves. A retry with the same
    key returns the same transfer, and the same key for anything else is refused. Refused
    while the client's sign-in is restricted after a code or a password reset, because the
    money goes to someone else.
    """
    sender_id = _client(client)
    now = clock.now()
    if restricted_until is not None and restricted_until > now:
        raise ApplicationError(
            "forbidden",
            fields=[FieldProblem(field_path=["body"], code="restricted_after_code_sign_in")],
        )
    cleaned = (message or "").strip() or None
    if cleaned is not None and len(cleaned) > MAX_MESSAGE_LENGTH:
        raise invalid("message", "too_long")

    async def command() -> Receipt:
        transfer_id = await _ask(
            session,
            client,
            sender_id,
            investment_id,
            to_account_number,
            amount,
            cleaned,
            clock,
            whole=whole,
        )
        return Receipt(201, {"transfer_id": transfer_id})

    outcome = await run_once(
        session,
        actor=client,
        operation=SEND_OPERATION,
        key=idempotency_key,
        request={
            "investment_id": investment_id,
            "to": to_account_number,
            "amount_minor": amount.minor,
            "currency": amount.currency.value,
            "message": cleaned,
            "whole": whole,
        },
        now=now,
        command=command,
    )
    return await _transfer(session, str(outcome.receipt.response["transfer_id"]))


async def cancel_transfer(
    session: AsyncSession, client: Actor, transfer_id: str, clock: Clock
) -> Transfer:
    """The sender takes back a transfer that is still held for staff. Nothing had moved."""
    sender_id = _client(client)
    transfer = await _locked(session, transfer_id)
    if transfer.sender_client_id != sender_id:
        raise ApplicationError("not_found")
    if transfer.status not in (TransferStatus.HELD, TransferStatus.RELEASE_PROPOSED):
        raise ApplicationError("conflict")
    await session.execute(_CANCEL, {"id": transfer.id, "now": clock.now()})
    cancelled = await _transfer(session, transfer.id)
    await _audit(session, AuditActor.client(sender_id), "change", "cancelled", cancelled, clock)
    return cancelled


async def client_transfers(
    session: AsyncSession, client: Actor, *, limit: int = MAX_LISTED
) -> list[ClientTransfer]:
    """What the client sent, and what they received once it is done, newest first."""
    client_id = _client(client)
    rows = await session.execute(
        _FOR_CLIENT, {"client_id": client_id, "limit": max(1, min(limit, MAX_LISTED))}
    )
    return [_client_view(_from_row(row), client_id) for row in rows.all()]


async def client_transfer(session: AsyncSession, client: Actor, transfer_id: str) -> ClientTransfer:
    client_id = _client(client)
    row = (await session.execute(_BY_ID, {"id": transfer_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    transfer = _from_row(row)
    # A recipient learns of a transfer only once it is theirs.
    if transfer.sender_client_id != client_id and (
        transfer.recipient_client_id != client_id or transfer.status is not TransferStatus.DONE
    ):
        raise ApplicationError("not_found")
    return _client_view(transfer, client_id)


# Staff ------------------------------------------------------------------------------------------


async def held_transfers(
    session: AsyncSession, staff: Actor, *, limit: int = 100
) -> list[Transfer]:
    """Transfers waiting for staff, oldest first. For the review queue and the approvals inbox."""
    _staff(staff)
    rows = await session.execute(_HELD, {"limit": max(1, min(limit, MAX_LISTED))})
    return [_from_row(row) for row in rows.all()]


async def list_transfers(
    session: AsyncSession,
    staff: Actor,
    *,
    status: TransferStatus | None = None,
    client_id: str | None = None,
    limit: int = 50,
) -> list[Transfer]:
    _staff(staff)
    rows = await session.execute(
        _LIST,
        {
            "status": status.value if status else None,
            "client_id": client_id,
            "limit": max(1, min(limit, MAX_LISTED)),
        },
    )
    return [_from_row(row) for row in rows.all()]


async def get_transfer(session: AsyncSession, staff: Actor, transfer_id: str) -> Transfer:
    _staff(staff)
    return await _transfer(session, transfer_id)


async def propose_release(
    session: AsyncSession, staff: Actor, transfer_id: str, reason: str, clock: Clock
) -> Transfer:
    """The first person proposes letting a held transfer through. A different one decides."""
    staff_id = _staff(staff)
    reason = _reason(reason)
    transfer = await _locked(session, transfer_id)
    if transfer.status is not TransferStatus.HELD:
        raise ApplicationError("conflict")
    await session.execute(
        _PROPOSE, {"id": transfer.id, "by": staff_id, "now": clock.now(), "reason": reason}
    )
    proposed = await _transfer(session, transfer.id)
    await _audit(
        session, AuditActor.staff(staff_id), "change", "release_proposed", proposed, clock, reason
    )
    return proposed


async def withdraw_release(
    session: AsyncSession, staff: Actor, transfer_id: str, clock: Clock
) -> Transfer:
    """The proposer takes their proposal back. The transfer is held again."""
    staff_id = _staff(staff)
    transfer = await _locked(session, transfer_id)
    if transfer.status is not TransferStatus.RELEASE_PROPOSED:
        raise ApplicationError("conflict")
    if transfer.release_proposed_by != staff_id:
        raise ApplicationError("forbidden")
    await session.execute(_UNPROPOSE, {"id": transfer.id})
    held = await _transfer(session, transfer.id)
    await _audit(session, AuditActor.staff(staff_id), "change", "release_withdrawn", held, clock)
    return held


async def approve_release(
    session: AsyncSession, staff: Actor, transfer_id: str, reason: str, clock: Clock
) -> Transfer:
    """A second person lets it through, and the worker is queued in this transaction."""
    staff_id = _staff(staff)
    reason = _reason(reason)
    transfer = await _locked(session, transfer_id)
    if transfer.status is not TransferStatus.RELEASE_PROPOSED:
        raise ApplicationError("conflict")
    if transfer.release_proposed_by == staff_id:
        raise ApplicationError("forbidden")
    now = clock.now()
    if await _on_hold(session, transfer.sender_client_id, now) or await _on_hold(
        session, transfer.recipient_client_id, now
    ):
        # Compliance decides the hold first. Staff may still decline the transfer.
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["status"], code="screening_hold")]
        )
    await session.execute(_RELEASE, {"id": transfer.id, "by": staff_id, "now": now})
    await _queue(session, transfer.id, now)
    released = await _transfer(session, transfer.id)
    await _audit(
        session, AuditActor.staff(staff_id), "approval", "released", released, clock, reason
    )
    return released


async def decline_transfer(
    session: AsyncSession, staff: Actor, transfer_id: str, reason: str, clock: Clock
) -> Transfer:
    """One person declines a held transfer, with a reason. Nothing had moved, so nothing does."""
    staff_id = _staff(staff)
    reason = _reason(reason)
    transfer = await _locked(session, transfer_id)
    if transfer.status not in (TransferStatus.HELD, TransferStatus.RELEASE_PROPOSED):
        raise ApplicationError("conflict")
    now = clock.now()
    await session.execute(
        _DECLINE, {"id": transfer.id, "by": staff_id, "now": now, "reason": reason}
    )
    declined = await _transfer(session, transfer.id)
    await _audit(
        session, AuditActor.staff(staff_id), "approval", "declined", declined, clock, reason
    )
    await _tell_sender(session, declined, "investment_transfer_declined", now)
    return declined


@dataclass(frozen=True, slots=True)
class MonitoredTransfer:
    """A finished transfer as transaction monitoring reads it: who, how much and when."""

    sender_client_id: str
    recipient_client_id: str
    currency: Currency
    # What the recipient got, in the smallest unit.
    amount_minor: int
    finished_at: datetime


async def for_monitoring(session: AsyncSession, transfer_id: str) -> MonitoredTransfer | None:
    """A transfer that was made, for screening's transaction monitoring. None if it was not
    made (yet), or there is no such transfer. For the worker."""
    transfer = await _transfer(session, transfer_id)
    if transfer.status is not TransferStatus.DONE or transfer.sent is None:
        return None
    assert transfer.finished_at is not None
    return MonitoredTransfer(
        sender_client_id=transfer.sender_client_id,
        recipient_client_id=transfer.recipient_client_id,
        currency=transfer.sent.currency,
        amount_minor=transfer.sent.minor,
        finished_at=transfer.finished_at,
    )


# The worker -------------------------------------------------------------------------------------


async def carry_out(session: AsyncSession, transfer_id: str, clock: Clock) -> Transfer:
    """Move a waiting transfer's money. Worker only. Safe to repeat: a finished one is left.

    If the investment no longer runs, an early withdrawal was asked for, it matures today, or
    the recipient can no longer receive, the transfer fails with a code and nothing moves.
    """
    transfer = await _locked(session, transfer_id)
    if transfer.status is not TransferStatus.WAITING:
        return transfer
    row = (await session.execute(_INVESTMENT_LOCKED, {"id": transfer.investment_id})).one()
    investment = investment_from_row(row)
    now = clock.now()
    today = business_date(now)
    failure = await _why_not(session, transfer, investment, today, now)
    value = investment.amount + investment.accrued_net
    if failure is None:
        # The interest due up to today first, so the value split is all the sender earned.
        await accrual.accrue_locked(session, investment, today, clock)
        investment = await get_investment(session, investment.id)
        value = investment.amount + investment.accrued_net
        if not transfer.whole and transfer.amount > value:
            failure = TransferRefusal.MORE_THAN_VALUE.value
    if failure is not None:
        await session.execute(
            _FINISH,
            {
                "id": transfer.id,
                "status": TransferStatus.FAILED.value,
                "now": now,
                "sent": None,
                "received": None,
                "kept": None,
                "entry_id": None,
                "failure_code": failure,
            },
        )
        failed = await _transfer(session, transfer.id)
        await _audit(session, AuditActor.system(), "change", "failed", failed, clock)
        await _tell_sender(session, failed, "investment_transfer_failed", now)
        logger.warning("investment_transfer_failed", transfer_id=transfer.id, error_code=failure)
        return failed

    shares = split(value, transfer.amount, whole=transfer.whole)
    term_days = remaining_term(investment.maturity_date, today)
    quoted_at: datetime = await session.scalar(_DATABASE_NOW)
    received_id = await _open(
        session,
        transfer,
        investment,
        client_id=transfer.recipient_client_id,
        principal=shares.sent,
        term_days=term_days,
        kept=False,
        quoted_at=quoted_at,
        now=now,
    )
    kept_id = None
    if shares.kept is not None:
        kept_id = await _open(
            session,
            transfer,
            investment,
            client_id=transfer.sender_client_id,
            principal=shares.kept,
            term_days=term_days,
            kept=True,
            quoted_at=quoted_at,
            now=now,
        )
    received = await get_investment(session, received_id)
    lines = [
        *await out_of_holding(session, investment, clock),
        credit(
            (await ledger.open_account(session, holding_account(received), clock)).id, shares.sent
        ),
    ]
    kept_investment = None
    if kept_id is not None and shares.kept is not None:
        kept_investment = await get_investment(session, kept_id)
        holding = await ledger.open_account(session, holding_account(kept_investment), clock)
        lines.append(credit(holding.id, shares.kept))
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="investment.transferred",
            effective_date=today,
            lines=tuple(lines),
            idempotency_key=f"investment-transfer:{transfer.id}",
            source_type="investment_transfer",
            source_id=transfer.id,
        ),
        clock,
    )
    for opened in (received_id, kept_id):
        if opened is not None:
            await session.execute(
                _START,
                {
                    "id": opened,
                    "start_date": today,
                    "maturity_date": None if term_days is None else maturity_date(today, term_days),
                    "entry_id": entry.id,
                    "now": now,
                },
            )
    await session.execute(_CLOSE, {"id": investment.id, "entry_id": entry.id, "now": now})
    await session.execute(
        _FINISH,
        {
            "id": transfer.id,
            "status": TransferStatus.DONE.value,
            "now": now,
            "sent": shares.sent.minor,
            "received": received_id,
            "kept": kept_id,
            "entry_id": entry.id,
            "failure_code": None,
        },
    )
    done = await _transfer(session, transfer.id)
    await publish(
        session,
        topic=TRANSFERRED_TOPIC,
        idempotency_key=transfer.id,
        payload={
            "transfer_id": transfer.id,
            "investment_id": investment.id,
            # The goals module follows a saving into what the sender kept, as at a rollover.
            "new_investment_id": kept_id,
            "received_investment_id": received_id,
            "product_code": investment.product_code,
            "currency": value.currency.value,
            "sent_minor": shares.sent.minor,
        },
        consumers=[FOLLOW_ROLLOVER] if kept_id is not None else [],
        now=now,
    )
    await _audit(session, AuditActor.system(), "change", "done", done, clock)
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="investments.transferred",
            resource_type=INVESTMENT_RESOURCE,
            resource_id=investment.id,
            actor=AuditActor.system(),
            details={"transfer_id": transfer.id, "ledger_entry_id": entry.id},
        ),
        clock,
    )
    product = product_from_row(await product_row(session, investment.product_code))
    await notifications.notify(
        session,
        event_name="investment_sent",
        client_id=transfer.sender_client_id,
        idempotency_key=f"transfer-sent:{transfer.id}",
        variables={
            "amount": shares.sent,
            "product": product.name,
            "person": transfer.recipient_name,
        },
        link=notifications.Link("investment", kept_id or investment.id),
        now=now,
    )
    await notifications.notify(
        session,
        event_name="investment_received",
        client_id=transfer.recipient_client_id,
        idempotency_key=f"transfer-received:{transfer.id}",
        variables={"amount": shares.sent, "product": product.name, "person": transfer.sender_name},
        link=notifications.Link("investment", received_id),
        now=now,
    )
    logger.info(
        "investment_transferred",
        transfer_id=transfer.id,
        investment_id=investment.id,
        received_investment_id=received_id,
    )
    return done


# Internals --------------------------------------------------------------------------------------


async def _ask(
    session: AsyncSession,
    client: Actor,
    sender_id: str,
    investment_id: str,
    to_account_number: str,
    amount: Money,
    message: str | None,
    clock: Clock,
    *,
    whole: bool = False,
) -> str:
    row = (await session.execute(_INVESTMENT_LOCKED, {"id": investment_id})).one_or_none()
    if row is None or row.client_id != sender_id:
        raise ApplicationError("not_found")
    investment = investment_from_row(row)
    number = re.sub(r"[\s-]", "", to_account_number)
    if not _ACCOUNT_NUMBER.fullmatch(number):
        raise invalid("to", "not_an_account_number")
    recipient = await kyc.transfer_recipient(session, account_number=number)
    if recipient is None or await client_status(session, recipient.client_id) != "active":
        raise invalid("to", "no_such_client")
    if recipient.client_id == sender_id:
        raise invalid("to", TransferRefusal.TO_YOURSELF.value)
    sender = await kyc.transfer_recipient(session, client_id=sender_id)
    if sender is None:
        # Nobody sends before their own identity check is approved.
        raise ApplicationError("forbidden")
    # Imported here: closures reads goals and automatic investing, which import this
    # module's package, so a top-level import makes a cycle whichever is loaded first.
    from app.modules.closures import service as closures

    asked_to_close = await closures.latest(session, sender_id)
    if asked_to_close is not None and asked_to_close.is_open:
        # The client asked to close their account: nothing new starts while staff decide.
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["client"], code="closure_requested")]
        )
    now = clock.now()
    today = business_date(now)
    product = product_from_row(await product_row(session, investment.product_code))
    value = investment.amount + investment.accrued_net
    if whole:
        if amount.currency is not value.currency:
            raise invalid("amount_minor", TransferRefusal.CURRENCY.value)
        # Everything, as it is worth now: interest may have been added since the client saw it.
        amount = value
    refusal = check_transfer(
        running=investment.status is InvestmentStatus.ACTIVE,
        withdrawal_requested=investment.withdrawal_requested_at is not None,
        maturity=investment.maturity_date,
        today=today,
        value=value,
        amount=amount,
        minimum=product.minimum,
    )
    if refusal in (TransferRefusal.NOT_RUNNING, TransferRefusal.WITHDRAWAL_REQUESTED):
        raise ApplicationError("conflict")
    if refusal is not None:
        raise invalid("amount_minor", refusal.value)
    await session.execute(_CLIENT_LOCK, {"lock": f"finvest.transfers.client:{sender_id}"})
    sent_today = Money(
        int(
            await session.scalar(
                _SENT_TODAY,
                {
                    "client_id": sender_id,
                    "currency": amount.currency.value,
                    "counted": [status.value for status in COUNTED],
                    "today": today,
                },
            )
            or 0
        ),
        amount.currency,
    )
    # A sender on a screening hold is held for staff like a large transfer, and told the same.
    held_for: HeldFor | None = None
    if await _on_hold(session, sender_id, now):
        held_for = HeldFor.SCREENING
    elif needs_review(amount, sent_today, await _review_above(session, amount.currency, today)):
        held_for = HeldFor.SIZE
    held = held_for is not None
    transfer_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": transfer_id,
                    "investment_id": investment.id,
                    "sender": sender_id,
                    "recipient": recipient.client_id,
                    "sender_name": display_name(sender.surname, sender.given_names),
                    "recipient_name": display_name(recipient.surname, recipient.given_names),
                    "currency": amount.currency.value,
                    "amount_minor": amount.minor,
                    "whole": amount == value,
                    "message": message,
                    "status": (TransferStatus.HELD if held else TransferStatus.WAITING).value,
                    "held": held,
                    "held_for": held_for.value if held_for else None,
                },
            )
    except IntegrityError as error:
        # A transfer of this investment is already open.
        raise ApplicationError("conflict") from error
    transfer = await _transfer(session, transfer_id)
    await _audit(session, AuditActor.client(sender_id), "change", "requested", transfer, clock)
    if held:
        await _tell_sender(session, transfer, "investment_transfer_held", now)
    else:
        await _queue(session, transfer_id, now)
    logger.info(
        "investment_transfer_requested",
        transfer_id=transfer_id,
        held=held,
        held_for=held_for.value if held_for else None,
    )
    return transfer_id


async def _why_not(
    session: AsyncSession,
    transfer: Transfer,
    investment: Investment,
    today: date,
    clock_now: datetime,
) -> str | None:
    if investment.status is not InvestmentStatus.ACTIVE:
        return TransferRefusal.NOT_RUNNING.value
    if investment.withdrawal_requested_at is not None:
        return TransferRefusal.WITHDRAWAL_REQUESTED.value
    if investment.maturity_date is not None and today >= investment.maturity_date:
        return TransferRefusal.MATURES_TODAY.value
    # A held transfer can wait for days: either account may have been locked or closed since.
    if await client_status(session, transfer.sender_client_id) != "active":
        return "sender_not_available"
    recipient = await kyc.transfer_recipient(session, client_id=transfer.recipient_client_id)
    if recipient is None or await client_status(session, transfer.recipient_client_id) != "active":
        return "recipient_not_available"
    if await _on_hold(session, transfer.sender_client_id, clock_now) or await _on_hold(
        session, transfer.recipient_client_id, clock_now
    ):
        return NOT_POSSIBLE
    return None


async def _open(
    session: AsyncSession,
    transfer: Transfer,
    investment: Investment,
    *,
    client_id: str,
    principal: Money,
    term_days: int | None,
    kept: bool,
    quoted_at: datetime,
    now: datetime,
) -> str:
    """Insert one of the transfer's investments, unpaid, for the worker to start."""
    new_id = push_id()
    at_maturity = None
    destination_id = None
    if kept:
        at_maturity = investment.at_maturity.value if investment.at_maturity else None
        destination_id = investment.payout_destination_id
    elif term_days is not None:
        at_maturity = "roll_over"
    await session.execute(
        _INSERT_INVESTMENT,
        {
            "id": new_id,
            "client_id": client_id,
            "principal_minor": principal.minor,
            "term_days": term_days,
            "at_maturity": at_maturity,
            "destination_id": destination_id,
            "created_at": quoted_at,
            "pay_by": pay_by(quoted_at),
            "now": now,
            "source_type": "transfer_kept" if kept else "transfer_received",
            "transfer_id": transfer.id,
            "from_id": investment.id,
        },
    )
    return new_id


async def _on_hold(session: AsyncSession, client_id: str, now: datetime) -> bool:
    """Whether screening holds the client: a screen under way, an open case or a sanctions
    match. A missing recent screen does not hold a transfer, as it would a payout: the value
    never leaves Finvest, and every client is screened at approval and every 30 days."""
    return await screening.payout_clearance(session, client_id, now) in (
        screening.Clearance.WAIT,
        screening.Clearance.BLOCKED,
    )


async def _review_above(session: AsyncSession, currency: Currency, today: date) -> Money | None:
    """The size above which a payout waits for staff, in this currency. None if none is set."""
    limits = await payouts.current_limits(session, today)
    if limits.currency is not currency:
        return None
    return limits.review_above


async def _queue(session: AsyncSession, transfer_id: str, now: datetime) -> None:
    await enqueue(
        session,
        JobRequest(
            kind=CARRY_OUT_KIND,
            idempotency_key=transfer_id,
            payload={"transfer_id": transfer_id},
        ),
        now=now,
    )


async def _recipient(session: AsyncSession, to: str) -> kyc.Recipient | None:
    digits = re.sub(r"[\s-]", "", to.strip())
    if _ACCOUNT_NUMBER.fullmatch(digits):
        found = await kyc.transfer_recipient(session, account_number=digits)
    else:
        client_id = await client_id_for_phone(session, to)
        found = (
            None
            if client_id is None
            else await kyc.transfer_recipient(session, client_id=client_id)
        )
    if found is None or await client_status(session, found.client_id) != "active":
        return None
    return found


async def _tell_sender(
    session: AsyncSession, transfer: Transfer, event_name: str, now: datetime
) -> None:
    investment = await get_investment(session, transfer.investment_id)
    await notifications.notify(
        session,
        event_name=event_name,
        client_id=transfer.sender_client_id,
        idempotency_key=f"{event_name}:{transfer.id}",
        variables={"product": investment.product_name, "person": transfer.recipient_name},
        link=notifications.Link("investment", transfer.investment_id),
        now=now,
    )


async def _audit(
    session: AsyncSession,
    actor: AuditActor,
    category: AuditCategory,
    step: str,
    transfer: Transfer,
    clock: Clock,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"investments.transfer.{step}",
            outcome="failed" if step == "failed" else "succeeded",
            resource_type=RESOURCE,
            resource_id=transfer.id,
            actor=actor,
            reason=reason,
            # IDs and codes only, never an amount or a name.
            details={
                "investment_id": transfer.investment_id,
                "status": transfer.status.value,
                "held": transfer.held,
                "held_for": transfer.held_for.value if transfer.held_for else None,
                "failure_code": transfer.failure_code,
            },
        ),
        clock,
    )


async def _locked(session: AsyncSession, transfer_id: str) -> Transfer:
    row = (await session.execute(_BY_ID_LOCKED, {"id": transfer_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _transfer(session: AsyncSession, transfer_id: str) -> Transfer:
    row = (await session.execute(_BY_ID, {"id": transfer_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


def _from_row(row: Row[Any]) -> Transfer:
    currency = Currency(row.currency)
    return Transfer(
        id=row.id,
        investment_id=row.investment_id,
        sender_client_id=row.sender_client_id,
        recipient_client_id=row.recipient_client_id,
        sender_name=row.sender_name,
        recipient_name=row.recipient_name,
        amount=Money(row.amount_minor, currency),
        whole=row.whole,
        message=row.message,
        status=TransferStatus(row.status),
        held=row.held,
        held_for=HeldFor(row.held_for) if row.held_for else None,
        requested_at=row.requested_at,
        release_proposed_by=row.release_proposed_by,
        release_proposed_at=row.release_proposed_at,
        release_reason=row.release_reason,
        released_by=row.released_by,
        released_at=row.released_at,
        declined_by=row.declined_by,
        declined_at=row.declined_at,
        decision_reason=row.decision_reason,
        cancelled_at=row.cancelled_at,
        finished_at=row.finished_at,
        sent=None if row.sent_minor is None else Money(row.sent_minor, currency),
        received_investment_id=row.received_investment_id,
        kept_investment_id=row.kept_investment_id,
        entry_id=row.entry_id,
        failure_code=row.failure_code,
    )


def _client_view(transfer: Transfer, client_id: str) -> ClientTransfer:
    sent = transfer.sender_client_id == client_id
    return ClientTransfer(
        id=transfer.id,
        direction="sent" if sent else "received",
        other_name=transfer.recipient_name if sent else transfer.sender_name,
        status=transfer.status,
        amount=transfer.sent or transfer.amount,
        whole=transfer.whole,
        message=transfer.message,
        requested_at=transfer.requested_at,
        finished_at=transfer.finished_at,
        investment_id=transfer.investment_id if sent else transfer.received_investment_id,
        kept_investment_id=transfer.kept_investment_id if sent else None,
        failure_code=transfer.failure_code if sent else None,
    )


def _client(client: Actor) -> str:
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    return client.id


def _staff(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise invalid("reason", "required")
    return cleaned
