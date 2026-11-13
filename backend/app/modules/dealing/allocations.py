"""Allocating the bills held to client investments. Run by the worker, in its transaction.

Each run brings the allocations of every product up to date: it releases what no longer
stands behind a running investment, or what a lot no longer holds, and then shares out the
cost left in each lot, oldest purchase first, to the investments waiting, oldest payment
first. A run repeated, or two at once, ends with the same allocations. The trigger of migration
0085 holds that a lot never has more allocated than it holds.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealing import holdings
from app.modules.dealing.domain.allocation import Held, Waiting, allocate, to_release
from app.modules.investments import service as investments
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

_PRODUCTS = text("SELECT DISTINCT product_code FROM dealing_lots ORDER BY product_code")
_IN_FORCE = text(
    "SELECT a.id, a.investment_id, a.lot_id, a.cost_minor, a.allocated_at, lot.currency "
    "FROM dealing_allocations AS a JOIN dealing_lots AS lot ON lot.id = a.lot_id "
    "WHERE lot.product_code = :product_code AND a.released_at IS NULL "
    "ORDER BY a.allocated_at, a.id"
)
_FOR_INVESTMENT = text(
    "SELECT a.id, a.investment_id, a.lot_id, a.cost_minor, a.allocated_at, lot.currency, "
    "lot.product_code, lot.matures_on, lot.custodian_reference "
    "FROM dealing_allocations AS a JOIN dealing_lots AS lot ON lot.id = a.lot_id "
    "WHERE a.investment_id = :investment_id AND a.released_at IS NULL "
    "ORDER BY a.allocated_at, a.id"
)
_INSERT = text(
    "INSERT INTO dealing_allocations (id, investment_id, lot_id, cost_minor, allocated_at) "
    "VALUES (:id, :investment_id, :lot_id, :cost, :now)"
)
_RELEASE = text(
    "UPDATE dealing_allocations SET released_at = :now, release_reason = :reason "
    "WHERE id = :id AND released_at IS NULL"
)
# One run per product at a time.
_LOCK_PRODUCT = text(
    "SELECT pg_advisory_xact_lock(hashtext('dealing_allocations'), hashtext(:product_code))"
)


@dataclass(frozen=True, slots=True)
class Run:
    allocated: int
    released: int


@dataclass(frozen=True, slots=True)
class InvestmentBills:
    """Some of one lot's cost standing behind an investment, for the allocation report."""

    lot_id: str
    product_code: str
    matures_on: date
    custodian_reference: str
    cost: Money


@dataclass(frozen=True, slots=True)
class _InForce:
    id: str
    investment_id: str
    lot_id: str
    cost: Money
    allocated_at: datetime


async def bills_behind(session: AsyncSession, investment_id: str) -> list[InvestmentBills]:
    """The bills standing behind one investment now. For staff and the worker."""
    rows = await session.execute(_FOR_INVESTMENT, {"investment_id": investment_id})
    return [
        InvestmentBills(
            lot_id=row.lot_id,
            product_code=row.product_code,
            matures_on=row.matures_on,
            custodian_reference=row.custodian_reference,
            cost=Money(row.cost_minor, Currency(row.currency)),
        )
        for row in rows.all()
    ]


async def allocate_all(session: AsyncSession, clock: Clock) -> Run:
    """Bring the allocations of every product up to date."""
    allocated = released = 0
    for row in (await session.execute(_PRODUCTS)).all():
        run = await allocate_product(session, row.product_code, clock)
        allocated += run.allocated
        released += run.released
    return Run(allocated, released)


async def allocate_product(session: AsyncSession, product_code: str, clock: Clock) -> Run:
    await session.execute(_LOCK_PRODUCT, {"product_code": product_code})
    now = clock.now()
    running = {
        investment.investment_id: investment
        for investment in await investments.running_investments(session, product_code)
    }
    lots = await holdings.allocatable_lots(session, product_code, on=business_date(now))
    in_force = [
        _in_force(row) for row in await session.execute(_IN_FORCE, {"product_code": product_code})
    ]
    release: dict[str, str] = {}

    # What no longer stands behind a running investment.
    for item in in_force:
        if item.investment_id not in running:
            release[item.id] = "investment_ended"
    # What an investment is no longer owed, newest first.
    by_investment: defaultdict[str, list[_InForce]] = defaultdict(list)
    for item in in_force:
        if item.id not in release:
            by_investment[item.investment_id].append(item)
    for investment_id, items in by_investment.items():
        for allocation_id in to_release(_held(items), running[investment_id].principal):
            release[allocation_id] = "investment_reduced"
    # What a lot no longer holds, newest first.
    cost_left = {held.id: held.lot.cost for held in lots}
    by_lot: defaultdict[str, list[_InForce]] = defaultdict(list)
    for item in in_force:
        if item.id not in release:
            by_lot[item.lot_id].append(item)
    for lot_id, items in by_lot.items():
        limit = cost_left.get(lot_id, Money.zero(items[0].cost.currency))
        for allocation_id in to_release(_held(items), limit):
            release[allocation_id] = "lot_reduced"
    for allocation_id, reason in release.items():
        await session.execute(_RELEASE, {"id": allocation_id, "now": now, "reason": reason})

    # Share out what each lot has left, oldest purchase first.
    kept = [item for item in in_force if item.id not in release]
    covered: defaultdict[str, int] = defaultdict(int)
    used: defaultdict[str, int] = defaultdict(int)
    for item in kept:
        covered[item.investment_id] += item.cost.minor
        used[item.lot_id] += item.cost.minor
    owed: dict[str, Money] = {}
    for investment_id, investment in running.items():
        gap = investment.principal.minor - covered[investment_id]
        if gap > 0:
            owed[investment_id] = Money(gap, investment.principal.currency)
    allocated = 0
    for held in lots:
        available = held.lot.cost.minor - used[held.id]
        if available <= 0 or not owed:
            continue
        waiting = [
            Waiting(investment_id, _started(running[investment_id].start_date), amount)
            for investment_id, amount in owed.items()
        ]
        result = allocate(Money(available, held.lot.cost.currency), waiting)
        for share in result.allocations:
            await session.execute(
                _INSERT,
                {
                    "id": push_id(),
                    "investment_id": share.investment_id,
                    "lot_id": held.id,
                    "cost": share.amount.minor,
                    "now": now,
                },
            )
            allocated += 1
            left = owed[share.investment_id] - share.amount
            if left.is_positive:
                owed[share.investment_id] = left
            else:
                del owed[share.investment_id]
    return Run(allocated, len(release))


def _held(items: list[_InForce]) -> list[Held]:
    return [Held(item.id, item.allocated_at, item.cost) for item in items]


def _started(day: date) -> datetime:
    """Investments start on a day, and the oldest payment is served first, then by ID."""
    return datetime.combine(day, time(0), tzinfo=UTC)


def _in_force(row: Row[Any]) -> _InForce:
    return _InForce(
        id=row.id,
        investment_id=row.investment_id,
        lot_id=row.lot_id,
        cost=Money(row.cost_minor, Currency(row.currency)),
        allocated_at=row.allocated_at,
    )
