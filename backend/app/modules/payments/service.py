"""Deposits by mobile money: starting one, and settling it from the provider's own record.

Nothing here calls a provider. `request_deposit` saves the deposit and a job to send it in one
transaction. The job handlers in `jobs.py` call the provider with no transaction open and then
come back here to record what it said. `settle_deposit` is the only place a deposit becomes
`confirmed`, and it posts the ledger entry in the same transaction.

Bank transfers share the deposits table and the posting here; their own steps are in
`bank_transfers.py`.

Every function takes the caller's open session and commits nothing itself.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import CursorResult, Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import JobRequest, enqueue, publish
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.documents.events import RECEIPT_ON_DEPOSIT
from app.modules.investments.events import START_ON_DEPOSIT
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payments.domain.deposits import (
    APPROVAL_WINDOW,
    UNSENT_EXPIRY,
    DepositStatus,
    InvalidDeposit,
    Method,
    Move,
    Network,
    Purpose,
    after_status,
    can_move,
    check_mobile_money_amount,
    normalise_msisdn,
)
from app.modules.payments.domain.outcomes import StatusReading
from app.modules.payments.domain.provider import ProviderNotice
from app.modules.screening.events import MONITOR_DEPOSIT
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import PUSH_ID_PATTERN, push_id, validate_push_id

SEND_KIND = "payments.send_collection"
VERIFY_KIND = "payments.verify_deposit"
SWEEP_KIND = "payments.sweep_deposits"
CONFIRMED_TOPIC = "payments.deposit_confirmed"
REQUEST_OPERATION = "payments.request_deposit"

# The payer is waiting with their phone in hand, so sending is tried for minutes, not hours.
SEND_ATTEMPTS = 5
SEND_MAX_AGE = UNSENT_EXPIRY - timedelta(minutes=2)
# Seconds to wait before each status read while the payer has the prompt. The last one repeats.
POLL_DELAYS = (3, 3, 5, 5, 10, 10, 15, 30)

_NOTICE_FIELD = re.compile(r"[A-Za-z0-9._:-]{1,128}")

Cause = Literal[
    "request", "send", "status_read", "sweep", "reconciliation", "staff_review", "reassignment"
]
logger = structlog.get_logger()
DEPOSIT_RESOURCE = "payment_deposit"


class DepositExists(ApplicationError):
    """The investment already has a deposit that has not failed: open, uncertain or confirmed.

    Carries that deposit, when the caller may see it, so the client can be taken to it and not
    asked to pay again.
    """

    def __init__(self, deposit_id: str | None, status: DepositStatus | None) -> None:
        super().__init__("conflict")
        self.deposit_id = deposit_id
        self.status = status


class UnusableNotice(Exception):
    """A signed callback whose identifiers are not ones we could have issued or stored."""


@dataclass(frozen=True, slots=True)
class DepositRequest:
    purpose: Purpose
    purpose_id: str
    network: Network
    amount: Money
    phone: str


@dataclass(frozen=True, slots=True)
class Deposit:
    id: str
    client_id: str
    purpose: Purpose
    purpose_id: str
    method: Method
    # None for a bank transfer, which has no network and no payer number.
    network: Network | None
    provider: str
    amount: Money
    payer_msisdn: str | None
    status: DepositStatus
    code: str | None
    provider_reference: str | None
    ledger_entry_id: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    settled_at: datetime | None


_COLUMNS = (
    "id, client_id, purpose_type, purpose_id, method, provider, currency, amount_minor, "
    "payer_msisdn, status, code, provider_reference, ledger_entry_id, created_at, updated_at, "
    "sent_at, settled_at"
)
_INSERT = text(
    "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, provider, "
    "currency, amount_minor, payer_msisdn, status, created_at, updated_at) VALUES (:id, "
    ":client_id, :purpose_type, :purpose_id, :method, :provider, :currency, :amount_minor, "
    ":payer_msisdn, :status, :now, :now)"
)
_SELECT = text(f"SELECT {_COLUMNS} FROM payment_deposits WHERE id = :id")  # noqa: S608
# Created by migration 0021. An investment has at most one deposit that has not failed.
LIVE_INVESTMENT_INDEX = "uq_payment_deposits_live_investment"
_HAS_LIVE = text(
    "SELECT 1 FROM payment_deposits WHERE purpose_type = :purpose_type "
    "AND purpose_id = :purpose_id AND status <> 'failed' LIMIT 1"
)
_HAS_LIVE_ID = text(
    "SELECT id FROM payment_deposits WHERE purpose_type = :purpose_type "
    "AND purpose_id = :purpose_id AND status <> 'failed' ORDER BY id DESC LIMIT 1"
)
_SELECT_LIVE = text(
    "SELECT id, status FROM payment_deposits WHERE purpose_type = 'investment' "
    "AND purpose_id = :purpose_id AND status <> 'failed' LIMIT 1"
)
_SELECT_LOCKED = text(
    f"SELECT {_COLUMNS} FROM payment_deposits WHERE id = :id FOR UPDATE"  # noqa: S608
)
_SELECT_BY_REFERENCE = text(
    "SELECT id FROM payment_deposits "
    "WHERE provider = :provider AND provider_reference = :provider_reference"
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM payment_deposits "  # noqa: S608 - constant column list
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:before_id AS text) IS NULL OR id < :before_id) "
    "ORDER BY id DESC LIMIT :limit"
)
_UPDATE = text(
    "UPDATE payment_deposits SET status = :status, code = :code, "
    "provider_reference = coalesce(provider_reference, :provider_reference), "
    "ledger_entry_id = :ledger_entry_id, updated_at = :now, "
    "sent_at = coalesce(sent_at, :sent_at), settled_at = :settled_at WHERE id = :id"
)
_INSERT_EVENT = text(
    "INSERT INTO payment_deposit_events (id, deposit_id, occurred_at, from_status, to_status, "
    "code, cause) VALUES (:id, :deposit_id, :now, :from_status, :to_status, :code, :cause)"
)
_INSERT_NOTICE = text(
    "INSERT INTO payment_provider_notices (id, provider, notice_key, request_id, "
    "provider_reference, received_at) VALUES (:id, :provider, :notice_key, :request_id, "
    ":provider_reference, :now) ON CONFLICT DO NOTHING"
)


async def request_deposit(
    session: AsyncSession,
    actor: Actor,
    request: DepositRequest,
    *,
    idempotency_key: str,
    account_phone: str,
    provider: str,
    clock: Clock,
) -> Deposit:
    """Save a client's mobile money payment for one investment or goal, and a job to send it.

    `session` is the client's own transaction. Nothing is sent here: the worker asks the
    provider to prompt the payer. Calling again with the same key returns the same deposit and
    creates nothing. The same key with different details is refused.

    `account_phone` is the number on the client's own account, from their session and never
    from what they sent. The payer must be that number: a payment from a number registered to
    someone else is refused.

    An investment takes one deposit. While one is open, uncertain or confirmed, a request with
    a new key raises `DepositExists` and no second prompt is sent. A failed deposit does not
    count, so the client can try again.
    """
    if actor.kind != "client" or actor.id is None:
        raise ApplicationError("forbidden")
    try:
        check_mobile_money_amount(request.amount)
        msisdn = normalise_msisdn(request.phone)
        validate_push_id(request.purpose_id)
        if msisdn != normalise_msisdn(account_phone):
            raise InvalidDeposit("not_account_phone")
    except InvalidDeposit as error:
        field = "phone" if error.code in ("invalid_phone", "not_account_phone") else "amount"
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=[field], code=error.code)]
        ) from None
    except ValueError:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["purpose_id"], code="invalid_id")],
        ) from None
    client_id = actor.id
    now = clock.now()

    async def command() -> Receipt:
        deposit_id = push_id()
        await insert_deposit(
            session,
            {
                "id": deposit_id,
                "client_id": client_id,
                "purpose_type": request.purpose.value,
                "purpose_id": request.purpose_id,
                "method": request.network.value,
                "provider": provider,
                "currency": request.amount.currency.value,
                "amount_minor": request.amount.minor,
                "payer_msisdn": msisdn,
                "status": DepositStatus.CREATED.value,
                "now": now,
            },
        )
        await record_event(session, deposit_id, None, DepositStatus.CREATED, None, "request", now)
        await record_requested(session, actor, deposit_id, request.network.value, clock)
        await enqueue(
            session,
            JobRequest(
                kind=SEND_KIND,
                idempotency_key=deposit_id,
                payload={"deposit_id": deposit_id},
                max_attempts=SEND_ATTEMPTS,
                max_age=SEND_MAX_AGE,
            ),
            now=now,
        )
        return Receipt(201, {"deposit_id": deposit_id})

    outcome = await run_once(
        session,
        actor=actor,
        operation=REQUEST_OPERATION,
        key=idempotency_key,
        request={
            "purpose": request.purpose.value,
            "purpose_id": request.purpose_id,
            "network": request.network.value,
            "currency": request.amount.currency.value,
            "amount_minor": request.amount.minor,
            "msisdn": msisdn,
        },
        now=now,
        command=command,
    )
    deposit_id = str(outcome.receipt.response["deposit_id"])
    if not outcome.replayed:
        logger.info("deposit_requested", deposit_id=deposit_id)
    return await get_deposit(session, deposit_id)


async def insert_deposit(session: AsyncSession, values: dict[str, object]) -> None:
    """Save a new deposit, or raise `DepositExists` if its investment already has a live one.

    An investment is paid for once, whichever way. Only a failed deposit frees it for another
    try. For the payments module's own commands, inside their idempotent command.
    """

    async def live_deposit() -> DepositExists | None:
        row = (await session.execute(_SELECT_LIVE, {"purpose_id": values["purpose_id"]})).first()
        return DepositExists(row.id, DepositStatus(row.status)) if row else None

    if values["purpose_type"] == Purpose.INVESTMENT.value:
        existing = await live_deposit()
        if existing:
            raise existing
    try:
        # The check above loses a race between two requests. The unique index does not.
        async with session.begin_nested():
            await session.execute(_INSERT, values)
    except IntegrityError as error:
        if LIVE_INVESTMENT_INDEX not in str(error.orig):
            raise
        raise (await live_deposit()) or DepositExists(None, None) from None


async def has_live_deposit(session: AsyncSession, *, purpose: Purpose, purpose_id: str) -> bool:
    """Whether a deposit that has not failed exists for this investment or goal.

    For other modules, called as the system in the worker. True while a deposit is being sent,
    awaiting approval, uncertain or confirmed: money for this purpose has arrived or still may.
    An investment must not be cancelled as unpaid while this is true. A client's own
    transaction sees only that client's deposits, so do not ask from one.
    """
    row = await session.execute(
        _HAS_LIVE, {"purpose_type": purpose.value, "purpose_id": purpose_id}
    )
    return row.first() is not None


async def live_deposit_for(
    session: AsyncSession, *, purpose: Purpose, purpose_id: str
) -> Deposit | None:
    """The deposit for this investment that has not failed, if this transaction may see it.

    For a client who was told a payment is already under way: this is the one to show them.
    """
    row = (
        await session.execute(
            _HAS_LIVE_ID, {"purpose_type": purpose.value, "purpose_id": purpose_id}
        )
    ).first()
    return None if row is None else await find_deposit(session, row.id)


async def get_deposit(session: AsyncSession, deposit_id: str) -> Deposit:
    """The deposit, if this transaction may see it: a client sees only their own."""
    deposit = await find_deposit(session, deposit_id)
    if deposit is None:
        raise ApplicationError("not_found")
    return deposit


async def find_deposit(
    session: AsyncSession, deposit_id: str, *, lock: bool = False
) -> Deposit | None:
    row = (
        await session.execute(_SELECT_LOCKED if lock else _SELECT, {"id": deposit_id})
    ).one_or_none()
    return None if row is None else _deposit(row)


async def list_deposits(
    session: AsyncSession,
    *,
    status: DepositStatus | None = None,
    limit: int = 50,
    before_id: str | None = None,
) -> list[Deposit]:
    """Newest first. A staff transaction or the worker sees all; a client sees their own."""
    rows = await session.execute(
        _LIST,
        {
            "status": status.value if status else None,
            "before_id": before_id,
            "limit": max(1, min(limit, 200)),
        },
    )
    return [_deposit(row) for row in rows.all()]


async def record_notice(
    session: AsyncSession, provider: str, notice: ProviderNotice, clock: Clock
) -> bool:
    """Save a provider callback and a job to read the status it hints at.

    Returns False for a callback already seen, which then does nothing more. The callback is
    never trusted: it changes no deposit, and the job reads the truth from the provider.
    """
    now = clock.now()
    request_id = notice.request_id
    for value in (notice.notice_id, request_id, notice.provider_reference):
        if value is not None and _NOTICE_FIELD.fullmatch(value) is None:
            raise UnusableNotice
    saved: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
        _INSERT_NOTICE,
        {
            "id": push_id(),
            "provider": provider,
            "notice_key": notice.notice_id,
            "request_id": request_id,
            "provider_reference": notice.provider_reference,
            "now": now,
        },
    )
    if saved.rowcount != 1:
        return False
    payload: dict[str, object] = {"provider": provider}
    if request_id is not None and _is_push_id(request_id):
        payload["deposit_id"] = request_id
    elif notice.provider_reference is not None:
        payload["provider_reference"] = notice.provider_reference
    else:
        return True
    await enqueue(
        session,
        JobRequest(
            kind=VERIFY_KIND,
            idempotency_key=f"notice:{provider}:{notice.notice_id}",
            payload=payload,
            max_age=APPROVAL_WINDOW,
        ),
        now=now,
    )
    return True


async def deposit_id_for_reference(
    session: AsyncSession, provider: str, provider_reference: str
) -> str | None:
    found = await session.scalar(
        _SELECT_BY_REFERENCE, {"provider": provider, "provider_reference": provider_reference}
    )
    return None if found is None else str(found)


async def settle_deposit(
    session: AsyncSession,
    deposit_id: str,
    reading: StatusReading,
    clock: Clock,
    *,
    cause: Cause,
) -> Deposit:
    """Move a deposit on from a status read back from the provider, in a system transaction.

    This is how the worker settles a deposit, and how reconciliation settles an uncertain one
    after reading the provider's record. It locks the deposit, so two callers cannot both act.
    A confirmation for exactly the expected amount posts the ledger entry and marks the deposit
    `confirmed` together. Calling it again, or for a deposit that is already final, changes
    nothing.
    """
    deposit = await find_deposit(session, deposit_id, lock=True)
    if deposit is None:
        raise ApplicationError("not_found")
    move = after_status(
        deposit.status,
        reading,
        expected=deposit.amount,
        sent_at=deposit.sent_at,
        now=clock.now(),
    )
    if move is None:
        return deposit
    return await apply_move(session, deposit, move, clock, cause=cause)


async def record_requested(
    session: AsyncSession, client: Actor, deposit_id: str, method: str, clock: Clock
) -> None:
    """Write to the audit trail that a client asked to pay, in their own transaction.

    The method only: never the amount, the number or the bank details, which the deposit holds.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="payments.deposit_requested",
            resource_type=DEPOSIT_RESOURCE,
            resource_id=deposit_id,
            actor=AuditActor(client.kind, client.id),
            details={"method": method},
        ),
        clock,
    )


async def _record_settled(
    session: AsyncSession, deposit: Deposit, move: Move, cause: Cause, clock: Clock
) -> None:
    """Write to the audit trail that a deposit was confirmed or failed. Deposits settle only in
    the worker, which acts for Finvest itself."""
    confirmed = move.status is DepositStatus.CONFIRMED
    details: dict[str, object] = {"method": deposit.method.value, "cause": cause}
    if not confirmed and move.code:
        details["failure"] = move.code
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="payments.deposit_confirmed" if confirmed else "payments.deposit_failed",
            resource_type=DEPOSIT_RESOURCE,
            resource_id=deposit.id,
            actor=AuditActor("system", None),
            outcome="succeeded" if confirmed else "failed",
            details=details,
        ),
        clock,
    )


async def apply_move(
    session: AsyncSession,
    deposit: Deposit,
    move: Move,
    clock: Clock,
    *,
    cause: Cause,
    paid_into: AccountSpec | None = None,
) -> Deposit:
    """Record `move` on a deposit the caller has locked. Posts to the ledger if it confirms.

    `paid_into` is the asset account the money reached. A mobile money deposit leaves it out and
    the provider's wallet is used. A bank transfer must name the bank account it was found in.
    """
    if not can_move(deposit.status, move.status):
        raise RuntimeError(f"A deposit cannot move from {deposit.status} to {move.status}")
    now = clock.now()
    entry_id: str | None = None
    if move.post is not None:
        if paid_into is None:
            paid_into = _wallet(deposit, move.post.currency)
        entry_id = await _post_confirmed(session, deposit, move.post, paid_into, clock)
    await session.execute(
        _UPDATE,
        {
            "id": deposit.id,
            "status": move.status.value,
            "code": move.code,
            "provider_reference": move.provider_reference,
            "ledger_entry_id": entry_id,
            "now": now,
            "sent_at": now if move.status is DepositStatus.SENDING else None,
            "settled_at": now if move.status.is_final else None,
        },
    )
    await record_event(session, deposit.id, deposit.status, move.status, move.code, cause, now)
    if move.status.is_final:
        await _record_settled(session, deposit, move, cause, clock)
    if move.status is DepositStatus.CONFIRMED:
        await publish_confirmed(session, deposit, now)
    logger.info(
        "deposit_moved", deposit_id=deposit.id, status=move.status.value, error_code=move.code
    )
    moved = await find_deposit(session, deposit.id)
    assert moved is not None
    return moved


async def publish_confirmed(session: AsyncSession, deposit: Deposit, now: datetime) -> None:
    """Tell other modules a deposit was confirmed at `now`, in the caller's transaction.

    Investments and goals start from this event, the client's receipt is asked for, and
    screening's transaction monitoring reads it. It is published once per deposit.
    """
    await publish(
        session,
        topic=CONFIRMED_TOPIC,
        idempotency_key=deposit.id,
        payload={
            "deposit_id": deposit.id,
            "purpose_type": deposit.purpose.value,
            "purpose_id": deposit.purpose_id,
            # The business day of the ledger entry. An investment earns from this day.
            "confirmed_on": business_date(now).isoformat(),
        },
        consumers=[START_ON_DEPOSIT, RECEIPT_ON_DEPOSIT, MONITOR_DEPOSIT],
        now=now,
    )


async def schedule_status_read(
    session: AsyncSession, deposit_id: str, clock: Clock, *, attempt: int
) -> None:
    """Queue the next status read for a deposit whose payer is being prompted."""
    delay = POLL_DELAYS[min(attempt, len(POLL_DELAYS) - 1)]
    await enqueue(
        session,
        JobRequest(
            kind=VERIFY_KIND,
            idempotency_key=f"{deposit_id}:poll:{attempt}",
            payload={"deposit_id": deposit_id, "attempt": attempt},
            delay=timedelta(seconds=delay),
            max_age=APPROVAL_WINDOW,
        ),
        now=clock.now(),
    )


def _wallet(deposit: Deposit, currency: Currency) -> AccountSpec:
    if deposit.network is None:
        raise RuntimeError("A bank transfer is posted to the bank account it was found in")
    return AccountSpec(
        f"{deposit.provider}.wallet",
        AccountKind.ASSET,
        currency,
        f"Mobile money wallet ({deposit.network.value})",
        reference=deposit.network.value,
    )


async def _post_confirmed(
    session: AsyncSession, deposit: Deposit, amount: Money, paid_into: AccountSpec, clock: Clock
) -> str:
    """Money arrived in our wallet or bank account for this client's investment or goal."""
    wallet = await ledger.open_account(session, paid_into, clock)
    awaiting_start = await ledger.open_account(
        session,
        AccountSpec(
            "client.awaiting_start",
            AccountKind.LIABILITY,
            amount.currency,
            "Paid in, awaiting start",
            owner_client_id=deposit.client_id,
            reference=deposit.purpose_id,
        ),
        clock,
    )
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="payment.deposit_confirmed",
            effective_date=business_date(clock.now()),
            lines=(debit(wallet.id, amount), credit(awaiting_start.id, amount)),
            idempotency_key=f"deposit:{deposit.id}",
            source_type="payment.deposit",
            source_id=deposit.id,
        ),
        clock,
    )
    return entry.id


async def record_event(
    session: AsyncSession,
    deposit_id: str,
    from_status: DepositStatus | None,
    to_status: DepositStatus,
    code: str | None,
    cause: Cause,
    now: datetime,
) -> None:
    await session.execute(
        _INSERT_EVENT,
        {
            "id": push_id(),
            "deposit_id": deposit_id,
            "now": now,
            "from_status": from_status.value if from_status else None,
            "to_status": to_status.value,
            "code": code,
            "cause": cause,
        },
    )


def _is_push_id(value: str) -> bool:
    return re.fullmatch(PUSH_ID_PATTERN, value) is not None


def _deposit(row: Row[Any]) -> Deposit:
    return Deposit(
        id=row.id,
        client_id=row.client_id,
        purpose=Purpose(row.purpose_type),
        purpose_id=row.purpose_id,
        method=Method(row.method),
        network=Method(row.method).network,
        provider=row.provider,
        amount=Money(int(row.amount_minor), Currency(row.currency)),
        payer_msisdn=row.payer_msisdn,
        status=DepositStatus(row.status),
        code=row.code,
        provider_reference=row.provider_reference,
        ledger_entry_id=row.ledger_entry_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        sent_at=row.sent_at,
        settled_at=row.settled_at,
    )
