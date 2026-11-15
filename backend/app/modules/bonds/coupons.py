"""Bond coupons: the run that pays them, and what a client does with one that waits.

Every six months, on each coupon date before maturity, the worker pays each holding that
started before that date its coupon, the way an investment's interest is paid at maturity. Each
run also pays any coupon an earlier run missed, so a coupon is never lost to a stopped scheduler:

* one ledger entry, `bond.coupon`: the coupon before tax from `interest.expense`, the tax to
  `tax.withholding_payable`, and the rest handed to payouts in the client's "payout pending"
  account for that coupon. The entry's source is the holding, so the tax module reads it.
* a payout to the destination the client chose for the holding. With none chosen, or one the
  payout module refuses, the coupon is held: the money waits, handed over, until the client
  names a destination for it.

The last coupon is paid with the face value at maturity, not here. The rules are in
`domain/coupons.py`. Migration 0492 lets only the worker save a coupon, and lets a held one be
sent on once.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.bonds import holdings
from app.modules.bonds.catalogue import invalid
from app.modules.bonds.domain.coupons import (
    HoldReason,
    coupon,
    due_dates,
    is_coupon_run_day,
    is_owed,
)
from app.modules.bonds.domain.holdings import HoldingStatus
from app.modules.bonds.domain.issues import BondIssue, BondStatus
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain import posting as payout_posting
from app.modules.tax.domain.withholding import INTEREST_EXPENSE, TAX_PAYABLE
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

BATCH = 200
SOURCE_TYPE = "bond_holding"
COUPON_RESOURCE = "bond_coupon"
CLIENT_PAGE = 100
logger = structlog.get_logger()


class CouponStatus:
    PAID_OUT = "paid_out"
    HELD = "held"


@dataclass(frozen=True, slots=True)
class CouponView:
    id: str
    holding_id: str
    client_id: str
    code: str
    name: str
    coupon_date: date
    gross: Money
    tax: Money
    net: Money
    status: str
    payout_id: str | None
    payout_refusal: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Batch:
    # The last holding looked at, to carry on after. None when nothing was left.
    last_id: str | None
    paid: int
    held: int
    failed: int


# Every issue someone holds, whatever its status: a coupon owed is paid even if the issue has
# since been paused or matured.
_ISSUES = text(
    "SELECT b.id, b.code, b.isin, b.name, b.currency, b.coupon_bps, b.tax_bps, b.issue_date, "
    "b.maturity_date, b.minimum_face_minor, b.face_step_minor, b.status FROM bond_issues b "
    "WHERE EXISTS (SELECT FROM bond_holdings h WHERE h.issue_id = b.id AND h.status = 'active')"
)
# A holding whose early sale is approved is left to the sale, which pays it to the day it sold.
SALE_APPROVED = (
    "NOT EXISTS (SELECT FROM bond_sales s WHERE s.holding_id = h.id AND s.status = 'approved')"
)
# Holdings owed any of the coupons given as (issue, date) pairs, and not yet paid it.
_DUE = text(
    "SELECT DISTINCT h.id FROM bond_holdings h "  # noqa: S608 - constant SQL
    "JOIN unnest(CAST(:issue_ids AS text[]), CAST(:dates AS date[])) AS d(issue_id, coupon_date) "
    "ON d.issue_id = h.issue_id "
    "WHERE h.status = 'active' AND h.start_date < d.coupon_date AND h.id > :after "
    f"AND {SALE_APPROVED} "
    "AND NOT EXISTS (SELECT FROM bond_coupons c WHERE c.holding_id = h.id "
    "AND c.coupon_date = d.coupon_date) ORDER BY h.id LIMIT :limit"
)
_PAID_DATES = text("SELECT coupon_date FROM bond_coupons WHERE holding_id = :holding_id")
_LOCK_HOLDING = text("SELECT id FROM bond_holdings WHERE id = :id FOR UPDATE")
_EXISTS = text(
    "SELECT 1 FROM bond_coupons WHERE holding_id = :holding_id AND coupon_date = :coupon_date"
)
_INSERT = text(
    "INSERT INTO bond_coupons (id, holding_id, client_id, coupon_date, currency, gross_minor, "
    "tax_minor, net_minor, entry_id, status, payout_id, payout_refusal, created_at, updated_at) "
    "VALUES (:id, :holding_id, :client_id, :coupon_date, :currency, :gross, :tax, :net, "
    ":entry_id, :status, :payout_id, :refusal, :now, :now)"
)
_COLUMNS = (
    "c.id, c.holding_id, c.client_id, c.coupon_date, c.currency, c.gross_minor, c.tax_minor, "
    "c.net_minor, c.status, c.payout_id, c.payout_refusal, c.created_at, b.code, b.name"
)
_FROM = (
    "FROM bond_coupons c JOIN bond_holdings h ON h.id = c.holding_id "
    "JOIN bond_issues b ON b.id = h.issue_id"
)
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} {_FROM} WHERE c.id = :id FOR UPDATE OF c")
_BY_ID = text(f"SELECT {_COLUMNS} {_FROM} WHERE c.id = :id")
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE c.client_id = :client_id "
    "ORDER BY c.coupon_date DESC, c.id DESC LIMIT :limit"
)
_FOR_STAFF = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE (CAST(:status AS text) IS NULL OR c.status = :status) "
    "ORDER BY c.coupon_date DESC, c.id DESC LIMIT :limit"
)
_SEND_ON = text(
    "UPDATE bond_coupons SET status = 'paid_out', payout_id = :payout_id, "
    "payout_refusal = NULL, updated_at = :now WHERE id = :id"
)
_CHOOSE = text(
    "UPDATE bond_holdings SET payout_destination_id = :destination_id, updated_at = :now "
    "WHERE id = :id"
)


# The worker -------------------------------------------------------------------------------------


async def pay_batch(
    session: AsyncSession,
    run_date: date,
    clock: Clock,
    *,
    after: str = "",
    limit: int = BATCH,
) -> Batch:
    """Pay the next holdings, in ID order after `after`, every coupon they are owed dated on or
    before `run_date` and not yet paid.

    So a run pays its own day's coupons and any an earlier run missed, however long the
    scheduler was down. Worker only. Each coupon is paid inside its own savepoint: one that
    cannot be paid is left as it was, logged, and stays owed for the next run or retry.
    """
    if run_date > business_date(clock.now()):
        # A coupon is never paid before its date.
        raise ApplicationError("conflict")
    issues = {row.id: _issue(row) for row in (await session.execute(_ISSUES)).all()}
    owed = {issue_id: due_dates(issue, run_date) for issue_id, issue in issues.items()}
    pairs = [(issue_id, day) for issue_id, days in owed.items() for day in days]
    if not pairs:
        return Batch(last_id=None, paid=0, held=0, failed=0)
    ids = [
        row.id
        for row in (
            await session.execute(
                _DUE,
                {
                    "issue_ids": [issue_id for issue_id, _ in pairs],
                    "dates": [day for _, day in pairs],
                    "after": after,
                    "limit": limit,
                },
            )
        ).all()
    ]
    paid = held = failed = 0
    for holding_id in ids:
        for coupon_date in await _unpaid_dates(session, holding_id, run_date):
            try:
                async with session.begin_nested():
                    outcome = await pay_coupon(session, holding_id, coupon_date, clock)
            except Exception as error:
                failed += 1
                logger.error(
                    "bond_coupon_not_paid", holding_id=holding_id, error_type=type(error).__name__
                )
                continue
            if outcome == CouponStatus.PAID_OUT:
                paid += 1
            elif outcome == CouponStatus.HELD:
                held += 1
    return Batch(last_id=ids[-1] if ids else None, paid=paid, held=held, failed=failed)


async def _unpaid_dates(session: AsyncSession, holding_id: str, run_date: date) -> list[date]:
    """The coupon dates on or before `run_date` the holding is owed and not yet paid, oldest
    first."""
    holding = await holdings.get_holding(session, holding_id)
    paid = set((await session.execute(_PAID_DATES, {"holding_id": holding_id})).scalars().all())
    return [
        day
        for day in due_dates(holding.issue, run_date)
        if is_owed(holding.start_date, day) and day not in paid
    ]


async def pay_coupon(
    session: AsyncSession, holding_id: str, coupon_date: date, clock: Clock
) -> str | None:
    """Pay one holding the coupon dated `coupon_date`. Returns how it ended, or None when it
    was already paid or is not owed. Worker only."""
    await session.execute(_LOCK_HOLDING, {"id": holding_id})
    holding = await holdings.get_holding(session, holding_id)
    if holding.status is not HoldingStatus.ACTIVE or not is_owed(holding.start_date, coupon_date):
        return None
    if not is_coupon_run_day(holding.issue, coupon_date):
        return None
    found = (
        await session.execute(_EXISTS, {"holding_id": holding.id, "coupon_date": coupon_date})
    ).first()
    if found is not None:
        return None
    figures = coupon(holding.issue, holding.face)
    now = clock.now()
    coupon_id = push_id()
    currency = holding.face.currency
    expense = await ledger.open_account(
        session,
        AccountSpec(INTEREST_EXPENSE, AccountKind.EXPENSE, currency, "Interest paid to clients"),
        clock,
    )
    handover = await ledger.open_account(
        session, payout_posting.funding_account(holding.client_id, coupon_id, currency), clock
    )
    lines = [debit(expense.id, figures.gross), credit(handover.id, figures.net)]
    if figures.tax.is_positive:
        payable = await ledger.open_account(
            session,
            AccountSpec(TAX_PAYABLE, AccountKind.LIABILITY, currency, "Tax withheld from interest"),
            clock,
        )
        lines.append(credit(payable.id, figures.tax))
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="bond.coupon",
            effective_date=business_date(now),
            lines=tuple(lines),
            idempotency_key=f"bond-coupon:{holding.id}:{coupon_date.isoformat()}",
            source_type=SOURCE_TYPE,
            source_id=holding.id,
        ),
        clock,
    )
    payout_id, refusal = await pay_to_destination(
        session, SYSTEM, holding, coupon_id, figures.net, clock
    )
    await session.execute(
        _INSERT,
        {
            "id": coupon_id,
            "holding_id": holding.id,
            "client_id": holding.client_id,
            "coupon_date": coupon_date,
            "currency": currency.value,
            "gross": figures.gross.minor,
            "tax": figures.tax.minor,
            "net": figures.net.minor,
            "entry_id": entry.id,
            "status": CouponStatus.HELD if payout_id is None else CouponStatus.PAID_OUT,
            "payout_id": payout_id,
            "refusal": refusal,
            "now": now,
        },
    )
    logger.info(
        "bond_coupon_paid",
        holding_id=holding.id,
        paid_out=payout_id is not None,
        error_code=refusal,
    )
    return CouponStatus.HELD if payout_id is None else CouponStatus.PAID_OUT


async def pay_to_destination(
    session: AsyncSession,
    actor: Actor,
    holding: holdings.Holding,
    reference_id: str,
    amount: Money,
    clock: Clock,
    *,
    kind: str = "bond-coupon",
) -> tuple[str | None, str | None]:
    """Pay money already handed to payouts for `reference_id` (a coupon, or the repayment at
    maturity) to the holding's destination. Returns the payout, or why the money waits."""
    return await pay_to(
        session,
        actor,
        holding.client_id,
        holding.payout_destination_id,
        reference_id,
        amount,
        clock,
        kind=kind,
    )


async def pay_to(
    session: AsyncSession,
    actor: Actor,
    client_id: str,
    destination_id: str | None,
    reference_id: str,
    amount: Money,
    clock: Clock,
    *,
    kind: str,
) -> tuple[str | None, str | None]:
    """Pay money already handed to payouts for `reference_id` to `destination_id`. Returns the
    payout, or why the money waits."""
    if destination_id is None:
        return None, HoldReason.NO_DESTINATION
    try:
        # In a savepoint, so a refusal part way leaves nothing of the payout behind.
        async with session.begin_nested():
            payout = await payouts.create_payout(
                session,
                actor,
                client_id=client_id,
                destination_id=destination_id,
                amount=amount,
                source_reference=reference_id,
                request_key=f"{kind}:{reference_id}:{destination_id}",
                clock=clock,
            )
    except payouts.PayoutRefused as refused:
        return None, refused.code
    except ApplicationError as error:
        # The destination was removed or is no longer the client's.
        return None, f"destination_{error.code}"
    return payout.id, None


# The client's commands --------------------------------------------------------------------------


async def choose_destination(
    session: AsyncSession,
    client: Actor,
    holding_id: str,
    *,
    destination_id: str | None,
    clock: Clock,
) -> holdings.Holding:
    """Choose where the holding's coupons are paid, or clear the choice. Coupons already held
    are sent on one at a time with `pay_out_held`."""
    holding = await _own(session, client, holding_id)
    if holding.status in (HoldingStatus.CANCELLED, HoldingStatus.MATURED, HoldingStatus.SOLD):
        raise ApplicationError("conflict")
    if destination_id is not None:
        await usable(session, holding.client_id, destination_id)
    await session.execute(
        _CHOOSE, {"id": holding.id, "destination_id": destination_id, "now": clock.now()}
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="bonds.coupon_destination_chosen",
            resource_type=holdings.HOLDING_RESOURCE,
            resource_id=holding.id,
            actor=AuditActor.client(holding.client_id),
            details={"destination_id": destination_id},
        ),
        clock,
    )
    return await holdings.get_holding(session, holding.id)


async def pay_out_held(
    session: AsyncSession,
    client: Actor,
    coupon_id: str,
    *,
    destination_id: str,
    clock: Clock,
    confirm: Callable[[], Awaitable[None]] | None = None,
) -> CouponView:
    """Send on a coupon that is waiting for a destination.

    Raises `payouts.PayoutRefused` if payouts will not pay it there now: it keeps waiting.
    `confirm` is awaited once the coupon and destination pass their checks, just before the
    payout is made. The route passes the app PIN check, which raises to refuse.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    row = (await session.execute(_BY_ID_LOCKED, {"id": coupon_id})).one_or_none()
    if row is None or row.client_id != client.id:
        raise ApplicationError("not_found")
    view = _view(row)
    if view.status != CouponStatus.HELD:
        raise ApplicationError("conflict")
    await usable(session, view.client_id, destination_id)
    if confirm is not None:
        await confirm()
    payout = await payouts.create_payout(
        session,
        client,
        client_id=view.client_id,
        destination_id=destination_id,
        amount=view.net,
        source_reference=view.id,
        request_key=f"bond-coupon:{view.id}:{destination_id}",
        clock=clock,
    )
    await session.execute(_SEND_ON, {"id": view.id, "payout_id": payout.id, "now": clock.now()})
    return await get_coupon(session, view.id)


# Reads ------------------------------------------------------------------------------------------


async def get_coupon(session: AsyncSession, coupon_id: str) -> CouponView:
    row = (await session.execute(_BY_ID, {"id": coupon_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def client_coupons(session: AsyncSession, client_id: str) -> list[CouponView]:
    """The client's coupons, newest first."""
    rows = await session.execute(_FOR_CLIENT, {"client_id": client_id, "limit": CLIENT_PAGE})
    return [_view(row) for row in rows.all()]


async def staff_coupons(
    session: AsyncSession, staff: Actor, status: str | None, limit: int
) -> list[CouponView]:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(_FOR_STAFF, {"status": status, "limit": limit})
    return [_view(row) for row in rows.all()]


# Internals --------------------------------------------------------------------------------------


async def _own(session: AsyncSession, client: Actor, holding_id: str) -> holdings.Holding:
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    holding = await holdings.get_holding(session, holding_id)
    if holding.client_id != client.id:
        raise ApplicationError("not_found")
    return holding


async def usable(session: AsyncSession, client_id: str, destination_id: str) -> None:
    try:
        await destinations.usable_destination(session, client_id, destination_id)
    except ApplicationError as error:
        if error.code != "not_found":
            raise
        raise invalid("destination_id", "not_usable") from error


def _issue(row: Row[Any]) -> BondIssue:
    currency = Currency(row.currency)
    return BondIssue(
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
        status=BondStatus(row.status),
    )


def _view(row: Row[Any]) -> CouponView:
    currency = Currency(row.currency)
    return CouponView(
        id=row.id,
        holding_id=row.holding_id,
        client_id=row.client_id,
        code=row.code,
        name=row.name,
        coupon_date=row.coupon_date,
        gross=Money(row.gross_minor, currency),
        tax=Money(row.tax_minor, currency),
        net=Money(row.net_minor, currency),
        status=row.status,
        payout_id=row.payout_id,
        payout_refusal=row.payout_refusal,
        created_at=row.created_at,
    )
