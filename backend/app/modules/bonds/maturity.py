"""Bond maturity: the run that repays each holding when its issue matures.

On or after an issue's maturity date the worker pays each active holding of it, once:

* first, any coupon before maturity the holding is owed and was not paid, so a coupon run that
  never happened is not lost when the holding closes.
* one ledger entry, `bond.matured`: the holding's cost out of the client's bond holding, the
  last coupon before tax from `interest.expense` with its tax to `tax.withholding_payable`, and
  the face value and the coupon after tax handed to payouts in the client's "payout pending"
  account for this repayment. The accrued interest the client paid at purchase goes to
  `bond.accrued_interest_bought`, and the face value less the clean amount to
  `bond.bought_below_par` (or the other way round to `bond.bought_above_par`), so only coupons
  are interest. The entry's source is the holding, so the tax module reads its coupon.
* a payout to the destination the client chose for the holding's coupons. With none, or one the
  payout module refuses, the money is held until the client names a destination for it.

The holding is then `matured`. The rules are in `domain/maturity.py`. Migration 0494 lets only
the worker save a repayment, for an active holding of a matured issue, and lets a held one be
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
from app.modules.bonds import coupons, holdings
from app.modules.bonds.domain.coupons import due_dates, is_owed
from app.modules.bonds.domain.holdings import HoldingStatus
from app.modules.bonds.domain.maturity import has_matured, redemption
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payouts import payouts
from app.modules.payouts.domain import posting as payout_posting
from app.modules.payouts.domain.payouts import PayoutPurpose
from app.modules.tax.domain.withholding import INTEREST_EXPENSE, TAX_PAYABLE
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

BATCH = 200
CLIENT_PAGE = 100
# The parts of what a client paid that are not repaid as face value or coupons. See
# `domain/maturity.py`. Not interest, so never in `interest.expense`.
ACCRUED_BOUGHT = "bond.accrued_interest_bought"
BELOW_PAR = "bond.bought_below_par"
ABOVE_PAR = "bond.bought_above_par"
logger = structlog.get_logger()


class RedemptionStatus:
    PAID_OUT = "paid_out"
    HELD = "held"


@dataclass(frozen=True, slots=True)
class RedemptionView:
    id: str
    holding_id: str
    client_id: str
    code: str
    name: str
    redeemed_on: date
    face: Money
    cost: Money
    coupon_gross: Money
    coupon_tax: Money
    coupon_net: Money
    # What the client is paid: the face value and the last coupon after tax.
    amount: Money
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


_DUE = text(
    "SELECT h.id FROM bond_holdings h JOIN bond_issues b ON b.id = h.issue_id "  # noqa: S608 - constant SQL
    "WHERE h.status = 'active' AND b.maturity_date <= :run_date AND h.id > :after "
    f"AND {coupons.SALE_APPROVED} ORDER BY h.id LIMIT :limit"
)
_SALE_WAITING = text(
    "SELECT 1 FROM bond_sales WHERE holding_id = :holding_id AND status = 'approved'"
)
# A sale asked for, or priced, is closed: the bond matured first and is repaid instead.
_CLOSE_SALES = text(
    "UPDATE bond_sales SET status = 'closed', closed_at = :now, updated_at = :now "
    "WHERE holding_id = :holding_id AND status IN ('requested', 'priced')"
)
_LOCK_HOLDING = text("SELECT id FROM bond_holdings WHERE id = :id FOR UPDATE")
_PAID_DATES = text("SELECT coupon_date FROM bond_coupons WHERE holding_id = :holding_id")
_INSERT = text(
    "INSERT INTO bond_redemptions (id, holding_id, client_id, redeemed_on, currency, face_minor, "
    "cost_minor, coupon_gross_minor, coupon_tax_minor, coupon_net_minor, amount_minor, entry_id, "
    "status, payout_id, payout_refusal, created_at, updated_at) VALUES (:id, :holding_id, "
    ":client_id, :redeemed_on, :currency, :face, :cost, :gross, :tax, :net, :amount, :entry_id, "
    ":status, :payout_id, :refusal, :now, :now)"
)
_MATURE = text(
    "UPDATE bond_holdings SET status = 'matured', updated_at = :now WHERE id = :id "
    "AND status = 'active'"
)
_COLUMNS = (
    "r.id, r.holding_id, r.client_id, r.redeemed_on, r.currency, r.face_minor, r.cost_minor, "
    "r.coupon_gross_minor, r.coupon_tax_minor, r.coupon_net_minor, r.amount_minor, r.status, "
    "r.payout_id, r.payout_refusal, r.created_at, b.code, b.name"
)
_FROM = (
    "FROM bond_redemptions r JOIN bond_holdings h ON h.id = r.holding_id "
    "JOIN bond_issues b ON b.id = h.issue_id"
)
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} {_FROM} WHERE r.id = :id FOR UPDATE OF r")
_BY_ID = text(f"SELECT {_COLUMNS} {_FROM} WHERE r.id = :id")
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE r.client_id = :client_id "
    "ORDER BY r.redeemed_on DESC, r.id DESC LIMIT :limit"
)
_FOR_STAFF = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE (CAST(:status AS text) IS NULL OR r.status = :status) "
    "ORDER BY r.redeemed_on DESC, r.id DESC LIMIT :limit"
)
_SEND_ON = text(
    "UPDATE bond_redemptions SET status = 'paid_out', payout_id = :payout_id, "
    "payout_refusal = NULL, updated_at = :now WHERE id = :id"
)


# The worker -------------------------------------------------------------------------------------


async def repay_batch(
    session: AsyncSession,
    run_date: date,
    clock: Clock,
    *,
    after: str = "",
    limit: int = BATCH,
) -> Batch:
    """Repay the next active holdings, in ID order after `after`, of issues that matured on or
    before `run_date`. Worker only.

    Each holding is repaid inside its own savepoint: one that cannot be is left active, logged,
    and is repaid by the next run or retry.
    """
    if run_date > business_date(clock.now()):
        # A bond is never repaid before it matures.
        raise ApplicationError("conflict")
    ids = [
        row.id
        for row in (
            await session.execute(_DUE, {"run_date": run_date, "after": after, "limit": limit})
        ).all()
    ]
    paid = held = failed = 0
    for holding_id in ids:
        try:
            async with session.begin_nested():
                outcome = await repay(session, holding_id, run_date, clock)
        except Exception as error:
            failed += 1
            logger.error("bond_not_repaid", holding_id=holding_id, error_type=type(error).__name__)
            continue
        if outcome == RedemptionStatus.PAID_OUT:
            paid += 1
        elif outcome == RedemptionStatus.HELD:
            held += 1
    return Batch(last_id=ids[-1] if ids else None, paid=paid, held=held, failed=failed)


async def repay(session: AsyncSession, holding_id: str, run_date: date, clock: Clock) -> str | None:
    """Repay one holding at maturity. Returns how it ended, or None when it was already repaid
    or its issue has not matured by `run_date`. Worker only."""
    await session.execute(_LOCK_HOLDING, {"id": holding_id})
    holding = await holdings.get_holding(session, holding_id)
    issue = holding.issue
    if holding.status is not HoldingStatus.ACTIVE or not has_matured(issue, run_date):
        return None
    if (await session.execute(_SALE_WAITING, {"holding_id": holding.id})).first() is not None:
        # Approved for sale before it matured: the sale pays it.
        return None
    # A coupon before maturity that a run missed is paid first, in its own entry.
    paid_dates = set(
        (await session.execute(_PAID_DATES, {"holding_id": holding.id})).scalars().all()
    )
    for coupon_date in due_dates(issue, run_date):
        if is_owed(holding.start_date, coupon_date) and coupon_date not in paid_dates:
            await coupons.pay_coupon(session, holding.id, coupon_date, clock)

    currency = holding.face.currency
    held_account = await ledger.open_account(session, holdings.holding_account(holding), clock)
    if await ledger.get_balance(session, held_account.id) != holding.cost:
        # Only buying puts money in a bond holding, and only this takes it out.
        logger.error("bond_holding_balance_unexpected", holding_id=holding.id)
        raise ApplicationError("conflict")
    figures = redemption(issue, holding.face, holding.cost, holding.accrued)
    now = clock.now()
    redemption_id = push_id()
    expense = await ledger.open_account(
        session,
        AccountSpec(INTEREST_EXPENSE, AccountKind.EXPENSE, currency, "Interest paid to clients"),
        clock,
    )
    handover = await ledger.open_account(
        session, payout_posting.funding_account(holding.client_id, redemption_id, currency), clock
    )
    lines = [
        debit(held_account.id, holding.cost),
        debit(expense.id, figures.coupon.gross),
        credit(handover.id, figures.paid),
    ]
    if figures.coupon.tax.is_positive:
        payable = await ledger.open_account(
            session,
            AccountSpec(TAX_PAYABLE, AccountKind.LIABILITY, currency, "Tax withheld from interest"),
            clock,
        )
        lines.append(credit(payable.id, figures.coupon.tax))
    for code, kind, name, amount, line in (
        (
            ACCRUED_BOUGHT,
            AccountKind.INCOME,
            "Accrued interest clients bought",
            figures.accrued,
            credit,
        ),
        (BELOW_PAR, AccountKind.EXPENSE, "Bonds bought below par", figures.below_par, debit),
        (ABOVE_PAR, AccountKind.INCOME, "Bonds bought above par", figures.above_par, credit),
    ):
        if amount.is_positive:
            account = await ledger.open_account(
                session, AccountSpec(code, kind, currency, name), clock
            )
            lines.append(line(account.id, amount))
    today = business_date(now)
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="bond.matured",
            effective_date=today,
            lines=tuple(lines),
            idempotency_key=f"bond-matured:{holding.id}",
            source_type=coupons.SOURCE_TYPE,
            source_id=holding.id,
        ),
        clock,
    )
    payout_id, refusal = await coupons.pay_to_destination(
        session, SYSTEM, holding, redemption_id, figures.paid, clock, kind="bond-matured"
    )
    status = RedemptionStatus.HELD if payout_id is None else RedemptionStatus.PAID_OUT
    await session.execute(
        _INSERT,
        {
            "id": redemption_id,
            "holding_id": holding.id,
            "client_id": holding.client_id,
            "redeemed_on": today,
            "currency": currency.value,
            "face": holding.face.minor,
            "cost": holding.cost.minor,
            "gross": figures.coupon.gross.minor,
            "tax": figures.coupon.tax.minor,
            "net": figures.coupon.net.minor,
            "amount": figures.paid.minor,
            "entry_id": entry.id,
            "status": status,
            "payout_id": payout_id,
            "refusal": refusal,
            "now": now,
        },
    )
    await session.execute(_MATURE, {"id": holding.id, "now": now})
    await session.execute(_CLOSE_SALES, {"holding_id": holding.id, "now": now})
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="bonds.holding_matured",
            resource_type=holdings.HOLDING_RESOURCE,
            resource_id=holding.id,
            actor=AuditActor.system(),
            details={"bond": holding.code, "paid_out": payout_id is not None},
        ),
        clock,
    )
    logger.info(
        "bond_holding_matured",
        holding_id=holding.id,
        paid_out=payout_id is not None,
        error_code=refusal,
    )
    return status


# The client's command ---------------------------------------------------------------------------


async def pay_out_held(
    session: AsyncSession,
    client: Actor,
    redemption_id: str,
    *,
    destination_id: str,
    clock: Clock,
    confirm: Callable[[], Awaitable[None]] | None = None,
) -> RedemptionView:
    """Send on a repayment that is waiting for a destination.

    Raises `payouts.PayoutRefused` if payouts will not pay it there now: it keeps waiting.
    `confirm` is awaited once the repayment and destination pass their checks, just before the
    payout is made. The route passes the app PIN check, which raises to refuse.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    row = (await session.execute(_BY_ID_LOCKED, {"id": redemption_id})).one_or_none()
    if row is None or row.client_id != client.id:
        raise ApplicationError("not_found")
    view = _view(row)
    if view.status != RedemptionStatus.HELD:
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
        request_key=f"bond-matured:{view.id}:{destination_id}",
        clock=clock,
        purpose=PayoutPurpose.BOND_REPAYMENT,
    )
    await session.execute(_SEND_ON, {"id": view.id, "payout_id": payout.id, "now": clock.now()})
    return await get_redemption(session, view.id)


# Reads ------------------------------------------------------------------------------------------


async def get_redemption(session: AsyncSession, redemption_id: str) -> RedemptionView:
    row = (await session.execute(_BY_ID, {"id": redemption_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def client_redemptions(session: AsyncSession, client_id: str) -> list[RedemptionView]:
    """The client's repayments at maturity, newest first."""
    rows = await session.execute(_FOR_CLIENT, {"client_id": client_id, "limit": CLIENT_PAGE})
    return [_view(row) for row in rows.all()]


async def staff_redemptions(
    session: AsyncSession, staff: Actor, status: str | None, limit: int
) -> list[RedemptionView]:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(_FOR_STAFF, {"status": status, "limit": limit})
    return [_view(row) for row in rows.all()]


def _view(row: Row[Any]) -> RedemptionView:
    currency = Currency(row.currency)
    return RedemptionView(
        id=row.id,
        holding_id=row.holding_id,
        client_id=row.client_id,
        code=row.code,
        name=row.name,
        redeemed_on=row.redeemed_on,
        face=Money(row.face_minor, currency),
        cost=Money(row.cost_minor, currency),
        coupon_gross=Money(row.coupon_gross_minor, currency),
        coupon_tax=Money(row.coupon_tax_minor, currency),
        coupon_net=Money(row.coupon_net_minor, currency),
        amount=Money(row.amount_minor, currency),
        status=row.status,
        payout_id=row.payout_id,
        payout_refusal=row.payout_refusal,
        created_at=row.created_at,
    )
