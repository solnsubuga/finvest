"""The worker's side of a plan: open a run on each due day, and give a failed run its one retry.

Every function works inside the worker's transaction, as the system actor. Opening runs moves
no money and calls no provider, so a run opened twice by mistake is refused by the database:
one run per due day of a plan, and at most one open at a time.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.autoinvest.domain.plans import (
    LATE_TOLERANCE,
    Frequency,
    RunStatus,
    Schedule,
    due_run,
    may_prompt_at,
)
from app.modules.autoinvest.requests import tell_missed
from app.modules.closures.pending import closing
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.push_ids import push_id

BATCH = 500
logger = structlog.get_logger()

_DUE_PLANS = text(
    "SELECT id, client_id, frequency, run_day, next_run_on FROM autoinvest_plans "
    "WHERE status = 'active' AND next_run_on <= :today ORDER BY next_run_on, id "
    "LIMIT :limit FOR UPDATE SKIP LOCKED"
)
_HAS_OPEN = text(
    "SELECT EXISTS (SELECT FROM autoinvest_runs WHERE plan_id = :plan_id "
    "AND status IN ('due', 'requested'))"
)
_INSERT_RUN = text(
    "INSERT INTO autoinvest_runs (id, plan_id, client_id, due_on, status, code, created_at, "
    "updated_at) VALUES (:id, :plan_id, :client_id, :due_on, :status, :code, :now, :now) "
    "ON CONFLICT (plan_id, due_on) DO NOTHING"
)
_ADVANCE = text(
    "UPDATE autoinvest_plans SET next_run_on = :next_run_on, updated_at = :now WHERE id = :id"
)
_RETRIES = text(
    "SELECT r.id, r.plan_id, r.client_id, r.due_on, r.retry_on, p.status AS plan_status "
    "FROM autoinvest_runs r "
    "JOIN autoinvest_plans p ON p.id = r.plan_id "
    "WHERE r.status = 'failed' AND r.retry_on <= :today ORDER BY r.retry_on, r.id "
    "LIMIT :limit FOR UPDATE OF r SKIP LOCKED"
)
_RETRY = text(
    "UPDATE autoinvest_runs SET status = :status, attempt = :attempt, retry_on = NULL, "
    "code = :code, updated_at = :now WHERE id = :id"
)


@dataclass(frozen=True, slots=True)
class Opened:
    """What one pass of the daily run did."""

    # Runs ready to ask for their investment and payment: new ones and retries.
    due: tuple[str, ...]
    missed: int


async def open_due_runs(session: AsyncSession, clock: Clock, *, limit: int = BATCH) -> Opened:
    """Open today's runs and today's retries. Worker only.

    Outside the prompt hours this does nothing, so it is safe to call as often as the
    scheduler likes. A plan whose previous run is still open, waiting for a payment to settle,
    misses this due day rather than sending a second prompt.
    """
    now = clock.now()
    if not may_prompt_at(now):
        return Opened(due=(), missed=0)
    today = business_date(now)
    due: list[str] = []
    missed = 0
    for plan in (await session.execute(_DUE_PLANS, {"today": today, "limit": limit})).all():
        decision = due_run(
            Schedule(Frequency(plan.frequency), plan.run_day), plan.next_run_on, today
        )
        if decision is None:  # pragma: no cover - the query only returns due plans
            continue
        if decision.missed is not None:
            missed += await _insert(session, plan, decision.missed, RunStatus.MISSED, "late", now)
        if decision.collect is not None:
            if await closing(session, plan.client_id):
                # The client asked to close their account: nothing more is collected.
                missed += await _insert(
                    session, plan, decision.collect, RunStatus.MISSED, "closing_account", now
                )
            elif (await session.execute(_HAS_OPEN, {"plan_id": plan.id})).scalar_one():
                missed += await _insert(
                    session, plan, decision.collect, RunStatus.MISSED, "previous_run_open", now
                )
            else:
                run_id = push_id()
                if await _insert(session, plan, decision.collect, RunStatus.DUE, None, now, run_id):
                    due.append(run_id)
        await session.execute(
            _ADVANCE, {"id": plan.id, "next_run_on": decision.next_run_on, "now": now}
        )
    for run in (await session.execute(_RETRIES, {"today": today, "limit": limit})).all():
        code: str | None = None
        if run.plan_status != "active":
            code = "plan_not_active"
        elif today - run.retry_on > LATE_TOLERANCE:
            code = "late"
        elif await closing(session, run.client_id):
            code = "closing_account"
        elif (await session.execute(_HAS_OPEN, {"plan_id": run.plan_id})).scalar_one():
            code = "previous_run_open"
        await session.execute(
            _RETRY,
            {
                "id": run.id,
                "status": (RunStatus.MISSED if code else RunStatus.DUE).value,
                "attempt": 1 if code else 2,
                "code": code,
                "now": now,
            },
        )
        if code:
            missed += 1
            await tell_missed(
                session,
                run_id=run.id,
                plan_id=run.plan_id,
                client_id=run.client_id,
                due_on=run.due_on,
                code=code,
                now=now,
            )
        else:
            due.append(run.id)
    if due or missed:
        logger.info("autoinvest_runs_opened", due=len(due), missed=missed)
    return Opened(due=tuple(due), missed=missed)


async def _insert(
    session: AsyncSession,
    plan: Row[Any],
    due_on: date,
    status: RunStatus,
    code: str | None,
    now: datetime,
    run_id: str | None = None,
) -> int:
    run_id = run_id or push_id()
    result = await session.execute(
        _INSERT_RUN,
        {
            "id": run_id,
            "plan_id": plan.id,
            "client_id": plan.client_id,
            "due_on": due_on,
            "status": status.value,
            "code": code,
            "now": now,
        },
    )
    inserted = int(result.rowcount)  # type: ignore[attr-defined]
    if inserted and status is RunStatus.MISSED and code is not None:
        await tell_missed(
            session,
            run_id=run_id,
            plan_id=plan.id,
            client_id=plan.client_id,
            due_on=due_on,
            code=code,
            now=now,
        )
    return inserted
