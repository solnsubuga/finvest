"""Commands and queries for payouts. The rules are in `domain/`.

Creating a payout and reserving its money happen in one transaction, and so does every later
change of status and the ledger entry that goes with it. Nothing here talks to a provider: a
queued mobile money payout gets a send job in the same transaction, and the worker in
`payouts/jobs.py` sends it and reports back through `apply_event`.

`create_payout` is called by the module that owns the money being withdrawn (an investment
paying out at maturity, or an early withdrawal). That module first moves the money into the
client's "payout pending" account for that piece of business and passes its reference.
The HTTP command that calls it carries the idempotency key.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.errors import InsufficientFunds
from app.modules.payouts import destinations
from app.modules.payouts.domain import payouts as machine
from app.modules.payouts.domain import posting
from app.modules.payouts.domain.destinations import DestinationKind, why_not_payable
from app.modules.payouts.domain.limits import (
    WithdrawalLimits,
    check_withdrawal,
    limits_in_force,
)
from app.modules.payouts.domain.payouts import LedgerEffect, PayoutEvent, PayoutStatus
from app.modules.payouts.domain.posting import PayoutTerms
from app.modules.payouts.domain.sending import HELD_UP_AFTER, SEND_ATTEMPTS
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

SEND_KIND = "payouts.send_payout"
READ_KIND = "payouts.read_payout_status"
SWEEP_KIND = "payouts.sweep_payouts"
# A send job not started within this is left to the sweep, which queues a fresh one.
SEND_MAX_AGE = timedelta(hours=1)


class PayoutRefused(Exception):
    """The payout was not created. `code` is safe to return to the client app."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PayoutView:
    id: str
    client_id: str
    destination_id: str
    kind: DestinationKind
    amount: Money
    status: PayoutStatus
    business_date: date
    provider_reference: str | None
    failure_code: str | None
    reviewed_by: str | None
    created_at: datetime
    source_account_id: str
    source_reference: str
    in_flight_account_id: str

    @property
    def terms(self) -> PayoutTerms:
        return PayoutTerms(
            self.id, self.client_id, self.amount, self.source_account_id, self.in_flight_account_id
        )


_COLUMNS = (
    "id, client_id, destination_id, kind, amount_minor, currency, status, business_date, "
    "provider_reference, failure_code, reviewed_by, created_at, source_account_id, "
    "in_flight_account_id, request_key, source_reference"
)
_LIMITS = text(
    "SELECT effective_from, currency, mobile_money_per_payout, mobile_money_per_day, "
    "bank_per_payout, bank_per_day, review_above FROM withdrawal_limits"
)
# One client's payouts are created one at a time, so two requests cannot both fit under the
# same remaining daily allowance.
_LOCK_CLIENT = text("SELECT pg_advisory_xact_lock(hashtextextended('payouts:' || :client_id, 0))")
_TODAY = text(
    "SELECT COALESCE(SUM(amount_minor), 0) AS total FROM payouts WHERE client_id = :client_id "
    "AND kind = :kind AND currency = :currency AND business_date = :day "
    "AND status NOT IN ('failed', 'cancelled')"
)
_INSERT = text(
    "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, currency, status, "
    "business_date, source_account_id, source_reference, in_flight_account_id, request_key, "
    "created_at, updated_at) VALUES (:id, :client_id, :destination_id, :kind, :amount_minor, "
    ":currency, :status, :day, :source_account_id, :source_reference, :in_flight_account_id, "
    ":request_key, :now, :now)"
)
_BY_KEY = text(
    f"SELECT {_COLUMNS} FROM payouts "  # noqa: S608 - constant column list
    "WHERE client_id = :client_id AND request_key = :request_key"
)
_GET = text(f"SELECT {_COLUMNS} FROM payouts WHERE id = :id")  # noqa: S608
_LOCK = text(f"SELECT {_COLUMNS} FROM payouts WHERE id = :id FOR UPDATE")  # noqa: S608
_UPDATE = text(
    "UPDATE payouts SET status = :status, updated_at = :now, "
    "provider_reference = COALESCE(:provider_reference, provider_reference), "
    "failure_code = COALESCE(:failure_code, failure_code), "
    "reviewed_by = COALESCE(:reviewed_by, reviewed_by) WHERE id = :id"
)
_HELD = text(
    f"SELECT {_COLUMNS} FROM payouts WHERE status = 'held' "  # noqa: S608 - constant column list
    "ORDER BY created_at, id LIMIT :limit"
)
# Queued mobile money payouts no send has left, first tried before a time.
_HELD_UP = text(
    "SELECT * FROM (SELECT "  # noqa: S608 - constant column list
    f"{_COLUMNS}, "
    "(SELECT count(*) FROM payout_send_attempts a WHERE a.payout_id = payouts.id) AS tries, "
    "(SELECT min(a.started_at) FROM payout_send_attempts a WHERE a.payout_id = payouts.id) "
    "AS first_tried_at, "
    "(SELECT max(a.started_at) FROM payout_send_attempts a WHERE a.payout_id = payouts.id) "
    "AS last_tried_at FROM payouts WHERE status = 'queued' AND kind = 'mobile_money' "
    "AND NOT EXISTS (SELECT FROM payout_send_attempts a WHERE a.payout_id = payouts.id "
    "AND (a.outcome IS NULL OR a.outcome <> 'deferred'))) AS queued "
    "WHERE first_tried_at <= :since ORDER BY first_tried_at, id LIMIT :limit"
)
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} FROM payouts WHERE client_id = :client_id "  # noqa: S608
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)

_REVIEW_EVENTS = frozenset({PayoutEvent.REVIEW_APPROVED, PayoutEvent.REVIEW_DECLINED})
_PERSON_EVENTS = _REVIEW_EVENTS | {PayoutEvent.CLIENT_CANCELLED}


async def current_limits(session: AsyncSession, on: date) -> WithdrawalLimits:
    rows = (await session.execute(_LIMITS)).all()

    def money(value: int | None, currency: Currency) -> Money | None:
        return None if value is None else Money(int(value), currency)

    versions = []
    for row in rows:
        currency = Currency(row.currency)
        versions.append(
            WithdrawalLimits(
                effective_from=row.effective_from,
                mobile_money_per_payout=Money(int(row.mobile_money_per_payout), currency),
                mobile_money_per_day=money(row.mobile_money_per_day, currency),
                bank_per_payout=money(row.bank_per_payout, currency),
                bank_per_day=money(row.bank_per_day, currency),
                review_above=money(row.review_above, currency),
            )
        )
    return limits_in_force(versions, on)


async def create_payout(
    session: AsyncSession,
    actor: Actor,
    *,
    client_id: str,
    destination_id: str,
    amount: Money,
    source_reference: str,
    request_key: str,
    clock: Clock,
    restricted_since: datetime | None = None,
) -> PayoutView:
    """Create a payout and reserve its money, or raise `PayoutRefused`.

    `actor` is the signed-in client asking for their own payout, or the system paying out on a
    client's behalf (an investment that matured). Staff cannot create one. A refusal leaves
    nothing behind: no payout row and no ledger entry.

    `request_key` makes the request safe to repeat. The same key for the same client returns
    the first payout and reserves nothing more. The same key for a different destination,
    amount or source is refused.

    The money comes from one place only: the client's "payout pending" account for
    `source_reference`, where the calling module handed it over (see `posting.funding_account`).
    The account is found from the client and the reference, never taken as an ID, so a payout
    cannot be funded from another payout's money in flight, from the failed-payout pot or from
    another client's account, whoever calls.
    """
    if actor.kind == "staff" or (actor.kind == "client" and actor.id != client_id):
        raise ApplicationError("forbidden")
    now = clock.now()
    today = business_date(now)
    await session.execute(_LOCK_CLIENT, {"client_id": client_id})

    try:
        funding = posting.funding_account(client_id, source_reference, amount.currency)
    except ValueError:  # includes the ledger's InvalidEntry
        raise PayoutRefused("source_not_allowed") from None

    saved = (
        await session.execute(_BY_KEY, {"client_id": client_id, "request_key": request_key})
    ).one_or_none()
    if saved is not None:
        first = _view(saved)
        if (first.destination_id, first.amount, first.source_reference) != (
            destination_id,
            amount,
            source_reference,
        ):
            raise ApplicationError("idempotency_key_reused")
        return first

    destination = await destinations.get_destination(session, destination_id)
    if destination.client_id != client_id:
        raise ApplicationError("not_found")
    blocked = why_not_payable(destination.state, now, restricted_since=restricted_since)
    if blocked is not None:
        raise PayoutRefused(blocked.value)

    limits = await current_limits(session, today)
    already = (
        await session.execute(
            _TODAY,
            {
                "client_id": client_id,
                "kind": destination.kind,
                "currency": amount.currency,
                "day": today,
            },
        )
    ).scalar_one()
    decision = check_withdrawal(
        limits, destination.kind, amount, Money(int(already), amount.currency)
    )
    if decision.refusal is not None:
        raise PayoutRefused(decision.refusal.value)

    payout_id = push_id()
    status = machine.initial_status(needs_review=decision.needs_review)
    try:
        async with session.begin_nested():
            source = await ledger.open_account(session, funding, clock)
            in_flight = await ledger.open_account(
                session, posting.in_flight_account(payout_id, client_id, amount.currency), clock
            )
            await session.execute(
                _INSERT,
                {
                    "id": payout_id,
                    "client_id": client_id,
                    "destination_id": destination_id,
                    "kind": destination.kind,
                    "amount_minor": amount.minor,
                    "currency": amount.currency,
                    "status": status,
                    "day": today,
                    "source_account_id": source.id,
                    "source_reference": source_reference,
                    "in_flight_account_id": in_flight.id,
                    "request_key": request_key,
                    "now": now,
                },
            )
            terms = PayoutTerms(payout_id, client_id, amount, source.id, in_flight.id)
            await ledger.post_entry(session, posting.reserve(terms, today), clock)
    except InsufficientFunds:
        raise PayoutRefused("insufficient_funds") from None
    if status is PayoutStatus.QUEUED:
        await queue_send(session, payout_id, destination.kind, now)
    await _audit(session, actor, "payouts.payout_requested", payout_id, clock)
    return await get_payout(session, payout_id)


async def apply_event(
    session: AsyncSession,
    actor: Actor,
    payout_id: str,
    event: PayoutEvent,
    clock: Clock,
    *,
    provider_reference: str | None = None,
    failure_code: str | None = None,
    reason: str | None = None,
) -> PayoutView:
    """Move a payout one step and post the ledger entry that goes with it, in one transaction.

    The worker reports what the provider said. A staff member approves or declines a held
    payout, and must give a reason. A client cancels their own held payout. The database
    refuses any other pairing of actor and step.
    """
    if event in machine.NEEDS_TWO_PEOPLE:
        # Arrives with reconciliation, where the second signature is stored.
        raise ApplicationError("forbidden")
    if (event in _PERSON_EVENTS) != (actor.kind != "system"):
        raise ApplicationError("forbidden")
    if event in _REVIEW_EVENTS and (actor.kind != "staff" or not (reason or "").strip()):
        raise ApplicationError("forbidden" if actor.kind != "staff" else "validation_failed")

    row = (await session.execute(_LOCK, {"id": payout_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    before = _view(row)
    transition = machine.apply(before.status, event)
    now = clock.now()
    # One savepoint, so a ledger refusal undoes the status change even if the caller commits.
    async with session.begin_nested():
        await session.execute(
            _UPDATE,
            {
                "id": payout_id,
                "status": transition.status,
                "now": now,
                "provider_reference": provider_reference,
                "failure_code": failure_code,
                "reviewed_by": actor.id if event in _REVIEW_EVENTS else None,
            },
        )
        if transition.effect is not LedgerEffect.NONE:
            to_account = await _destination_account(session, before, transition.effect, clock)
            draft = posting.finish(
                before.terms, transition.effect, business_date(now), to_account_id=to_account
            )
            await ledger.post_entry(session, draft, clock)
    if transition.status is PayoutStatus.PAID:
        await destinations.record_first_payout(session, before.destination_id, now)
    if before.status is PayoutStatus.HELD and transition.status is PayoutStatus.QUEUED:
        await queue_send(session, payout_id, before.kind, now)
    if event in _PERSON_EVENTS:
        await _audit(
            session,
            actor,
            f"payouts.payout_{event.value}",
            payout_id,
            clock,
            category="approval" if event in _REVIEW_EVENTS else "change",
            reason=reason,
        )
    return await get_payout(session, payout_id)


async def queue_send(
    session: AsyncSession,
    payout_id: str,
    kind: DestinationKind,
    now: datetime,
    *,
    key: str | None = None,
) -> None:
    """Ask the worker to send a queued payout. Queuing twice with the same key queues once.

    Only mobile money is sent through the provider. A bank payout waits for staff to pay it
    from the trust account, which is not built yet.
    """
    if kind is not DestinationKind.MOBILE_MONEY:
        return
    await enqueue(
        session,
        JobRequest(
            kind=SEND_KIND,
            idempotency_key=key or payout_id,
            payload={"payout_id": payout_id},
            max_attempts=SEND_ATTEMPTS,
            max_age=SEND_MAX_AGE,
        ),
        now=now,
    )


async def get_payout(session: AsyncSession, payout_id: str) -> PayoutView:
    row = (await session.execute(_GET, {"id": payout_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def held_for_review(session: AsyncSession, *, limit: int = 50) -> list[PayoutView]:
    """Payouts waiting for a staff decision, oldest first. Empty outside a staff transaction."""
    rows = await session.execute(_HELD, {"limit": max(1, min(limit, 100))})
    return [_view(row) for row in rows.all()]


@dataclass(frozen=True, slots=True)
class HeldUpPayout:
    """A queued payout first tried `HELD_UP_AFTER` or more ago, and taken by nothing since."""

    payout: PayoutView
    first_tried_at: datetime
    last_tried_at: datetime
    # Sends tried so far. Each one ended with the provider saying it took nothing.
    tries: int


async def held_up(session: AsyncSession, now: datetime, *, limit: int = 50) -> list[HeldUpPayout]:
    """Payouts held up at the provider, longest first. Empty outside a staff transaction.

    The sweep keeps sending them; this is for a person to see and find out why.
    """
    rows = await session.execute(
        _HELD_UP, {"since": now - HELD_UP_AFTER, "limit": max(1, min(limit, 100))}
    )
    return [
        HeldUpPayout(_view(row), row.first_tried_at, row.last_tried_at, int(row.tries))
        for row in rows.all()
    ]


async def payouts_for_client(
    session: AsyncSession, client_id: str, *, limit: int = 50
) -> list[PayoutView]:
    rows = await session.execute(
        _FOR_CLIENT, {"client_id": client_id, "limit": max(1, min(limit, 100))}
    )
    return [_view(row) for row in rows.all()]


async def _destination_account(
    session: AsyncSession, payout: PayoutView, effect: LedgerEffect, clock: Clock
) -> str:
    currency = payout.amount.currency
    if effect is LedgerEffect.RELEASE:
        return payout.source_account_id
    if effect is LedgerEffect.PARK:
        spec = posting.pending_account(payout.client_id, currency)
    else:
        destination = await destinations.get_destination(session, payout.destination_id)
        spec = posting.paid_from_account(payout.kind, destination.network, currency)
    return (await ledger.open_account(session, spec, clock)).id


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    payout_id: str,
    clock: Clock,
    *,
    category: str = "change",
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,  # type: ignore[arg-type]
            action=action,
            resource_type="payout",
            resource_id=payout_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
        ),
        clock,
    )


def _view(row: Row[Any]) -> PayoutView:
    return PayoutView(
        id=row.id,
        client_id=row.client_id,
        destination_id=row.destination_id,
        kind=DestinationKind(row.kind),
        amount=Money(int(row.amount_minor), Currency(row.currency)),
        status=PayoutStatus(row.status),
        business_date=row.business_date,
        provider_reference=row.provider_reference,
        failure_code=row.failure_code,
        reviewed_by=row.reviewed_by,
        created_at=row.created_at,
        source_account_id=row.source_account_id,
        source_reference=row.source_reference,
        in_flight_account_id=row.in_flight_account_id,
    )
