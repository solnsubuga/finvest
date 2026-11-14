"""What staff read about automatic investing, for the console. Nothing here changes anything:
a run asks a client for money, so staff never start, retry or change one.

The app role's row policies let staff read every plan and run (migration 0120); which staff
may is the `automatic_investing.view` permission, checked by the route.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.autoinvest.domain.plans import Frequency, RunStatus
from app.modules.autoinvest.plans import MAX_PAGE_SIZE, Run, run_from_row
from app.modules.payments.domain.deposits import Network
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class PlanCounts:
    active: int
    paused: int


@dataclass(frozen=True, slots=True)
class StaffRun:
    """A run with the plan terms it was opened under. The plan may have changed since: the
    terms shown are the plan's now, as a run keeps only its own outcome."""

    run: Run
    product_code: str
    amount: Money
    frequency: Frequency
    day: int
    network: Network


_COUNTS = text(
    "SELECT count(*) FILTER (WHERE status = 'active') AS active, "
    "count(*) FILTER (WHERE status = 'paused') AS paused FROM autoinvest_plans "
    "WHERE CAST(:product_code AS text) IS NULL OR product_code = :product_code"
)
_RUNS = text(
    "SELECT r.id, r.plan_id, r.client_id, r.due_on, r.status, r.attempt, r.retry_on, "
    "r.investment_id, r.deposit_id, r.code, r.created_at, r.updated_at, "
    "p.product_code, p.currency, p.amount_minor, p.frequency, p.run_day, p.network "
    "FROM autoinvest_runs r JOIN autoinvest_plans p ON p.id = r.plan_id "
    "WHERE (CAST(:status AS text) IS NULL OR r.status = :status) "
    "AND r.due_on BETWEEN :date_from AND :date_to "
    "AND (CAST(:before_id AS text) IS NULL OR r.id < :before_id) "
    "ORDER BY r.id DESC LIMIT :limit"
)


def _staff_only(staff: Actor) -> None:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")


async def plan_counts(
    session: AsyncSession, staff: Actor, *, product_code: str | None = None
) -> PlanCounts:
    """How many plans are running and paused, into one product if named. Stopped plans are not
    counted."""
    _staff_only(staff)
    row = (await session.execute(_COUNTS, {"product_code": product_code})).one()
    return PlanCounts(active=row.active, paused=row.paused)


async def staff_runs(
    session: AsyncSession,
    staff: Actor,
    *,
    date_from: date,
    date_to: date,
    status: RunStatus | None = None,
    before_id: str | None = None,
    limit: int = MAX_PAGE_SIZE,
) -> list[StaffRun]:
    """Runs due from one day to another, newest first, each with its plan's terms."""
    _staff_only(staff)
    if date_to < date_from:
        raise ApplicationError("validation_failed")
    rows = await session.execute(
        _RUNS,
        {
            "status": status.value if status else None,
            "date_from": date_from,
            "date_to": date_to,
            "before_id": before_id,
            "limit": max(1, min(limit, MAX_PAGE_SIZE)),
        },
    )
    return [
        StaffRun(
            run=run_from_row(row),
            product_code=row.product_code,
            amount=Money(row.amount_minor, Currency(row.currency)),
            frequency=Frequency(row.frequency),
            day=row.run_day,
            network=Network(row.network),
        )
        for row in rows
    ]
