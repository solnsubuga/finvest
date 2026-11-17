"""Selling a bond holding early, through the dealing desk, by two people.

1. The client asks to sell their whole active holding and names where the money goes, with the
   app PIN on the phone. Nothing moves. They may cancel until the sale is priced.
2. The dealing desk sells that face value on the secondary market. One member of staff records
   the price it fetched, the day it settled and the desk's reference. They may withdraw it.
3. A different member of staff approves it, or sends it back with a note. Approving queues
   `bonds.settle_sale` in the same transaction. From then on the coupon run and the maturity
   run leave the holding alone.
4. The worker pays any coupon the holding was owed up to the day it sold, then posts one ledger
   entry, `bond.sold`: what the client paid out of their bond holding, the accrued interest from
   `interest.expense` with its tax to `tax.withholding_payable`, the proceeds after tax to the
   client's payout pending account for this sale, the accrued interest bought at purchase to
   `bond.accrued_interest_bought`, and the price difference to `bond.sold_above_cost` or
   `bond.sold_below_cost`. The entry's source is the holding, so the tax module reads its
   interest. It pays the proceeds to the destination the client named, or holds them until the
   client names one that payouts accepts, and marks the holding `sold`.

The rules are in `domain/sales.py`. Migration 0495 holds who may do each step in the database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs.queue import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.bonds import coupons, holdings
from app.modules.bonds.catalogue import invalid
from app.modules.bonds.domain.coupons import due_dates, is_owed
from app.modules.bonds.domain.holdings import HoldingStatus
from app.modules.bonds.domain.issues import (
    BondIssue,
    BondStatus,
    InvalidIssue,
    Sale,
    check_price,
    sale,
)
from app.modules.bonds.domain.sales import (
    OPEN,
    PayoutStatus,
    SaleRefusal,
    SaleStatus,
    check_request,
    check_sold_on,
    check_text,
    settlement,
)
from app.modules.bonds.maturity import ACCRUED_BOUGHT
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payouts import payouts
from app.modules.payouts.domain import posting as payout_posting
from app.modules.tax.domain.withholding import INTEREST_EXPENSE, TAX_PAYABLE
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

REQUEST_OPERATION = "bonds.request_sale"
SALE_RESOURCE = "bond_sale"
SETTLE_KIND = "bonds.settle_sale"
ABOVE_COST = "bond.sold_above_cost"
BELOW_COST = "bond.sold_below_cost"
CLIENT_PAGE = 100
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class SaleView:
    id: str
    holding_id: str
    client_id: str
    code: str
    name: str
    face: Money
    cost: Money
    destination_id: str
    requested_at: datetime
    status: SaleStatus
    price: int | None
    sold_on: date | None
    reason: str | None
    priced_by: str | None
    priced_at: datetime | None
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    clean: Money | None
    accrued: Money | None
    tax: Money | None
    # What the client is paid: the clean price and accrued interest, less tax on the interest.
    amount: Money | None
    settled_at: datetime | None
    payout_status: str | None
    payout_id: str | None
    payout_refusal: str | None
    closed_at: datetime | None
    # While priced or approved: what the sale will pay at the recorded price and day, so the
    # person approving sees what the client gets. The figures above are set once it is sold.
    quoted: Sale | None = None


_COLUMNS = (
    "s.id, s.holding_id, s.client_id, s.currency, s.face_minor, s.cost_minor, "
    "s.destination_id, s.requested_at, s.status, s.price, s.sold_on, s.reason, s.priced_by, "
    "s.priced_at, s.decided_by, s.decided_at, s.note, s.clean_minor, s.accrued_minor, "
    "s.tax_minor, s.amount_minor, s.settled_at, s.payout_status, s.payout_id, "
    "s.payout_refusal, s.closed_at, b.code, b.name, b.isin, b.coupon_bps, b.tax_bps, "
    "b.issue_date, b.maturity_date, b.minimum_face_minor, b.face_step_minor, "
    "b.status AS issue_status"
)
_FROM = (
    "FROM bond_sales s JOIN bond_holdings h ON h.id = s.holding_id "
    "JOIN bond_issues b ON b.id = h.issue_id"
)
_BY_ID = text(f"SELECT {_COLUMNS} {_FROM} WHERE s.id = :id")
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} {_FROM} WHERE s.id = :id FOR UPDATE OF s")
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE s.client_id = :client_id "
    "ORDER BY s.requested_at DESC, s.id DESC LIMIT :limit"
)
_FOR_STAFF = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE (CAST(:status AS text) IS NULL OR s.status = :status) "
    "ORDER BY s.requested_at DESC, s.id DESC LIMIT :limit"
)
_OPEN_FOR_HOLDING = text(
    "SELECT 1 FROM bond_sales WHERE holding_id = :holding_id AND status = ANY(:statuses)"
)
_INSERT = text(
    "INSERT INTO bond_sales (id, holding_id, client_id, currency, face_minor, cost_minor, "
    "destination_id, requested_at, status, updated_at) VALUES (:id, :holding_id, :client_id, "
    ":currency, :face, :cost, :destination_id, :now, 'requested', :now)"
)
_CANCEL = text(
    "UPDATE bond_sales SET status = 'cancelled', closed_at = :now, updated_at = :now WHERE id = :id"
)
_PRICE = text(
    "UPDATE bond_sales SET status = 'priced', price = :price, sold_on = :sold_on, "
    "reason = :reason, priced_by = :staff_id, priced_at = :now, updated_at = :now WHERE id = :id"
)
_BACK = text(
    "UPDATE bond_sales SET status = 'requested', price = NULL, sold_on = NULL, reason = NULL, "
    "priced_by = NULL, priced_at = NULL, note = :note, updated_at = :now WHERE id = :id"
)
_APPROVE = text(
    "UPDATE bond_sales SET status = 'approved', decided_by = :staff_id, decided_at = :now, "
    "updated_at = :now WHERE id = :id"
)
_SOLD = text(
    "UPDATE bond_sales SET status = 'sold', clean_minor = :clean, accrued_minor = :accrued, "
    "tax_minor = :tax, amount_minor = :amount, entry_id = :entry_id, settled_at = :now, "
    "payout_status = :payout_status, payout_id = :payout_id, payout_refusal = :refusal, "
    "updated_at = :now WHERE id = :id"
)
_HOLDING_SOLD = text(
    "UPDATE bond_holdings SET status = 'sold', updated_at = :now WHERE id = :id "
    "AND status = 'active'"
)
_SEND_ON = text(
    "UPDATE bond_sales SET payout_status = 'paid_out', payout_id = :payout_id, "
    "payout_refusal = NULL, updated_at = :now WHERE id = :id"
)
_LOCK_HOLDING = text("SELECT id FROM bond_holdings WHERE id = :id FOR UPDATE")
_PAID_DATES = text("SELECT coupon_date FROM bond_coupons WHERE holding_id = :holding_id")


# The client's commands --------------------------------------------------------------------------


async def request_sale(
    session: AsyncSession,
    client: Actor,
    holding_id: str,
    *,
    idempotency_key: str,
    destination_id: str,
    clock: Clock,
    confirm: Callable[[], Awaitable[None]] | None = None,
) -> SaleView:
    """Ask the dealing desk to sell the client's whole holding. Nothing moves yet.

    `confirm` is awaited once the holding and destination pass their checks, just before the
    ask is saved. The route passes the app PIN check, which raises to refuse. A retry with the
    same key returns the same ask without asking for the PIN again.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")

    async def command() -> Receipt:
        sale_id = await _ask(session, client, holding_id, destination_id, clock, confirm)
        return Receipt(201, {"sale_id": sale_id})

    outcome = await run_once(
        session,
        actor=client,
        operation=REQUEST_OPERATION,
        key=idempotency_key,
        request={"holding_id": holding_id, "destination_id": destination_id},
        now=clock.now(),
        command=command,
    )
    return await get_sale(session, str(outcome.receipt.response["sale_id"]))


async def _ask(
    session: AsyncSession,
    client: Actor,
    holding_id: str,
    destination_id: str,
    clock: Clock,
    confirm: Callable[[], Awaitable[None]] | None,
) -> str:
    holding = await holdings.get_holding(session, holding_id)
    if holding.client_id != client.id:
        raise ApplicationError("not_found")
    now = clock.now()
    if holding.status is not HoldingStatus.ACTIVE:
        raise invalid("holding_id", SaleRefusal.NOT_ACTIVE.value)
    refusal = check_request(holding.issue, holding.start_date, business_date(now))
    if refusal is not None:
        raise invalid("holding_id", refusal.value)
    await _refuse_if_open(session, holding.id)
    await coupons.usable(session, holding.client_id, destination_id)
    if confirm is not None:
        await confirm()
    sale_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": sale_id,
                    "holding_id": holding.id,
                    "client_id": holding.client_id,
                    "currency": holding.face.currency.value,
                    "face": holding.face.minor,
                    "cost": holding.cost.minor,
                    "destination_id": destination_id,
                    "now": now,
                },
            )
    except IntegrityError:
        # Another ask for this holding committed first.
        raise _already_asked() from None
    await _audit(
        session, client, "bonds.sale_requested", sale_id, holding.code, clock, kind="request"
    )
    return sale_id


async def cancel_sale(session: AsyncSession, client: Actor, sale_id: str, clock: Clock) -> SaleView:
    """Cancel an ask to sell, before the dealing desk has priced it."""
    view = await _locked(session, sale_id)
    if client.kind != "client" or view.client_id != client.id:
        raise ApplicationError("not_found")
    if view.status is not SaleStatus.REQUESTED:
        raise ApplicationError("conflict")
    await session.execute(_CANCEL, {"id": view.id, "now": clock.now()})
    await _audit(session, client, "bonds.sale_cancelled", view.id, view.code, clock)
    return await get_sale(session, view.id)


async def pay_out_held(
    session: AsyncSession,
    client: Actor,
    sale_id: str,
    *,
    destination_id: str,
    clock: Clock,
    confirm: Callable[[], Awaitable[None]] | None = None,
) -> SaleView:
    """Send on the proceeds of a sale that are waiting for a destination.

    Raises `payouts.PayoutRefused` if payouts will not pay it there now: it keeps waiting.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    view = await _locked(session, sale_id)
    if view.client_id != client.id:
        raise ApplicationError("not_found")
    if view.payout_status != PayoutStatus.HELD or view.amount is None:
        raise ApplicationError("conflict")
    await coupons.usable(session, view.client_id, destination_id)
    if confirm is not None:
        await confirm()
    payout = await payouts.create_payout(
        session,
        client,
        client_id=view.client_id,
        destination_id=destination_id,
        amount=view.amount,
        source_reference=view.id,
        request_key=f"bond-sold:{view.id}:{destination_id}",
        clock=clock,
    )
    await session.execute(_SEND_ON, {"id": view.id, "payout_id": payout.id, "now": clock.now()})
    return await get_sale(session, view.id)


# The dealing desk -------------------------------------------------------------------------------


async def price_sale(
    session: AsyncSession,
    staff: Actor,
    sale_id: str,
    *,
    price: int,
    sold_on: date,
    reason: str,
    clock: Clock,
) -> SaleView:
    """Record the price the dealing desk sold the holding at and the day it settled, for a
    second person to approve. `reason` carries the desk's reference for the trade."""
    staff_id = _staff_id(staff)
    view = await _locked(session, sale_id)
    if view.status is not SaleStatus.REQUESTED:
        raise ApplicationError("conflict")
    try:
        check_price(price)
    except InvalidIssue:
        raise invalid("price", SaleRefusal.PRICE_OUT_OF_RANGE.value) from None
    text_refusal = check_text(reason)
    if text_refusal is not None:
        raise invalid("reason", text_refusal.value)
    now = clock.now()
    await _check_holding(session, view, sold_on, business_date(now))
    await session.execute(
        _PRICE,
        {
            "id": view.id,
            "price": price,
            "sold_on": sold_on,
            "reason": reason.strip(),
            "staff_id": staff_id,
            "now": now,
        },
    )
    await _audit(
        session,
        staff,
        "bonds.sale_priced",
        view.id,
        view.code,
        clock,
        reason=reason.strip(),
        price=price,
        sold_on=sold_on.isoformat(),
    )
    return await get_sale(session, view.id)


async def approve_sale(session: AsyncSession, staff: Actor, sale_id: str, clock: Clock) -> SaleView:
    """Approve a priced sale as the second person. The worker pays the client."""
    staff_id = _staff_id(staff)
    view = await _locked(session, sale_id)
    if view.status is not SaleStatus.PRICED or view.sold_on is None:
        raise ApplicationError("conflict")
    if view.priced_by == staff_id:
        raise ApplicationError("forbidden")
    now = clock.now()
    # A coupon date may have passed since it was priced: then the holding was paid that coupon.
    await _check_holding(session, view, view.sold_on, business_date(now))
    await session.execute(_APPROVE, {"id": view.id, "staff_id": staff_id, "now": now})
    await enqueue(
        session,
        JobRequest(
            kind=SETTLE_KIND,
            idempotency_key=f"bond-sale:{view.id}",
            payload={"sale_id": view.id},
            max_attempts=20,
            max_age=timedelta(days=7),
        ),
        now=now,
    )
    await _audit(session, staff, "bonds.sale_approved", view.id, view.code, clock)
    return await get_sale(session, view.id)


async def send_back_sale(
    session: AsyncSession, staff: Actor, sale_id: str, *, note: str, clock: Clock
) -> SaleView:
    """Send a priced sale back to the desk, with a note. It waits to be priced again."""
    staff_id = _staff_id(staff)
    text_refusal = check_text(note)
    if text_refusal is not None:
        raise invalid("note", text_refusal.value)
    view = await _locked(session, sale_id)
    if view.status is not SaleStatus.PRICED:
        raise ApplicationError("conflict")
    if view.priced_by == staff_id:
        raise ApplicationError("forbidden")
    await session.execute(_BACK, {"id": view.id, "note": note.strip(), "now": clock.now()})
    await _audit(
        session, staff, "bonds.sale_sent_back", view.id, view.code, clock, reason=note.strip()
    )
    return await get_sale(session, view.id)


async def withdraw_sale(
    session: AsyncSession, staff: Actor, sale_id: str, clock: Clock
) -> SaleView:
    """Take back a price you recorded, before it is decided. The ask waits to be priced."""
    staff_id = _staff_id(staff)
    view = await _locked(session, sale_id)
    if view.status is not SaleStatus.PRICED:
        raise ApplicationError("conflict")
    if view.priced_by != staff_id:
        raise ApplicationError("forbidden")
    await session.execute(_BACK, {"id": view.id, "note": view.note, "now": clock.now()})
    await _audit(session, staff, "bonds.sale_withdrawn", view.id, view.code, clock)
    return await get_sale(session, view.id)


# The worker -------------------------------------------------------------------------------------


async def settle(session: AsyncSession, sale_id: str, clock: Clock) -> SaleView | None:
    """Pay an approved sale. Returns None when it was already paid. Worker only."""
    first = await get_sale(session, sale_id)
    await session.execute(_LOCK_HOLDING, {"id": first.holding_id})
    view = await _locked(session, sale_id)
    if view.status is SaleStatus.SOLD:
        return None
    if view.status is not SaleStatus.APPROVED or view.price is None or view.sold_on is None:
        raise ApplicationError("conflict")
    holding = await holdings.get_holding(session, view.holding_id)
    if holding.status is not HoldingStatus.ACTIVE:
        raise ApplicationError("conflict")
    issue = holding.issue
    # A coupon dated up to the day it sold that a run missed is the client's, paid first.
    paid_dates = set(
        (await session.execute(_PAID_DATES, {"holding_id": holding.id})).scalars().all()
    )
    for coupon_date in due_dates(issue, view.sold_on):
        if is_owed(holding.start_date, coupon_date) and coupon_date not in paid_dates:
            await coupons.pay_coupon(session, holding.id, coupon_date, clock)

    currency = holding.face.currency
    held_account = await ledger.open_account(session, holdings.holding_account(holding), clock)
    if await ledger.get_balance(session, held_account.id) != holding.cost:
        logger.error("bond_holding_balance_unexpected", holding_id=holding.id)
        raise ApplicationError("conflict")
    figures = settlement(
        issue, holding.face, holding.cost, holding.accrued, view.price, view.sold_on
    )
    now = clock.now()
    handover = await ledger.open_account(
        session, payout_posting.funding_account(holding.client_id, view.id, currency), clock
    )
    lines = [debit(held_account.id, holding.cost), credit(handover.id, figures.paid)]
    for code, kind, name, amount, line in (
        (INTEREST_EXPENSE, AccountKind.EXPENSE, "Interest paid to clients", figures.accrued, debit),
        (TAX_PAYABLE, AccountKind.LIABILITY, "Tax withheld from interest", figures.tax, credit),
        (
            ACCRUED_BOUGHT,
            AccountKind.INCOME,
            "Accrued interest clients bought",
            figures.bought_accrued,
            credit,
        ),
        (ABOVE_COST, AccountKind.EXPENSE, "Bonds sold above cost", figures.above_cost, debit),
        (BELOW_COST, AccountKind.INCOME, "Bonds sold below cost", figures.below_cost, credit),
    ):
        if amount.is_positive:
            account = await ledger.open_account(
                session, AccountSpec(code, kind, currency, name), clock
            )
            lines.append(line(account.id, amount))
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="bond.sold",
            effective_date=business_date(now),
            lines=tuple(lines),
            idempotency_key=f"bond-sold:{view.id}",
            source_type=coupons.SOURCE_TYPE,
            source_id=holding.id,
        ),
        clock,
    )
    payout_id, refusal = await coupons.pay_to(
        session,
        SYSTEM,
        holding.client_id,
        view.destination_id,
        view.id,
        figures.paid,
        clock,
        kind="bond-sold",
    )
    await session.execute(
        _SOLD,
        {
            "id": view.id,
            "clean": figures.clean.minor,
            "accrued": figures.accrued.minor,
            "tax": figures.tax.minor,
            "amount": figures.paid.minor,
            "entry_id": entry.id,
            "payout_status": PayoutStatus.HELD if payout_id is None else PayoutStatus.PAID_OUT,
            "payout_id": payout_id,
            "refusal": refusal,
            "now": now,
        },
    )
    await session.execute(_HOLDING_SOLD, {"id": holding.id, "now": now})
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="bonds.holding_sold",
            resource_type=holdings.HOLDING_RESOURCE,
            resource_id=holding.id,
            actor=AuditActor.system(),
            details={"bond": holding.code, "sale_id": view.id, "paid_out": payout_id is not None},
        ),
        clock,
    )
    logger.info(
        "bond_holding_sold",
        holding_id=holding.id,
        paid_out=payout_id is not None,
        error_code=refusal,
    )
    return await get_sale(session, view.id)


# Reads ------------------------------------------------------------------------------------------


async def get_sale(session: AsyncSession, sale_id: str) -> SaleView:
    row = (await session.execute(_BY_ID, {"id": sale_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def client_sales(session: AsyncSession, client_id: str) -> list[SaleView]:
    """The client's asks to sell, newest first."""
    rows = await session.execute(_FOR_CLIENT, {"client_id": client_id, "limit": CLIENT_PAGE})
    return [_view(row) for row in rows.all()]


async def staff_sales(
    session: AsyncSession, staff: Actor, status: SaleStatus | None, limit: int
) -> list[SaleView]:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(
        _FOR_STAFF, {"status": None if status is None else status.value, "limit": limit}
    )
    return [_view(row) for row in rows.all()]


# Helpers ----------------------------------------------------------------------------------------


def _staff_id(actor: Actor) -> str:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


async def _locked(session: AsyncSession, sale_id: str) -> SaleView:
    row = (await session.execute(_BY_ID_LOCKED, {"id": sale_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def _refuse_if_open(session: AsyncSession, holding_id: str) -> None:
    statuses = [s.value for s in (*OPEN, SaleStatus.SOLD)]
    found = await session.execute(
        _OPEN_FOR_HOLDING, {"holding_id": holding_id, "statuses": statuses}
    )
    if found.first() is not None:
        raise _already_asked()


def _already_asked() -> ApplicationError:
    return ApplicationError(
        "conflict",
        fields=[FieldProblem(field_path=["holding_id"], code=SaleRefusal.ALREADY_ASKED.value)],
    )


async def _check_holding(session: AsyncSession, view: SaleView, sold_on: date, today: date) -> None:
    holding = await holdings.get_holding(session, view.holding_id)
    if holding.status is not HoldingStatus.ACTIVE:
        raise ApplicationError("conflict")
    refusal = check_sold_on(holding.issue, holding.start_date, sold_on, today)
    if refusal is not None:
        raise invalid("sold_on", refusal.value)


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    sale_id: str,
    code: str,
    clock: Clock,
    *,
    reason: str | None = None,
    **details: object,
) -> None:
    # The bond and the sale's terms. No amounts tied to the client.
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=action,
            resource_type=SALE_RESOURCE,
            resource_id=sale_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details={"bond": code, **details},
        ),
        clock,
    )


def _view(row: Row[Any]) -> SaleView:
    currency = Currency(row.currency)

    def money(minor: int | None) -> Money | None:
        return None if minor is None else Money(minor, currency)

    status = SaleStatus(row.status)
    face = Money(row.face_minor, currency)
    quoted = None
    if status in (SaleStatus.PRICED, SaleStatus.APPROVED) and row.price and row.sold_on:
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
        quoted = sale(issue, face, row.price, row.sold_on)

    return SaleView(
        id=row.id,
        holding_id=row.holding_id,
        client_id=row.client_id,
        code=row.code,
        name=row.name,
        face=face,
        cost=Money(row.cost_minor, currency),
        destination_id=row.destination_id,
        requested_at=row.requested_at,
        status=status,
        price=row.price,
        sold_on=row.sold_on,
        reason=row.reason,
        priced_by=row.priced_by,
        priced_at=row.priced_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        note=row.note,
        clean=money(row.clean_minor),
        accrued=money(row.accrued_minor),
        tax=money(row.tax_minor),
        amount=money(row.amount_minor),
        settled_at=row.settled_at,
        payout_status=row.payout_status,
        payout_id=row.payout_id,
        payout_refusal=row.payout_refusal,
        closed_at=row.closed_at,
        quoted=quoted,
    )
