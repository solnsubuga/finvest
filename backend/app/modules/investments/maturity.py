"""Maturity: what happens when a fixed term ends. Every function works inside the caller's
transaction.

On its maturity date an investment has earned its last day of interest. The maturity run then
closes it in one transaction, and the principal and the interest leave the holding together:

* **Rolled over.** A new investment in the same product starts for principal plus interest, at
  the rate in force that day. One ledger entry moves the money from the old holding to the new.
* **Paid out.** One ledger entry hands the money to the payouts module (the client's "payout
  pending" account for this investment), and a payout to the destination the client chose is
  created in the same transaction. This module never calls a provider.
* **Held.** If the client's choice cannot be carried out, or payouts refuses the payout, the
  money is handed over just the same and waits there. The reason is kept on the investment, and
  the client sends the money on with `pay_out_matured`. Nothing is ever rolled over or paid on a
  guess.

See docs/investing.md.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import publish
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.goals.events import FOLLOW_ROLLOVER
from app.modules.investments import accrual
from app.modules.investments.catalogue import invalid, product_from_row, product_row, rates_for
from app.modules.investments.domain.interest import maturity_date
from app.modules.investments.domain.investments import (
    AtMaturity,
    HoldReason,
    InvestmentStatus,
    MaturityStep,
    can_choose_maturity,
    is_due_to_mature,
    pay_by,
    plan_maturity,
)
from app.modules.investments.domain.products import ProductStatus
from app.modules.investments.domain.rates import rate_in_force
from app.modules.investments.holdings import (
    INVESTMENT_RESOURCE,
    Investment,
    get_investment,
    holding_account,
    investment_from_row,
    select_investments,
)
from app.modules.investments.locks import inherit_lock
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import EntryDraft, Line, credit, debit
from app.modules.notifications import contract as notifications
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain import posting as payout_posting
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Money
from app.shared.push_ids import push_id

MATURED_TOPIC = "investments.matured"
ROLLED_OVER_TOPIC = "investments.rolled_over"
BATCH = 100
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Batch:
    last_id: str | None
    matured: int
    failed: int


_DATABASE_NOW = text("SELECT clock_timestamp()")
_LOCKED = select_investments("WHERE i.id = :id FOR UPDATE OF i")
_DUE = select_investments(
    "WHERE i.status = 'active' AND i.withdrawal_requested_at IS NULL "
    "AND i.maturity_date <= :run_date AND i.id > :after "
    "ORDER BY i.id LIMIT :limit FOR UPDATE OF i SKIP LOCKED"
)
_CHOOSE = text(
    "UPDATE investments SET at_maturity = :at_maturity, payout_destination_id = :destination_id, "
    "updated_at = :now WHERE id = :id"
)
_INSERT_ROLLED = text(
    "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
    "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at, "
    "rolled_from_id) VALUES (:id, :client_id, :product_id, :currency, :principal_minor, "
    ":annual_rate_bps, :tax_bps, :term_days, 'roll_over', :created_at, :pay_by, :now, "
    ":rolled_from_id)"
)
_START_ROLLED = text(
    "UPDATE investments SET status = 'active', start_date = :start_date, "
    "maturity_date = :maturity_date, started_at = :now, start_entry_id = :entry_id, "
    "updated_at = :now WHERE id = :id"
)
_MATURE = text(
    "UPDATE investments SET status = 'matured', matured_at = :now, maturity_entry_id = :entry_id, "
    "rolled_into_id = :rolled_into_id, payout_id = :payout_id, payout_refusal = :refusal, "
    "updated_at = :now WHERE id = :id"
)
_SETTLE = text(
    "UPDATE investments SET payout_id = :payout_id, payout_refusal = NULL, updated_at = :now "
    "WHERE id = :id"
)


# The client's commands --------------------------------------------------------------------------


async def choose_at_maturity(
    session: AsyncSession,
    client: Actor,
    investment_id: str,
    *,
    at_maturity: AtMaturity,
    destination_id: str | None,
    clock: Clock,
) -> Investment:
    """Save what the client wants done at maturity. It can change up to the day before.

    To be paid out the client names one of their approved payout destinations. Rolling over
    takes none, and clears one chosen earlier.
    """
    investment = await _own_locked(session, client, investment_id)
    today = business_date(clock.now())
    if not can_choose_maturity(
        investment.status, investment.term_days, investment.maturity_date, today
    ):
        raise ApplicationError("conflict")
    if at_maturity is AtMaturity.PAY_OUT:
        if destination_id is None:
            raise invalid("destination_id", "required")
        await _usable(session, investment.client_id, destination_id)
    elif destination_id is not None:
        raise invalid("destination_id", "not_used_when_rolling_over")
    await session.execute(
        _CHOOSE,
        {
            "id": investment.id,
            "at_maturity": at_maturity.value,
            "destination_id": destination_id,
            "now": clock.now(),
        },
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="investments.maturity_chosen",
            resource_type=INVESTMENT_RESOURCE,
            resource_id=investment.id,
            actor=AuditActor.client(investment.client_id),
            details={"at_maturity": at_maturity.value, "destination_id": destination_id},
        ),
        clock,
    )
    return await get_investment(session, investment.id)


async def pay_out_matured(
    session: AsyncSession,
    client: Actor,
    investment_id: str,
    *,
    destination_id: str,
    clock: Clock,
    confirm: Callable[[], Awaitable[None]] | None = None,
) -> Investment:
    """Send on the money of a matured investment that is waiting for a destination.

    Raises `payouts.PayoutRefused` if payouts will not pay it there now. The money then keeps
    waiting, and the client can try again or choose another destination.

    `confirm` is awaited once the investment and destination pass their checks, just before the
    payout is made. The route passes the app PIN check, which raises to refuse.
    """
    investment = await _own_locked(session, client, investment_id)
    if (
        investment.status is not InvestmentStatus.MATURED
        or investment.rolled_into_id is not None
        or investment.payout_id is not None
    ):
        raise ApplicationError("conflict")
    await _usable(session, investment.client_id, destination_id)
    if confirm is not None:
        await confirm()
    payout = await payouts.create_payout(
        session,
        client,
        client_id=investment.client_id,
        destination_id=destination_id,
        amount=_proceeds(investment),
        source_reference=investment.id,
        # A new key for each destination tried: a refused payout left nothing behind, and
        # the same destination asked twice is the same payout.
        request_key=f"maturity:{investment.id}:{destination_id}",
        clock=clock,
    )
    await session.execute(
        _SETTLE, {"id": investment.id, "payout_id": payout.id, "now": clock.now()}
    )
    return await get_investment(session, investment.id)


# The worker -------------------------------------------------------------------------------------


async def mature_batch(
    session: AsyncSession, run_date: date, clock: Clock, *, after: str = "", limit: int = BATCH
) -> Batch:
    """Close the next investments whose term ended on or before `run_date`. Worker only.

    Each one is closed inside its own savepoint. One that cannot be closed is left running,
    logged, and met again by the next run.
    """
    if run_date > business_date(clock.now()):
        raise ApplicationError("conflict")
    rows = (
        await session.execute(_DUE, {"run_date": run_date, "after": after, "limit": limit})
    ).all()
    matured = failed = 0
    for row in rows:
        investment = investment_from_row(row)
        try:
            async with session.begin_nested():
                await _mature(session, investment, run_date, clock)
        except Exception as error:
            failed += 1
            logger.error(
                "investment_not_matured",
                investment_id=investment.id,
                error_type=type(error).__name__,
            )
            continue
        matured += 1
    return Batch(last_id=rows[-1].id if rows else None, matured=matured, failed=failed)


async def mature_investment(
    session: AsyncSession, investment_id: str, run_date: date, clock: Clock
) -> Investment:
    """Close one investment if its term has ended by `run_date`. Worker only.

    Calling again for one already closed changes nothing and returns it. `conflict` if it is
    not due.
    """
    if run_date > business_date(clock.now()):
        raise ApplicationError("conflict")
    row = (await session.execute(_LOCKED, {"id": investment_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    investment = investment_from_row(row)
    if investment.status is InvestmentStatus.MATURED:
        return investment
    if not is_due_to_mature(investment.status, investment.maturity_date, run_date):
        raise ApplicationError("conflict")
    await _mature(session, investment, run_date, clock)
    return await get_investment(session, investment.id)


# Internals --------------------------------------------------------------------------------------


async def _mature(
    session: AsyncSession, investment: Investment, run_date: date, clock: Clock
) -> None:
    """Close `investment`, which is locked, active and due."""
    # The last days of interest first, if the interest run has not reached them yet.
    await accrual.accrue_locked(session, investment, run_date, clock)
    investment = await get_investment(session, investment.id)
    if investment.accrued_through != investment.maturity_date:
        raise ApplicationError("conflict")
    row = await product_row(session, investment.product_code)
    product = product_from_row(row)
    # A rollover is quoted like any new investment: by PostgreSQL's own clock, which the
    # database checks the creation time against (migration 0062).
    quoted_at: datetime = await session.scalar(_DATABASE_NOW)
    rate = rate_in_force(await rates_for(session, row.id), business_date(quoted_at))
    plan = plan_maturity(
        investment.at_maturity,
        has_destination=investment.payout_destination_id is not None,
        product_open=product.status is ProductStatus.LIVE and rate is not None,
    )
    if plan.step is MaturityStep.ROLL_OVER and rate is not None:
        await _roll_over(
            session, investment, row.id, rate.annual_rate_bps, rate.tax_bps, quoted_at, clock
        )
    else:
        await _hand_over(session, investment, plan.step, plan.hold_reason, clock)
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="investments.matured",
            resource_type=INVESTMENT_RESOURCE,
            resource_id=investment.id,
            actor=AuditActor.system(),
            details={"step": plan.step.value},
        ),
        clock,
    )
    await notifications.notify(
        session,
        event_name="investment_matured",
        client_id=investment.client_id,
        idempotency_key=investment.id,
        variables={"amount": _proceeds(investment), "product": product.name},
        link=notifications.Link("investment", investment.id),
        now=clock.now(),
    )


async def _roll_over(
    session: AsyncSession,
    investment: Investment,
    product_id: str,
    annual_rate_bps: int,
    tax_bps: int,
    quoted_at: datetime,
    clock: Clock,
) -> None:
    now = clock.now()
    proceeds = _proceeds(investment)
    new_id = push_id()
    # The new term starts the day the old one ended, so no day is left without interest.
    start_date = _maturity(investment)
    if investment.term_days is None:
        raise ApplicationError("conflict")
    await session.execute(
        _INSERT_ROLLED,
        {
            "id": new_id,
            "client_id": investment.client_id,
            "product_id": product_id,
            "currency": proceeds.currency.value,
            "principal_minor": proceeds.minor,
            "annual_rate_bps": annual_rate_bps,
            "tax_bps": tax_bps,
            "term_days": investment.term_days,
            "created_at": quoted_at,
            "pay_by": pay_by(quoted_at),
            "now": now,
            "rolled_from_id": investment.id,
        },
    )
    rolled = await get_investment(session, new_id)
    new_holding = await ledger.open_account(session, holding_account(rolled), clock)
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="investment.rolled_over",
            effective_date=business_date(now),
            lines=(
                *await out_of_holding(session, investment, clock),
                credit(new_holding.id, proceeds),
            ),
            idempotency_key=f"investment-rollover:{investment.id}",
            source_type="investment",
            source_id=investment.id,
        ),
        clock,
    )
    await session.execute(
        _START_ROLLED,
        {
            "id": new_id,
            "start_date": start_date,
            "maturity_date": maturity_date(start_date, investment.term_days),
            "entry_id": entry.id,
            "now": now,
        },
    )
    # A saving locked through a goal stays locked in its next term, until the same day.
    await inherit_lock(
        session, old_id=investment.id, new_id=new_id, today=business_date(now), now=now
    )
    await session.execute(
        _MATURE,
        {
            "id": investment.id,
            "entry_id": entry.id,
            "rolled_into_id": new_id,
            "payout_id": None,
            "refusal": None,
            "now": now,
        },
    )
    await publish(
        session,
        topic=ROLLED_OVER_TOPIC,
        idempotency_key=investment.id,
        payload={
            "investment_id": investment.id,
            "new_investment_id": new_id,
            "client_id": investment.client_id,
            "product_code": investment.product_code,
            "principal_minor": proceeds.minor,
            "currency": proceeds.currency.value,
        },
        consumers=[FOLLOW_ROLLOVER],
        now=now,
    )
    logger.info("investment_rolled_over", investment_id=investment.id, new_investment_id=new_id)


async def _hand_over(
    session: AsyncSession,
    investment: Investment,
    step: MaturityStep,
    hold_reason: HoldReason | None,
    clock: Clock,
) -> None:
    """Hand principal and interest to payouts, and create the payout if there is somewhere to
    send it."""
    now = clock.now()
    proceeds = _proceeds(investment)
    handover = await ledger.open_account(
        session,
        payout_posting.funding_account(investment.client_id, investment.id, proceeds.currency),
        clock,
    )
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="investment.matured",
            effective_date=business_date(now),
            lines=(
                *await out_of_holding(session, investment, clock),
                credit(handover.id, proceeds),
            ),
            idempotency_key=f"investment-matured:{investment.id}",
            source_type="investment",
            source_id=investment.id,
        ),
        clock,
    )
    payout_id: str | None = None
    refusal: str | None = hold_reason.value if hold_reason else None
    destination_id = investment.payout_destination_id
    if step is MaturityStep.PAY_OUT and destination_id is not None:
        try:
            # In a savepoint, so a refusal part way leaves nothing of the payout behind.
            async with session.begin_nested():
                payout = await payouts.create_payout(
                    session,
                    SYSTEM,
                    client_id=investment.client_id,
                    destination_id=destination_id,
                    amount=proceeds,
                    source_reference=investment.id,
                    request_key=f"maturity:{investment.id}:{destination_id}",
                    clock=clock,
                )
            payout_id = payout.id
        except payouts.PayoutRefused as refused:
            refusal = refused.code
        except ApplicationError as error:
            # The destination was removed or is no longer the client's.
            refusal = f"destination_{error.code}"
    await session.execute(
        _MATURE,
        {
            "id": investment.id,
            "entry_id": entry.id,
            "rolled_into_id": None,
            "payout_id": payout_id,
            "refusal": refusal,
            "now": now,
        },
    )
    await publish(
        session,
        topic=MATURED_TOPIC,
        idempotency_key=investment.id,
        payload={
            "investment_id": investment.id,
            "client_id": investment.client_id,
            "product_code": investment.product_code,
            "principal_minor": investment.amount.minor,
            "currency": proceeds.currency.value,
        },
        consumers=[],
        now=now,
    )
    logger.info(
        "investment_matured",
        investment_id=investment.id,
        paid_out=payout_id is not None,
        error_code=refusal,
    )


async def out_of_holding(
    session: AsyncSession, investment: Investment, clock: Clock
) -> tuple[Line, ...]:
    """The debits that empty an investment: its principal, and the interest it earned."""
    holding = await ledger.open_account(session, holding_account(investment), clock)
    lines = [debit(holding.id, investment.amount)]
    if investment.accrued_net.is_positive:
        earned = await ledger.open_account(session, accrual.interest_account(investment), clock)
        lines.append(debit(earned.id, investment.accrued_net))
    return tuple(lines)


def _proceeds(investment: Investment) -> Money:
    """What the client is owed at maturity: the principal and the interest after tax."""
    return investment.amount + investment.accrued_net


def _maturity(investment: Investment) -> date:
    if investment.maturity_date is None:
        raise ApplicationError("conflict")
    return investment.maturity_date


async def _own_locked(session: AsyncSession, client: Actor, investment_id: str) -> Investment:
    if client.kind != "client":
        raise ApplicationError("forbidden")
    row = (await session.execute(_LOCKED, {"id": investment_id})).one_or_none()
    if row is None or row.client_id != client.id:
        raise ApplicationError("not_found")
    return investment_from_row(row)


async def _usable(session: AsyncSession, client_id: str, destination_id: str) -> None:
    try:
        await destinations.usable_destination(session, client_id, destination_id)
    except ApplicationError as error:
        if error.code != "not_found":
            raise
        raise invalid("destination_id", "not_usable") from error
