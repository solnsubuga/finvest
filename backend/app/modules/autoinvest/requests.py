"""The worker's side of a run once it is due: ask for the investment and the payment, then settle.

Every function works inside the worker's transaction, as the system acting for the client, and
calls no provider. Asking saves the investment awaiting payment (investments module) and a
mobile money deposit with its send job (payments module) in one transaction; the payments
worker then prompts the client's phone, and the client approves with their mobile money PIN as
for any payment. Settling reads what became of that deposit and investment, which those modules
own, and records it on the run.

    due ──request_run──► requested ──settle_runs──► paid
     │                       │
     │                       └──────────────────► failed (retried once, the next day)
     └──► failed, when the investment or the payment could not even be asked for

A run is locked while it is asked for and while it is settled, so two workers never both act.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.autoinvest.domain.plans import RunStatus, after_failure, may_prompt_at
from app.modules.identity import service as identity
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.notifications import contract as notifications
from app.modules.payments import service as payments
from app.modules.payments.domain.deposits import (
    DepositStatus,
    InvalidDeposit,
    Network,
    Purpose,
    normalise_msisdn,
)
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money

SOURCE_TYPE = "autoinvest_run"
SETTLE_BATCH = 200
# A run still due this long after it was opened lost its request job. It is missed, never sent
# late: the client was expecting the prompt on its day.
STRANDED_AFTER = timedelta(days=1)
logger = structlog.get_logger()

# Notifications. The words name the due day, never the amount: see docs/notifications.md.
RUN_MISSED = "autoinvest_run_missed"
RUN_RETRYING = "autoinvest_run_retrying"
RUN_FAILED = "autoinvest_run_failed"
# Missed for these reasons, the client is told. A plan the client paused or stopped, or an
# account they asked to close, needs no news.
TELL_WHEN_MISSED = frozenset({"late", "previous_run_open"})

_RUN_FOR_REQUEST = text(
    "SELECT r.id, r.plan_id, r.client_id, r.due_on, r.status, r.attempt, r.investment_id, "
    "p.status AS plan_status, p.product_code, p.currency, p.amount_minor, p.network, "
    "p.payer_msisdn, p.at_maturity "
    "FROM autoinvest_runs r JOIN autoinvest_plans p ON p.id = r.plan_id "
    "WHERE r.id = :id FOR UPDATE OF r"
)
_REQUESTED = text(
    "UPDATE autoinvest_runs SET status = 'requested', investment_id = :investment_id, "
    "deposit_id = :deposit_id, code = NULL, updated_at = :now WHERE id = :id"
)
_FAIL = text(
    "UPDATE autoinvest_runs SET status = 'failed', investment_id = "
    "coalesce(investment_id, :investment_id), retry_on = :retry_on, code = :code, "
    "updated_at = :now WHERE id = :id"
)
_MISS = text(
    "UPDATE autoinvest_runs SET status = 'missed', code = :code, updated_at = :now WHERE id = :id"
)
_PAID = text(
    "UPDATE autoinvest_runs SET status = 'paid', code = NULL, updated_at = :now WHERE id = :id"
)
_TO_SETTLE = text(
    "SELECT id, plan_id, client_id, due_on, attempt, investment_id, deposit_id "
    "FROM autoinvest_runs WHERE status = 'requested' ORDER BY updated_at, id "
    "LIMIT :limit FOR UPDATE SKIP LOCKED"
)
_STRANDED = text(
    "SELECT id, plan_id, client_id, due_on FROM autoinvest_runs "
    "WHERE status = 'due' AND updated_at <= :cutoff ORDER BY updated_at, id "
    "LIMIT :limit FOR UPDATE SKIP LOCKED"
)


@dataclass(frozen=True, slots=True)
class Settled:
    """What one pass of the settling sweep did."""

    paid: int
    failed: int
    missed: int


async def request_run(session: AsyncSession, run_id: str, *, provider: str, clock: Clock) -> str:
    """Ask for the investment and the mobile money payment of one due run. Worker only.

    Returns what became of the run, as a code for the job's result. A run that is no longer due
    is left alone, so a repeated job does nothing. Outside the prompt hours nothing is sent and
    the run waits; the settling sweep records it as missed if it is still waiting a day later.
    """
    row = (await session.execute(_RUN_FOR_REQUEST, {"id": run_id})).one_or_none()
    if row is None or row.status != RunStatus.DUE.value:
        return "not_due"
    now = clock.now()
    if not may_prompt_at(now):
        return "outside_hours"
    if row.plan_status != "active":
        await _miss(session, row.id, "plan_not_active", now)
        return "missed"
    contact = await identity.client_contact(session, row.client_id)
    if contact is None or not contact.claimed:
        # Locked, closed, or never claimed from Finvest 1.0: nobody to prompt.
        await _fail(session, row, None, "client_not_active", clock, retry=False)
        return "failed"
    try:
        same_phone = normalise_msisdn(contact.phone) == row.payer_msisdn
    except InvalidDeposit:
        same_phone = False
    if not same_phone:
        # The number on the account changed since the plan was set up. The plan prompts only
        # the account's own number, so it must be set up again with the new one.
        await _fail(session, row, None, "phone_changed", clock, retry=False)
        return "failed"
    amount = Money(int(row.amount_minor), Currency(row.currency))
    try:
        async with session.begin_nested():
            investment = await investments.request_system_investment(
                session,
                client_id=row.client_id,
                source_type=SOURCE_TYPE,
                source_id=row.id,
                product_code=row.product_code,
                amount=amount,
                at_maturity=AtMaturity(row.at_maturity) if row.at_maturity else None,
                clock=clock,
            )
    except ApplicationError as error:
        await _fail(session, row, None, f"investment_{error.code}", clock)
        return "failed"
    if not _payable(investment, now):
        # A retry finds the investment of its first attempt. It may have been cancelled, or
        # its payment window closed, in between.
        await _fail(session, row, investment.id, "investment_closed", clock, retry=False)
        return "failed"
    try:
        async with session.begin_nested():
            deposit = await payments.request_system_deposit(
                session,
                payments.DepositRequest(
                    purpose=Purpose.INVESTMENT,
                    purpose_id=investment.id,
                    network=Network(row.network),
                    amount=amount,
                    phone=row.payer_msisdn,
                ),
                client_id=row.client_id,
                account_phone=contact.phone,
                provider=provider,
                clock=clock,
            )
        deposit_id = deposit.id
    except payments.DepositExists as exists:
        # Already under way or paid, for example from a request whose job was repeated after
        # its transaction committed. Follow that one rather than prompt again.
        if exists.deposit_id is None:
            raise
        deposit_id = exists.deposit_id
    except ApplicationError as error:
        await _fail(session, row, investment.id, f"payment_{error.code}", clock)
        return "failed"
    await session.execute(
        _REQUESTED,
        {"id": row.id, "investment_id": investment.id, "deposit_id": deposit_id, "now": now},
    )
    logger.info("autoinvest_run_requested", run_id=row.id)
    return "requested"


async def settle_runs(session: AsyncSession, clock: Clock, *, limit: int = SETTLE_BATCH) -> Settled:
    """Record what became of requested runs, and give up on runs whose request was lost.

    Worker only. A run is `paid` once its deposit is confirmed and its investment has started,
    and `failed` once its deposit failed: a first failure is tried again the next day. A run
    whose deposit is still open or uncertain waits, so its plan's next day is missed rather
    than prompting twice. Reconciliation settles an uncertain deposit, never this sweep.
    """
    now = clock.now()
    today = business_date(now)
    paid = failed = missed = 0
    for run in (await session.execute(_TO_SETTLE, {"limit": limit})).all():
        deposit = await payments.find_deposit(session, run.deposit_id) if run.deposit_id else None
        if deposit is None:  # pragma: no cover - a requested run always names its deposit
            continue
        if deposit.status is DepositStatus.FAILED:
            await _fail(session, run, None, deposit.code or "payment_failed", clock)
            failed += 1
            continue
        if deposit.status is not DepositStatus.CONFIRMED:
            continue
        investment = await investments.get_investment(session, run.investment_id)
        if investment.status in (InvestmentStatus.AWAITING_PAYMENT, InvestmentStatus.CANCELLED):
            # Confirmed, and the investments module has not started it yet. If it never can,
            # the money waits for staff in the client's awaiting-start account.
            continue
        await session.execute(_PAID, {"id": run.id, "now": now})
        paid += 1
    cutoff = now - STRANDED_AFTER
    for run in (await session.execute(_STRANDED, {"cutoff": cutoff, "limit": limit})).all():
        await _miss(session, run.id, "late", now)
        await _tell(session, RUN_MISSED, run, now)
        missed += 1
    if paid or failed or missed:
        logger.info(
            "autoinvest_runs_settled", paid=paid, failed=failed, missed=missed, day=str(today)
        )
    return Settled(paid=paid, failed=failed, missed=missed)


async def tell_missed(
    session: AsyncSession,
    *,
    run_id: str,
    plan_id: str,
    client_id: str,
    due_on: date,
    code: str,
    now: datetime,
) -> None:
    """Tell the client a run was missed, if its reason is one they would want to hear."""
    if code in TELL_WHEN_MISSED:
        await notifications.notify(
            session,
            event_name=RUN_MISSED,
            client_id=client_id,
            idempotency_key=run_id,
            variables={"date": due_on},
            link=notifications.Link("autoinvest_plan", plan_id),
            now=now,
        )


async def _fail(
    session: AsyncSession,
    run: Row[Any],
    investment_id: str | None,
    code: str,
    clock: Clock,
    *,
    retry: bool = True,
) -> None:
    now = clock.now()
    retry_on = after_failure(run.attempt, business_date(now)) if retry else None
    await session.execute(
        _FAIL,
        {
            "id": run.id,
            "investment_id": investment_id,
            "retry_on": retry_on,
            "code": _code(code),
            "now": now,
        },
    )
    await _tell(session, RUN_RETRYING if retry_on else RUN_FAILED, run, now, attempt=run.attempt)
    logger.info(
        "autoinvest_run_failed", run_id=run.id, error_code=_code(code), retry=bool(retry_on)
    )


async def _miss(session: AsyncSession, run_id: str, code: str, now: datetime) -> None:
    await session.execute(_MISS, {"id": run_id, "code": code, "now": now})


async def _tell(
    session: AsyncSession, event_name: str, run: Row[Any], now: datetime, *, attempt: int = 1
) -> None:
    await notifications.notify(
        session,
        event_name=event_name,
        client_id=run.client_id,
        # One of each per attempt of a run, so a repeated job tells nobody twice.
        idempotency_key=f"{run.id}:{attempt}",
        variables={"date": run.due_on},
        link=notifications.Link("autoinvest_plan", run.plan_id),
        now=now,
    )


def _payable(investment: investments.Investment, now: datetime) -> bool:
    return investment.status is InvestmentStatus.AWAITING_PAYMENT and investment.pay_by > now


def _code(code: str) -> str:
    """An error code the runs table takes: lower case letters, digits, `_` and `.`."""
    cleaned = "".join(c if c.isalnum() or c in "_." else "_" for c in code.lower())
    return cleaned[:64] or "failed"
