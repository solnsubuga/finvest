"""Maturity: what happens when a fixed term ends. Every function works inside the caller's
transaction.

On its maturity date an investment has earned its last day of interest. The maturity run then
closes it in one transaction, and the principal and the interest leave the holding together:

* **Rolled over.** A new investment in the same product starts for principal plus interest, at
  the rate in force that day. One ledger entry moves the money from the old holding to the new.
* **Paid out.** One ledger entry hands the money to the payouts module (the client's "payout
  pending" account for this investment), and a payout to the destination the client chose is
  created in the same transaction. This module never calls a provider.
* **Interest paid, principal kept invested.** A new investment in the same product starts for
  the principal alone, and the interest is handed to payouts and paid out as above, in one
  ledger entry. The new term keeps the same choice and destination.
* **Interest saved to a goal, principal kept invested.** As above, the principal starts a new
  term in the same product, keeping the choice and the goal. The interest starts a second new
  investment at once, in the product the goal saves into, at that product's rate on the day,
  with no payment to make and no minimum (the owner's decision). Both come from one ledger
  entry, and goals records the second as a saving towards the goal. If the goal was closed, or
  its product no longer takes money, the interest rolls over with the principal instead and
  the client is told.
* **Held.** If the client's choice cannot be carried out, or payouts refuses the payout, the
  money is handed over just the same and waits there (only the interest, when the principal
  was kept invested). The reason is kept on the investment, and
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
from app.modules.documents.events import NOTE_ON_END
from app.modules.goals import reads as goal_reads
from app.modules.goals.events import FOLLOW_ROLLOVER, LINK_INTEREST_SAVING
from app.modules.investments import accrual
from app.modules.investments.catalogue import invalid, product_from_row, product_row, rates_for
from app.modules.investments.domain.interest import maturity_date
from app.modules.investments.domain.investments import (
    PAYS_OUT,
    AtMaturity,
    GoalMiss,
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
    GOAL_INTEREST_SOURCE,
    INVESTMENT_RESOURCE,
    Investment,
    get_investment,
    holding_account,
    investment_from_row,
    select_investments,
)
from app.modules.investments.locks import inherit_lock, lock_withdrawals
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
INTEREST_SAVED_TOPIC = "investments.interest_saved_to_goal"
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
    "interest_goal_id = :goal_id, updated_at = :now WHERE id = :id"
)
_INSERT_ROLLED = text(
    "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
    "annual_rate_bps, tax_bps, term_days, at_maturity, payout_destination_id, interest_goal_id, "
    "created_at, pay_by, updated_at, rolled_from_id) VALUES (:id, :client_id, :product_id, "
    ":currency, :principal_minor, :annual_rate_bps, :tax_bps, :term_days, :at_maturity, "
    ":destination_id, :goal_id, :created_at, :pay_by, :now, :rolled_from_id)"
)
_INSERT_INTEREST_SAVING = text(
    "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
    "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at, "
    "source_type, source_id) VALUES (:id, :client_id, :product_id, :currency, :principal_minor, "
    ":annual_rate_bps, :tax_bps, :term_days, :at_maturity, :created_at, :pay_by, :now, "
    ":source_type, :source_id)"
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
    goal_id: str | None = None,
) -> Investment:
    """Save what the client wants done at maturity. It can change up to the day before.

    To be paid out, all of it or the interest alone, the client names one of their approved
    payout destinations. To save the interest to a goal, they name one of their open goals in
    the investment's currency, other than the goal the investment is itself saved towards
    (rolling over already keeps its interest there). Rolling over takes neither, and clears
    one chosen earlier.
    """
    investment = await _own_locked(session, client, investment_id)
    today = business_date(clock.now())
    if not can_choose_maturity(
        investment.status, investment.term_days, investment.maturity_date, today
    ):
        raise ApplicationError("conflict")
    if at_maturity in PAYS_OUT:
        if destination_id is None:
            raise invalid("destination_id", "required")
        await _usable(session, investment.client_id, destination_id)
    elif destination_id is not None:
        raise invalid("destination_id", "not_used_when_rolling_over")
    if at_maturity is AtMaturity.INTEREST_TO_GOAL:
        if goal_id is None:
            raise invalid("goal_id", "required")
        await _usable_goal(session, investment, goal_id)
    elif goal_id is not None:
        raise invalid("goal_id", "not_used")
    await session.execute(
        _CHOOSE,
        {
            "id": investment.id,
            "at_maturity": at_maturity.value,
            "destination_id": destination_id,
            "goal_id": goal_id,
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
            details={
                "at_maturity": at_maturity.value,
                "destination_id": destination_id,
                # Only a choice that saves to a goal names one, so other choices keep their shape.
                **({"goal_id": goal_id} if goal_id is not None else {}),
            },
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
    """Send on the money of a matured investment that is waiting for a destination: all of
    it, or the interest when the principal was kept invested.

    Raises `payouts.PayoutRefused` if payouts will not pay it there now. The money then keeps
    waiting, and the client can try again or choose another destination.

    `confirm` is awaited once the investment and destination pass their checks, just before the
    payout is made. The route passes the app PIN check, which raises to refuse.
    """
    investment = await _own_locked(session, client, investment_id)
    waiting = investment.waiting
    if waiting is None:
        raise ApplicationError("conflict")
    await _usable(session, investment.client_id, destination_id)
    if confirm is not None:
        await confirm()
    payout = await payouts.create_payout(
        session,
        client,
        client_id=investment.client_id,
        destination_id=destination_id,
        amount=waiting,
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


def _term_start(investment: Investment) -> date:
    if investment.start_date is None:
        raise ApplicationError("conflict")
    return investment.start_date


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
    goal: goal_reads.GoalForInterest | None = None
    saving: _GoalSaving | None = None
    miss: GoalMiss | None = None
    if investment.at_maturity is AtMaturity.INTEREST_TO_GOAL:
        goal, saving, miss = await _goal_saving(session, investment, quoted_at)
    plan = plan_maturity(
        investment.at_maturity,
        has_destination=investment.payout_destination_id is not None,
        product_open=product.status is ProductStatus.LIVE and rate is not None,
        goal_miss=miss,
    )
    if plan.step is MaturityStep.ROLL_OVER and rate is not None:
        await _roll_over(
            session,
            investment,
            row.id,
            product.term_days,
            rate.annual_rate_bps,
            rate.tax_bps,
            quoted_at,
            clock,
            # The choice carries on while the goal is open; a closed goal ends it.
            keep_goal=goal is not None and goal.active and miss is not None,
        )
        if goal is not None and miss is not None:
            await notifications.notify(
                session,
                event_name="goal_interest_not_saved",
                client_id=investment.client_id,
                idempotency_key=investment.id,
                variables={"goal": goal.name, "product": product.name},
                link=notifications.Link("investment", investment.id),
                now=clock.now(),
            )
    elif plan.step is MaturityStep.ROLL_OVER_INTEREST_TO_GOAL and rate is not None:
        await _roll_over(
            session,
            investment,
            row.id,
            product.term_days,
            rate.annual_rate_bps,
            rate.tax_bps,
            quoted_at,
            clock,
            to_goal=saving,
        )
    elif plan.step is MaturityStep.ROLL_OVER_PAY_INTEREST and rate is not None:
        await _roll_over(
            session,
            investment,
            row.id,
            product.term_days,
            rate.annual_rate_bps,
            rate.tax_bps,
            quoted_at,
            clock,
            pay_interest=True,
            hold_reason=plan.hold_reason,
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
            details={
                "step": plan.step.value,
                **({"goal_miss": plan.goal_miss.value} if plan.goal_miss else {}),
            },
        ),
        clock,
    )
    await notifications.notify(
        session,
        event_name="investment_matured",
        client_id=investment.client_id,
        idempotency_key=investment.id,
        variables={
            "amount": _proceeds(investment),
            "product": product.name,
            "date": _term_start(investment),
        },
        link=notifications.Link("investment", investment.id),
        now=clock.now(),
    )


@dataclass(frozen=True, slots=True)
class _GoalSaving:
    """Where the interest goes when it is saved to a goal: the goal and its product's terms on
    the day."""

    goal: goal_reads.GoalForInterest
    product_id: str
    term_days: int | None
    annual_rate_bps: int
    tax_bps: int


async def _goal_saving(
    session: AsyncSession, investment: Investment, quoted_at: datetime
) -> tuple[goal_reads.GoalForInterest | None, _GoalSaving | None, GoalMiss | None]:
    """The goal the interest is to be saved to, and whether it can take it today."""
    goal_id = investment.interest_goal_id
    goal = None if goal_id is None else await goal_reads.goal_for_interest(session, goal_id)
    if goal is None or not goal.active or goal.currency is not investment.amount.currency:
        return goal, None, GoalMiss.GOAL_CLOSED
    try:
        row = await product_row(session, goal.product_code)
    except ApplicationError:
        return goal, None, GoalMiss.GOAL_PRODUCT_NOT_OPEN
    product = product_from_row(row)
    rate = rate_in_force(await rates_for(session, row.id), business_date(quoted_at))
    if product.status is not ProductStatus.LIVE or rate is None:
        return goal, None, GoalMiss.GOAL_PRODUCT_NOT_OPEN
    return (
        goal,
        _GoalSaving(goal, row.id, product.term_days, rate.annual_rate_bps, rate.tax_bps),
        None,
    )


async def _roll_over(
    session: AsyncSession,
    investment: Investment,
    product_id: str,
    term_days: int | None,
    annual_rate_bps: int,
    tax_bps: int,
    quoted_at: datetime,
    clock: Clock,
    *,
    pay_interest: bool = False,
    hold_reason: HoldReason | None = None,
    to_goal: _GoalSaving | None = None,
    keep_goal: bool = False,
) -> None:
    """Start a new term in the same product, on the product's own terms on the day.

    The term is the product's, not the old investment's: an investment received from another
    client, or the part a sender kept, ran only the days left of the one it came from.

    With `pay_interest`, the new term is for the principal alone, keeps the client's choice and
    destination, and the interest goes to payouts in the same entry. With `to_goal`, the new
    term is for the principal alone and keeps the choice and the goal, and the interest starts
    a new investment in the goal's product, in the same entry. With `keep_goal`, everything
    rolls over but the new term keeps the choice to save its interest to the goal, which is
    still open.
    """
    now = clock.now()
    currency = investment.amount.currency
    split = pay_interest or to_goal is not None
    interest = investment.accrued_net if split else Money.zero(currency)
    proceeds = _proceeds(investment) - interest
    new_id = push_id()
    # The new term starts the day the old one ended, so no day is left without interest.
    start_date = _maturity(investment)
    if term_days is None:
        raise ApplicationError("conflict")
    if pay_interest:
        choice = AtMaturity.PAY_INTEREST
    elif to_goal is not None or keep_goal:
        choice = AtMaturity.INTEREST_TO_GOAL
    else:
        choice = AtMaturity.ROLL_OVER
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
            "term_days": term_days,
            "at_maturity": choice.value,
            "destination_id": investment.payout_destination_id if pay_interest else None,
            "goal_id": investment.interest_goal_id
            if choice is AtMaturity.INTEREST_TO_GOAL
            else None,
            "created_at": quoted_at,
            "pay_by": pay_by(quoted_at),
            "now": now,
            "rolled_from_id": investment.id,
        },
    )
    rolled = await get_investment(session, new_id)
    new_holding = await ledger.open_account(session, holding_account(rolled), clock)
    lines = [*await out_of_holding(session, investment, clock), credit(new_holding.id, proceeds)]
    saving_id: str | None = None
    if to_goal is not None and interest.is_positive:
        saving_id = await _open_interest_saving(
            session, investment, to_goal, interest, quoted_at, now
        )
        saving = await get_investment(session, saving_id)
        saving_holding = await ledger.open_account(session, holding_account(saving), clock)
        lines.append(credit(saving_holding.id, interest))
    elif pay_interest and interest.is_positive:
        handover = await ledger.open_account(
            session,
            payout_posting.funding_account(investment.client_id, investment.id, interest.currency),
            clock,
        )
        lines.append(credit(handover.id, interest))
    if pay_interest:
        entry_type = "investment.rolled_over_interest_paid"
    elif to_goal is not None:
        entry_type = "investment.rolled_over_interest_saved"
    else:
        entry_type = "investment.rolled_over"
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type=entry_type,
            effective_date=business_date(now),
            lines=tuple(lines),
            idempotency_key=f"investment-rollover:{investment.id}",
            source_type="investment",
            source_id=investment.id,
        ),
        clock,
    )
    payout_id: str | None = None
    refusal: str | None = None
    if pay_interest and interest.is_positive:
        payout_id, refusal = await _pay(session, investment, interest, hold_reason, clock)
    await session.execute(
        _START_ROLLED,
        {
            "id": new_id,
            "start_date": start_date,
            "maturity_date": maturity_date(start_date, term_days),
            "entry_id": entry.id,
            "now": now,
        },
    )
    today = business_date(now)
    # A saving locked through a goal stays locked in its next term, until the same day.
    await inherit_lock(session, old_id=investment.id, new_id=new_id, today=today, now=now)
    if saving_id is not None and to_goal is not None:
        await session.execute(
            _START_ROLLED,
            {
                "id": saving_id,
                "start_date": start_date,
                "maturity_date": None
                if to_goal.term_days is None
                else maturity_date(start_date, to_goal.term_days),
                "entry_id": entry.id,
                "now": now,
            },
        )
        locked_until = to_goal.goal.locked_on(today)
        if locked_until is not None:
            # A locked goal's new savings are locked with it, until the same day.
            await lock_withdrawals(
                session,
                client_id=investment.client_id,
                investment_ids=[saving_id],
                until=locked_until,
                now=now,
            )
    await session.execute(
        _MATURE,
        {
            "id": investment.id,
            "entry_id": entry.id,
            "rolled_into_id": new_id,
            "payout_id": payout_id,
            "refusal": refusal,
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
        # Goals follows the rollover; documents issues the note that confirms the end.
        consumers=[FOLLOW_ROLLOVER, NOTE_ON_END],
        now=now,
    )
    if saving_id is not None and to_goal is not None:
        await publish(
            session,
            topic=INTEREST_SAVED_TOPIC,
            idempotency_key=investment.id,
            payload={
                "investment_id": investment.id,
                "saving_investment_id": saving_id,
                "goal_id": to_goal.goal.id,
                "client_id": investment.client_id,
            },
            # Goals records the new investment as a saving towards the goal.
            consumers=[LINK_INTEREST_SAVING],
            now=now,
        )
    logger.info(
        "investment_rolled_over",
        investment_id=investment.id,
        new_investment_id=new_id,
        interest_paid_out=payout_id is not None,
        interest_saving_id=saving_id,
        error_code=refusal,
    )


async def _open_interest_saving(
    session: AsyncSession,
    investment: Investment,
    to_goal: _GoalSaving,
    interest: Money,
    quoted_at: datetime,
    now: datetime,
) -> str:
    """Insert the investment the interest starts in the goal's product, unpaid, for this same
    transaction to start. The product's minimum does not apply (the owner's decision): the
    money is already invested, so no deposit is taken."""
    saving_id = push_id()
    await session.execute(
        _INSERT_INTEREST_SAVING,
        {
            "id": saving_id,
            "client_id": investment.client_id,
            "product_id": to_goal.product_id,
            "currency": interest.currency.value,
            "principal_minor": interest.minor,
            "annual_rate_bps": to_goal.annual_rate_bps,
            "tax_bps": to_goal.tax_bps,
            "term_days": to_goal.term_days,
            "at_maturity": None if to_goal.term_days is None else AtMaturity.ROLL_OVER.value,
            "created_at": quoted_at,
            "pay_by": pay_by(quoted_at),
            "now": now,
            "source_type": GOAL_INTEREST_SOURCE,
            "source_id": investment.id,
        },
    )
    return saving_id


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
    if step is MaturityStep.PAY_OUT:
        payout_id, refusal = await _pay(session, investment, proceeds, hold_reason, clock)
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
        # Documents issues the note that confirms the investment ended.
        consumers=[NOTE_ON_END],
        now=now,
    )
    logger.info(
        "investment_matured",
        investment_id=investment.id,
        paid_out=payout_id is not None,
        error_code=refusal,
    )


async def _pay(
    session: AsyncSession,
    investment: Investment,
    amount: Money,
    hold_reason: HoldReason | None,
    clock: Clock,
) -> tuple[str | None, str | None]:
    """Pay `amount`, already handed to payouts, to the destination the client chose. Returns
    the payout, or why the money waits instead."""
    destination_id = investment.payout_destination_id
    if destination_id is None or hold_reason is not None:
        return None, (hold_reason or HoldReason.NO_DESTINATION).value
    try:
        # In a savepoint, so a refusal part way leaves nothing of the payout behind.
        async with session.begin_nested():
            payout = await payouts.create_payout(
                session,
                SYSTEM,
                client_id=investment.client_id,
                destination_id=destination_id,
                amount=amount,
                source_reference=investment.id,
                request_key=f"maturity:{investment.id}:{destination_id}",
                clock=clock,
            )
    except payouts.PayoutRefused as refused:
        return None, refused.code
    except ApplicationError as error:
        # The destination was removed or is no longer the client's.
        return None, f"destination_{error.code}"
    return payout.id, None


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


async def _usable_goal(session: AsyncSession, investment: Investment, goal_id: str) -> None:
    """Refuse a goal the interest cannot be saved to: not the client's, closed, in another
    currency, or the goal this investment is itself saved towards."""
    goal = await goal_reads.goal_for_interest(session, goal_id)
    if goal is None or goal.client_id != investment.client_id:
        raise invalid("goal_id", "not_usable")
    if not goal.active:
        raise invalid("goal_id", "goal_closed")
    if goal.currency is not investment.amount.currency:
        raise invalid("goal_id", "not_goal_currency")
    if await goal_reads.goal_saved_to(session, investment.id) == goal.id:
        raise invalid("goal_id", "already_saved_to_goal")
