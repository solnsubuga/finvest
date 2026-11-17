"""Clients' holdings of bond issues: bought at a quote, paid once, started by the worker.

Every function works inside the caller's transaction. The rules are in `domain/holdings.py`;
migration 0491 holds the important ones in the database too: only the client buys for
themselves, only while the issue is open and at the price in force, and only the worker moves
a holding on, once.

A holding's cost is paid like an investment: the payments module takes one payment for exactly
the cost, puts it in the client's awaiting-start account for this holding, and publishes
`payments.deposit_confirmed`. The worker then moves it to the client's bond holding in one
ledger entry. Functions whose names start with `client_` return only figures after tax.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.bonds import catalogue
from app.modules.bonds.catalogue import invalid
from app.modules.bonds.domain.holdings import (
    MAX_UNPAID,
    HoldingStatus,
    can_be_cancelled,
    can_be_paid,
    check_start,
    pay_by,
)
from app.modules.bonds.domain.issues import (
    BondIssue,
    BondStatus,
    check_purchase,
    coupon_gross,
    quote,
    remaining_coupons,
    tax_on,
)
from app.modules.bonds.domain.issues import Refusal as BuyRefusal
from app.modules.closures.pending import refuse_if_closing
from app.modules.kyc import service as kyc
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payments import service as payments
from app.modules.payments.domain.deposits import Purpose
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.limits import largest_single_amount
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

REQUEST_OPERATION = "bonds.request_purchase"
HOLDING_RESOURCE = "bond_holding"
# The same account an investment's payment waits in, under the holding's ID.
AWAITING_START = "client.awaiting_start"
BOND_HOLDING = "client.bond_holding"
SWEEP_BATCH = 200
CLIENT_PAGE = 100
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Holding:
    id: str
    client_id: str
    issue_id: str
    code: str
    name: str
    face: Money
    price: int
    accrued: Money
    cost: Money
    quoted_on: date
    status: HoldingStatus
    created_at: datetime
    pay_by: datetime
    start_date: date | None
    issue: BondIssue
    # Where the client wants coupons paid. None until they choose.
    payout_destination_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClientHolding:
    """What a client sees of their holding. Every figure is after tax."""

    id: str
    code: str
    name: str
    face: Money
    price: int
    accrued: Money
    cost: Money
    status: HoldingStatus
    created_at: datetime
    # Until when an unpaid holding may be paid. None once it is not unpaid.
    pay_by: datetime | None
    start_date: date | None
    maturity_date: date
    # One coupon after tax, the coupons still to be paid to it and the next one's date.
    coupon_net: Money
    coupons_left: int
    next_coupon_date: date | None
    # Everything still to come if held to maturity: the coupons and the face value.
    still_to_come: Money
    payout_destination_id: str | None = None


@dataclass(frozen=True, slots=True)
class PayableHolding:
    holding_id: str
    client_id: str
    amount: Money


_COLUMNS = (
    "h.id, h.client_id, h.issue_id, h.currency, h.face_minor, h.price, h.accrued_minor, "
    "h.cost_minor, h.quoted_on, h.status, h.created_at, h.pay_by, h.start_date, "
    "h.payout_destination_id, "
    "b.code, b.isin, b.name, b.coupon_bps, b.tax_bps, b.issue_date, b.maturity_date, "
    "b.minimum_face_minor, b.face_step_minor, b.status AS issue_status"
)
_FROM = "FROM bond_holdings h JOIN bond_issues b ON b.id = h.issue_id"
_BY_ID = text(f"SELECT {_COLUMNS} {_FROM} WHERE h.id = :id")
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} {_FROM} WHERE h.id = :id FOR UPDATE OF h")
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE h.client_id = :client_id AND h.status <> 'cancelled' "
    "ORDER BY h.created_at DESC, h.id DESC LIMIT :limit"
)
_ALL = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE (CAST(:status AS text) IS NULL OR h.status = :status) "
    "ORDER BY h.created_at DESC, h.id DESC LIMIT :limit"
)
_LATEST_PRICE = text(
    "SELECT id, price FROM bond_prices WHERE issue_id = :issue_id "
    "ORDER BY effective_at DESC, id DESC LIMIT 1"
)
_COUNT_UNPAID = text(
    "SELECT count(*) FROM bond_holdings WHERE client_id = :client_id "
    "AND status = 'unpaid' AND pay_by > :now"
)
_INSERT = text(
    "INSERT INTO bond_holdings (id, client_id, issue_id, currency, face_minor, price, price_id, "
    "accrued_minor, cost_minor, quoted_on, status, created_at, pay_by, updated_at) VALUES "
    "(:id, :client_id, :issue_id, :currency, :face_minor, :price, :price_id, :accrued_minor, "
    ":cost_minor, :quoted_on, 'unpaid', :now, :pay_by, :now)"
)
_START = text(
    "UPDATE bond_holdings SET status = 'active', start_date = :start_date, started_at = :now, "
    "start_entry_id = :entry_id, updated_at = :now WHERE id = :id"
)
_CANCEL = text(
    "UPDATE bond_holdings SET status = 'cancelled', cancelled_at = :now, updated_at = :now "
    "WHERE id = :id"
)
_OVERDUE = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE h.status = 'unpaid' AND h.pay_by <= :cutoff "
    "ORDER BY h.pay_by LIMIT :limit FOR UPDATE OF h SKIP LOCKED"
)
_ADVISORY = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


def _holding_lock(holding_id: str) -> dict[str, str]:
    # Taken by the payment check and by the sweep: either the sweep sees the deposit the check
    # let in, or the check sees the holding the sweep cancelled. Never neither.
    return {"lock": f"finvest.bonds.holding:{holding_id}"}


# The client's commands --------------------------------------------------------------------------


async def request_purchase(
    session: AsyncSession,
    client: Actor,
    *,
    idempotency_key: str,
    code: str,
    face: Money,
    clock: Clock,
) -> Holding:
    """Save the purchase a client reviewed and confirmed, at today's price. Nothing is paid.

    The price, accrued interest and cost hold for the payment window, and while the next coupon
    is still to come. A retry with the same key returns the same holding. The same key for a
    different request is refused.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    client_id = client.id

    async def command() -> Receipt:
        holding_id = await _create(session, client_id, code, face, clock)
        return Receipt(201, {"holding_id": holding_id})

    outcome = await run_once(
        session,
        actor=client,
        operation=REQUEST_OPERATION,
        key=idempotency_key,
        request={"code": code, "face_minor": face.minor, "currency": face.currency.value},
        now=clock.now(),
        command=command,
    )
    return await get_holding(session, str(outcome.receipt.response["holding_id"]))


async def _create(
    session: AsyncSession, client_id: str, code: str, face: Money, clock: Clock
) -> str:
    if await kyc.verified_name(session, client_id) is None:
        # Nobody invests before their identity check is approved.
        raise ApplicationError("forbidden")
    # Nothing new starts while two people check the account is clear to close.
    await refuse_if_closing(session, client_id)
    now = clock.now()
    today = business_date(now)
    view = await catalogue.issue_view(session, code)
    if view.issue.status is BondStatus.DRAFT:
        raise ApplicationError("not_found")
    latest = (await session.execute(_LATEST_PRICE, {"issue_id": view.id})).one_or_none()
    if latest is None:
        raise invalid("code", BuyRefusal.NO_PRICE.value)
    if face.currency is not view.issue.currency:
        raise invalid("face_minor", BuyRefusal.CURRENCY_NOT_SUPPORTED.value)
    refusal = check_purchase(view.issue, face, today, largest_single_amount(face.currency))
    if refusal is not None:
        field = "code" if refusal in _ISSUE_REFUSALS else "face_minor"
        raise invalid(field, refusal.value)
    found = quote(view.issue, face, latest.price, today)
    await session.execute(_ADVISORY, {"lock": f"finvest.bonds.client:{client_id}"})
    waiting = (
        await session.execute(_COUNT_UNPAID, {"client_id": client_id, "now": now})
    ).scalar_one()
    if waiting >= MAX_UNPAID:
        raise ApplicationError("conflict")
    holding_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": holding_id,
            "client_id": client_id,
            "issue_id": view.id,
            "currency": face.currency.value,
            "face_minor": face.minor,
            "price": latest.price,
            "price_id": latest.id,
            "accrued_minor": found.accrued.minor,
            "cost_minor": found.cost.minor,
            "quoted_on": today,
            "now": now,
            "pay_by": pay_by(now),
        },
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="bonds.purchase_requested",
            resource_type=HOLDING_RESOURCE,
            resource_id=holding_id,
            actor=AuditActor.client(client_id),
            details={"bond": view.issue.code},
        ),
        clock,
    )
    logger.info("bond_purchase_requested", holding_id=holding_id, bond=view.issue.code)
    return holding_id


_ISSUE_REFUSALS = {BuyRefusal.ISSUE_NOT_OPEN, BuyRefusal.TOO_CLOSE_TO_MATURITY, BuyRefusal.NO_PRICE}


# What the payments module calls -----------------------------------------------------------------


async def payable_holding(
    session: AsyncSession, *, client_id: str, holding_id: str, clock: Clock
) -> PayableHolding:
    """Who pays for a holding and exactly how much, checked before a deposit is taken.

    `not_found` if there is no such holding or it is another client's. `conflict` if it can no
    longer be paid for: it started, was cancelled, its window closed, or a coupon fell due.
    """
    await session.execute(_ADVISORY, _holding_lock(holding_id))
    row = (await session.execute(_BY_ID, {"id": holding_id})).one_or_none()
    if row is None or row.client_id != client_id:
        raise ApplicationError("not_found")
    holding = _from_row(row)
    now = clock.now()
    if not can_be_paid(
        holding.status,
        holding.pay_by,
        now,
        issue=holding.issue,
        quoted_on=holding.quoted_on,
        today=business_date(now),
    ):
        raise ApplicationError("conflict")
    return PayableHolding(holding.id, holding.client_id, holding.cost)


def holding_account(holding: Holding) -> AccountSpec:
    return AccountSpec(
        BOND_HOLDING,
        AccountKind.LIABILITY,
        holding.face.currency,
        "Client bond holding",
        owner_client_id=holding.client_id,
        reference=holding.id,
    )


def _awaiting_account(holding: Holding) -> AccountSpec:
    return AccountSpec(
        AWAITING_START,
        AccountKind.LIABILITY,
        holding.face.currency,
        "Paid in, awaiting start",
        owner_client_id=holding.client_id,
        reference=holding.id,
    )


# The worker -------------------------------------------------------------------------------------


async def start_holding(
    session: AsyncSession, holding_id: str, *, confirmed_on: date, clock: Clock
) -> Holding:
    """Start a holding whose payment was confirmed on `confirmed_on`. Worker only.

    The whole cost must be waiting in the client's awaiting-start account for this holding. It
    moves to their bond holding in one ledger entry, in this transaction. Calling again for a
    holding that already started changes nothing and returns it.

    Refused with `conflict` if the money waiting is not exactly the cost, the holding was
    cancelled, or a coupon fell due after the quote. The money then stays where it is, for
    staff.
    """
    row = (await session.execute(_BY_ID_LOCKED, {"id": holding_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    holding = _from_row(row)
    if holding.status is HoldingStatus.ACTIVE:
        return holding
    now = clock.now()
    today = business_date(now)
    awaiting = await ledger.open_account(session, _awaiting_account(holding), clock)
    refusal = check_start(
        holding.status,
        issue=holding.issue,
        cost=holding.cost,
        waiting=await ledger.get_balance(session, awaiting.id),
        quoted_on=holding.quoted_on,
        confirmed_on=confirmed_on,
        today=today,
    )
    if refusal is not None:
        logger.warning("bond_holding_not_started", holding_id=holding.id, error_code=refusal.value)
        raise ApplicationError("conflict")
    held = await ledger.open_account(session, holding_account(holding), clock)
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="bond.bought",
            effective_date=today,
            lines=(debit(awaiting.id, holding.cost), credit(held.id, holding.cost)),
            idempotency_key=f"bond-start:{holding.id}",
            source_type="bond_holding",
            source_id=holding.id,
        ),
        clock,
    )
    await session.execute(
        _START,
        {"id": holding.id, "start_date": confirmed_on, "now": now, "entry_id": entry.id},
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="bonds.holding_started",
            resource_type=HOLDING_RESOURCE,
            resource_id=holding.id,
            actor=AuditActor.system(),
            details={"bond": holding.code},
        ),
        clock,
    )
    logger.info("bond_holding_started", holding_id=holding.id)
    return await get_holding(session, holding.id)


async def cancel_unpaid(session: AsyncSession, clock: Clock, *, limit: int = SWEEP_BATCH) -> int:
    """Cancel holdings nobody paid for in time. Worker only. Returns how many.

    One with money waiting is left alone: that money arrived, so it starts or goes to staff.
    So is one with a deposit that has not failed, which may still confirm.
    """
    now = clock.now()
    cancelled = 0
    rows = (await session.execute(_OVERDUE, {"cutoff": now, "limit": limit})).all()
    for row in rows:
        holding = _from_row(row)
        await session.execute(_ADVISORY, _holding_lock(holding.id))
        awaiting = await ledger.open_account(session, _awaiting_account(holding), clock)
        waiting = await ledger.get_balance(session, awaiting.id)
        live = await payments.has_live_deposit(session, purpose=Purpose.BOND, purpose_id=holding.id)
        if not live and can_be_cancelled(holding.status, holding.pay_by, now, waiting):
            await session.execute(_CANCEL, {"id": holding.id, "now": now})
            cancelled += 1
    return cancelled


# Reads ------------------------------------------------------------------------------------------


async def get_holding(session: AsyncSession, holding_id: str) -> Holding:
    """The holding, if this transaction may see it: a client sees only their own."""
    row = (await session.execute(_BY_ID, {"id": holding_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def client_holdings(
    session: AsyncSession, client_id: str, clock: Clock
) -> list[ClientHolding]:
    """The client's holdings that were not cancelled, newest first, after tax."""
    rows = await session.execute(_FOR_CLIENT, {"client_id": client_id, "limit": CLIENT_PAGE})
    today = business_date(clock.now())
    return [client_view(_from_row(row), today) for row in rows.all()]


async def client_holding(
    session: AsyncSession, client_id: str, holding_id: str, clock: Clock
) -> ClientHolding:
    holding = await get_holding(session, holding_id)
    if holding.client_id != client_id:
        raise ApplicationError("not_found")
    return client_view(holding, business_date(clock.now()))


async def staff_holdings(
    session: AsyncSession, staff: Actor, status: HoldingStatus | None, limit: int
) -> list[Holding]:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(_ALL, {"status": status.value if status else None, "limit": limit})
    return [_from_row(row) for row in rows.all()]


def client_view(holding: Holding, today: date) -> ClientHolding:
    issue = holding.issue
    gross = coupon_gross(issue, holding.face)
    net = gross - tax_on(issue, gross)
    # A holding is paid every coupon dated after the day it started. Until it starts, the
    # coupons after the quote are what it would be paid.
    after = holding.start_date or holding.quoted_on
    # A holding sold, repaid or cancelled is paid nothing more.
    over = holding.status in (HoldingStatus.SOLD, HoldingStatus.MATURED, HoldingStatus.CANCELLED)
    left = (
        () if over else tuple(d for d in remaining_coupons(issue, max(after, today)) if d > after)
    )
    still_to_come = Money(
        net.minor * len(left) + (holding.face.minor if left else 0), issue.currency
    )
    unpaid = holding.status is HoldingStatus.UNPAID
    return ClientHolding(
        id=holding.id,
        code=holding.code,
        name=holding.name,
        face=holding.face,
        price=holding.price,
        accrued=holding.accrued,
        cost=holding.cost,
        status=holding.status,
        created_at=holding.created_at,
        pay_by=holding.pay_by if unpaid else None,
        start_date=holding.start_date,
        maturity_date=issue.maturity_date,
        coupon_net=net,
        coupons_left=len(left),
        next_coupon_date=left[0] if left else None,
        still_to_come=still_to_come,
        payout_destination_id=holding.payout_destination_id,
    )


def _from_row(row: Row[Any]) -> Holding:
    currency = Currency(row.currency)
    issue = BondIssue(
        code=row.code,
        isin=row.isin,
        name=row.name,
        currency=currency,
        coupon_bps=row.coupon_bps,
        tax_bps=row.tax_bps,
        issue_date=row.issue_date,
        maturity_date=row.maturity_date,
        minimum_face=Money(row.minimum_face_minor, currency),
        face_step=Money(row.face_step_minor, currency),
        status=BondStatus(row.issue_status),
    )
    return Holding(
        id=row.id,
        client_id=row.client_id,
        issue_id=row.issue_id,
        code=row.code,
        name=row.name,
        face=Money(row.face_minor, currency),
        price=row.price,
        accrued=Money(row.accrued_minor, currency),
        cost=Money(row.cost_minor, currency),
        quoted_on=row.quoted_on,
        status=HoldingStatus(row.status),
        created_at=row.created_at,
        pay_by=row.pay_by,
        start_date=row.start_date,
        issue=issue,
        payout_destination_id=row.payout_destination_id,
    )
