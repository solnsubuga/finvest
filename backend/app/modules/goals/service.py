"""Goal commands and queries. Every function works inside the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here. The
trigger and policies of migration 0110 then hold whatever this code does.

A goal moves no money and posts nothing to the ledger. Saving towards a goal asks the
investments module for a new investment, which the client then pays for like any other, and
records that the investment was made for this goal. The goal's value is read back from those
investments, so it always ties to the ledger.

Functions and types whose names start with `client_` or `Client` carry only figures after tax.
They are the only ones a client-facing route may use.
"""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.goals.domain.goals import (
    MAX_ACTIVE_GOALS,
    GoalCategory,
    GoalRefusal,
    GoalStatus,
    Progress,
    check_goal,
    clean_name,
    progress,
)
from app.modules.goals.domain.plans import Plan, PlanProduct, best_plan, product_for_goal
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.investments.domain.products import ProductStatus
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

CREATE_OPERATION = "goals.create_goal"
SAVE_OPERATION = "goals.save_to_goal"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Goal:
    """A goal as staff and the worker see it."""

    id: str
    client_id: str
    name: str
    category: GoalCategory
    target: Money
    target_date: date | None
    product_code: str
    status: GoalStatus
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ClientPlan:
    """The monthly saving that reaches a goal by its date, as the client is shown it.

    An estimate at today's rates, after tax. See `domain/plans.py` for how it is worked out.
    """

    product_code: str
    product_name: str
    # "10.63": the yearly rate after tax, as a label.
    rate_percent: str
    term_days: int
    saving: Money
    savings: int
    first_on: date
    last_on: date
    put_in: Money
    interest: Money
    without_investing: Money
    raised_to_minimum: bool


@dataclass(frozen=True, slots=True)
class ClientGoal:
    """What a client sees of one goal. Every figure is after tax."""

    id: str
    name: str
    category: GoalCategory
    target: Money
    target_date: date | None
    status: GoalStatus
    product_code: str
    created_at: datetime
    progress: Progress
    # What is still needed each month from today. None with no date, once reached, or closed.
    plan: ClientPlan | None
    # The investments made for this goal, newest first, without cancelled ones.
    savings: list[investments.ClientInvestment]


@dataclass(frozen=True, slots=True)
class StaffGoal:
    goal: Goal
    progress: Progress
    savings: list[investments.Investment]


_COLUMNS = (
    "id, client_id, name, category, currency, target_minor, target_date, product_code, status, "
    "created_at, updated_at, closed_at"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM goals WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} FROM goals WHERE id = :id FOR UPDATE")  # noqa: S608
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} FROM goals WHERE client_id = :client_id "  # noqa: S608
    "ORDER BY status, created_at DESC, id DESC LIMIT :limit"
)
_FOR_STAFF = text(
    f"SELECT {_COLUMNS} FROM goals "  # noqa: S608
    "WHERE (CAST(:client_id AS text) IS NULL OR client_id = :client_id) "
    "AND (CAST(:status AS text) IS NULL OR status = :status) "
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)
_COUNT_ACTIVE = text(
    "SELECT count(*) FROM goals WHERE client_id = :client_id AND status = 'active'"
)
_INSERT = text(
    "INSERT INTO goals (id, client_id, name, category, currency, target_minor, target_date, "
    "product_code, created_at, updated_at) VALUES (:id, :client_id, :name, :category, "
    ":currency, :target_minor, :target_date, :product_code, :now, :now)"
)
_UPDATE = text(
    "UPDATE goals SET name = :name, category = :category, target_minor = :target_minor, "
    "target_date = :target_date, product_code = :product_code, updated_at = :now WHERE id = :id"
)
_CLOSE = text(
    "UPDATE goals SET status = 'closed', closed_at = :now, updated_at = :now WHERE id = :id"
)
_SAVINGS = text(
    "SELECT investment_id FROM goal_savings WHERE goal_id = :goal_id "
    "ORDER BY created_at DESC, investment_id DESC"
)
_INSERT_SAVING = text(
    "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at) "
    "VALUES (:investment_id, :goal_id, :client_id, :now) ON CONFLICT (investment_id) DO NOTHING"
)
# One client's goal commands run one at a time, so the cap on open goals cannot be raced.
_CLIENT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# Plans ------------------------------------------------------------------------------------------


async def client_plan(
    session: AsyncSession, *, target: Money, target_date: date, clock: Clock
) -> ClientPlan | None:
    """The plan for a goal before it is saved: what to save each month to reach `target` by
    `target_date`. `None` if no product's term ends before that date."""
    today = business_date(clock.now())
    refusal = check_goal("plan", target, target_date, today)
    if refusal is not None:
        raise _invalid(refusal)
    plan = best_plan(await plan_products(session, clock), target, today, target_date)
    return None if plan is None else _client_plan(plan)


async def plan_products(session: AsyncSession, clock: Clock) -> list[PlanProduct]:
    """The fixed-term products live today, with today's rate."""
    found: list[PlanProduct] = []
    for view in await investments.list_products(session, clock):
        product, rate = view.product, view.rate
        if product.status is not ProductStatus.LIVE or rate is None or product.term_days is None:
            continue
        found.append(
            PlanProduct(
                code=product.code,
                name=product.name,
                term_days=product.term_days,
                minimum=product.minimum,
                annual_rate_bps=rate.annual_rate_bps,
                tax_bps=rate.tax_bps,
                rate_percent=rate.client_rate_percent,
            )
        )
    return found


# The client's commands --------------------------------------------------------------------------


async def create_goal(
    session: AsyncSession,
    client: Actor,
    *,
    idempotency_key: str,
    name: str,
    category: GoalCategory,
    target: Money,
    target_date: date | None,
    clock: Clock,
) -> ClientGoal:
    """Set up a goal. Nothing is paid and nothing moves.

    Its savings go to the product of its plan, or with no date to the shortest term. A retry
    with the same key returns the same goal.
    """
    client_id = _client_id(client)

    async def command() -> Receipt:
        today = business_date(clock.now())
        cleaned = _checked(name, target, target_date, today)
        await session.execute(_CLIENT_LOCK, {"lock": f"finvest.goals.client:{client_id}"})
        active = (await session.execute(_COUNT_ACTIVE, {"client_id": client_id})).scalar_one()
        if active >= MAX_ACTIVE_GOALS:
            raise ApplicationError("conflict")
        product = await _product(session, target, target_date, today, clock)
        goal_id = push_id()
        await session.execute(
            _INSERT,
            {
                "id": goal_id,
                "client_id": client_id,
                "name": cleaned,
                "category": category.value,
                "currency": target.currency.value,
                "target_minor": target.minor,
                "target_date": target_date,
                "product_code": product.code,
                "now": clock.now(),
            },
        )
        logger.info("goal_created", goal_id=goal_id, category=category.value)
        return Receipt(201, {"goal_id": goal_id})

    outcome = await run_once(
        session,
        actor=client,
        operation=CREATE_OPERATION,
        key=idempotency_key,
        request={
            "name": name,
            "category": category.value,
            "target_minor": target.minor,
            "currency": target.currency.value,
            "target_date": target_date.isoformat() if target_date else None,
        },
        now=clock.now(),
        command=command,
    )
    return await client_goal(session, client, str(outcome.receipt.response["goal_id"]), clock)


async def update_goal(
    session: AsyncSession,
    client: Actor,
    goal_id: str,
    *,
    name: str,
    category: GoalCategory,
    target: Money,
    target_date: date | None,
    clock: Clock,
) -> ClientGoal:
    """Change a goal's name, target or date. Savings already made are not touched.

    A new target or date can change the product that later savings go to. The currency is the
    goal's for good.
    """
    goal = await _own_goal(session, client, goal_id, lock=True)
    if goal.status is not GoalStatus.ACTIVE:
        raise ApplicationError("conflict")
    if target.currency is not goal.target.currency:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["currency"], code="unchanged")]
        )
    today = business_date(clock.now())
    cleaned = _checked(name, target, target_date, today)
    product_code = goal.product_code
    if target != goal.target or target_date != goal.target_date:
        needed = target - (await _progress_of(session, client, goal, clock)).value
        product = await _product(
            session, needed if needed.is_positive else target, target_date, today, clock
        )
        product_code = product.code
    await session.execute(
        _UPDATE,
        {
            "id": goal.id,
            "name": cleaned,
            "category": category.value,
            "target_minor": target.minor,
            "target_date": target_date,
            "product_code": product_code,
            "now": clock.now(),
        },
    )
    return await client_goal(session, client, goal.id, clock)


async def close_goal(
    session: AsyncSession, client: Actor, goal_id: str, clock: Clock
) -> ClientGoal:
    """Stop a goal. Its investments carry on as the client's ordinary investments.

    Closing a closed goal again changes nothing.
    """
    goal = await _own_goal(session, client, goal_id, lock=True)
    if goal.status is GoalStatus.ACTIVE:
        await session.execute(_CLOSE, {"id": goal.id, "now": clock.now()})
        logger.info("goal_closed", goal_id=goal.id)
    return await client_goal(session, client, goal.id, clock)


async def save_to_goal(
    session: AsyncSession,
    client: Actor,
    goal_id: str,
    *,
    idempotency_key: str,
    amount: Money,
    clock: Clock,
) -> investments.ClientInvestment:
    """Confirm one saving towards a goal: a new investment awaiting payment.

    The client then pays for that investment by mobile money or bank transfer, exactly as for
    any other, and it starts when the payment is confirmed. It is in the goal's product and
    rolls over at maturity. The amount must meet the product's minimum, because every saving is
    an investment of its own.

    A retry with the same key returns the same investment. The same key for a different saving
    is refused.
    """
    client_id = _client_id(client)

    async def command() -> Receipt:
        goal = await _own_goal(session, client, goal_id, lock=False)
        if goal.status is not GoalStatus.ACTIVE:
            raise ApplicationError("conflict")
        if amount.currency is not goal.target.currency:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["currency"], code="not_goal_currency")],
            )
        investment = await investments.request_investment(
            session,
            client,
            idempotency_key=_investment_key(goal.id, idempotency_key),
            product_code=goal.product_code,
            amount=amount,
            at_maturity=None,
            clock=clock,
        )
        await session.execute(
            _INSERT_SAVING,
            {
                "investment_id": investment.id,
                "goal_id": goal.id,
                "client_id": client_id,
                "now": clock.now(),
            },
        )
        logger.info("goal_saving_confirmed", goal_id=goal.id, investment_id=investment.id)
        return Receipt(201, {"investment_id": investment.id})

    outcome = await run_once(
        session,
        actor=client,
        operation=SAVE_OPERATION,
        key=idempotency_key,
        request={
            "goal_id": goal_id,
            "amount_minor": amount.minor,
            "currency": amount.currency.value,
        },
        now=clock.now(),
        command=command,
    )
    return await investments.client_investment(
        session, client, str(outcome.receipt.response["investment_id"]), clock
    )


# Queries ----------------------------------------------------------------------------------------


async def client_goals(
    session: AsyncSession, client: Actor, clock: Clock, *, limit: int = MAX_PAGE_SIZE
) -> list[ClientGoal]:
    """The client's goals, open ones first, newest first within each."""
    client_id = _client_id(client)
    rows = await session.execute(
        _FOR_CLIENT, {"client_id": client_id, "limit": max(1, min(limit, MAX_PAGE_SIZE))}
    )
    return [await _client_view(session, client, _goal(row), clock) for row in rows.all()]


async def client_goal(
    session: AsyncSession, client: Actor, goal_id: str, clock: Clock
) -> ClientGoal:
    goal = await _own_goal(session, client, goal_id, lock=False)
    return await _client_view(session, client, goal, clock)


async def list_goals(
    session: AsyncSession,
    staff: Actor,
    *,
    client_id: str | None = None,
    status: GoalStatus | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[Goal]:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(
        _FOR_STAFF,
        {
            "client_id": client_id,
            "status": status.value if status else None,
            "limit": max(1, min(limit, MAX_PAGE_SIZE)),
        },
    )
    return [_goal(row) for row in rows.all()]


async def staff_goal(session: AsyncSession, staff: Actor, goal_id: str, clock: Clock) -> StaffGoal:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    goal = await get_goal(session, goal_id)
    savings = [
        await investments.get_investment(session, investment_id)
        for investment_id in await _saving_ids(session, goal.id)
    ]
    savings = [s for s in savings if s.status is not InvestmentStatus.CANCELLED]
    zero = Money.zero(goal.target.currency)
    value = sum((s.amount + s.accrued_net for s in savings if _counts(s.status)), zero)
    interest = sum((s.accrued_net for s in savings if _counts(s.status)), zero)
    waiting = sum((s.amount for s in savings if _waiting(s.status)), zero)
    return StaffGoal(
        goal=goal,
        progress=_standing(goal, value, interest, waiting, clock),
        savings=savings,
    )


async def get_goal(session: AsyncSession, goal_id: str) -> Goal:
    """The goal, if this transaction may see it. For staff and the worker."""
    row = (await session.execute(_BY_ID, {"id": goal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _goal(row)


# Internals --------------------------------------------------------------------------------------


def _client_id(client: Actor) -> str:
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    return client.id


def _invalid(refusal: GoalRefusal) -> ApplicationError:
    field = {
        GoalRefusal.NAME_EMPTY: "name",
        GoalRefusal.NAME_TOO_LONG: "name",
        GoalRefusal.TARGET_NOT_POSITIVE: "target_minor",
        GoalRefusal.DATE_NOT_IN_FUTURE: "target_date",
        GoalRefusal.DATE_TOO_FAR: "target_date",
    }[refusal]
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=refusal.value)]
    )


def _checked(name: str, target: Money, target_date: date | None, today: date) -> str:
    refusal = check_goal(name, target, target_date, today)
    if refusal is not None:
        raise _invalid(refusal)
    return clean_name(name)


async def _product(
    session: AsyncSession,
    needed: Money,
    target_date: date | None,
    today: date,
    clock: Clock,
) -> PlanProduct:
    product = product_for_goal(await plan_products(session, clock), needed, today, target_date)
    if product is None and target_date is not None:
        # Every term would end after the goal's date, so the money would not be back in time.
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["target_date"], code="sooner_than_every_term")],
        )
    if product is None:
        # No live product in the goal's currency.
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["currency"], code="no_product")],
        )
    return product


def _investment_key(goal_id: str, key: str) -> str:
    """The investments module's key for one saving: the goal and the client's own key.

    Hashed so the result fits the key pattern whatever length the client's key was.
    """
    digest = hashlib.sha256(f"{goal_id}:{key}".encode()).hexdigest()[:40]
    return f"goal-saving.{digest}"


async def _own_goal(session: AsyncSession, client: Actor, goal_id: str, *, lock: bool) -> Goal:
    client_id = _client_id(client)
    row = (await session.execute(_BY_ID_LOCKED if lock else _BY_ID, {"id": goal_id})).one_or_none()
    if row is None or row.client_id != client_id:
        raise ApplicationError("not_found")
    return _goal(row)


async def _saving_ids(session: AsyncSession, goal_id: str) -> list[str]:
    return list((await session.execute(_SAVINGS, {"goal_id": goal_id})).scalars().all())


def _counts(status: InvestmentStatus) -> bool:
    return status is InvestmentStatus.ACTIVE


def _waiting(status: InvestmentStatus) -> bool:
    return status is InvestmentStatus.AWAITING_PAYMENT


async def _client_savings(
    session: AsyncSession, client: Actor, goal: Goal, clock: Clock
) -> list[investments.ClientInvestment]:
    savings = [
        await investments.client_investment(session, client, investment_id, clock)
        for investment_id in await _saving_ids(session, goal.id)
    ]
    return [s for s in savings if s.status is not InvestmentStatus.CANCELLED]


async def _progress_of(session: AsyncSession, client: Actor, goal: Goal, clock: Clock) -> Progress:
    return _client_progress(goal, await _client_savings(session, client, goal, clock), clock)


def _client_progress(
    goal: Goal, savings: list[investments.ClientInvestment], clock: Clock
) -> Progress:
    zero = Money.zero(goal.target.currency)
    value = sum((s.value for s in savings if _counts(s.status)), zero)
    interest = sum((s.interest_so_far for s in savings if _counts(s.status)), zero)
    waiting = sum((s.amount for s in savings if _waiting(s.status)), zero)
    return _standing(goal, value, interest, waiting, clock)


def _standing(goal: Goal, value: Money, interest: Money, waiting: Money, clock: Clock) -> Progress:
    return progress(
        target=goal.target,
        value=value,
        interest=interest,
        awaiting_payment=waiting,
        set_up_on=business_date(goal.created_at),
        target_date=goal.target_date,
        today=business_date(clock.now()),
    )


async def _client_view(
    session: AsyncSession, client: Actor, goal: Goal, clock: Clock
) -> ClientGoal:
    savings = await _client_savings(session, client, goal, clock)
    standing = _client_progress(goal, savings, clock)
    plan: ClientPlan | None = None
    needed = goal.target - standing.value - standing.awaiting_payment
    today = business_date(clock.now())
    if (
        goal.status is GoalStatus.ACTIVE
        and goal.target_date is not None
        and goal.target_date > today
        and needed.is_positive
    ):
        products = [p for p in await plan_products(session, clock) if p.code == goal.product_code]
        found = best_plan(products, needed, today, goal.target_date)
        plan = None if found is None else _client_plan(found)
    return ClientGoal(
        id=goal.id,
        name=goal.name,
        category=goal.category,
        target=goal.target,
        target_date=goal.target_date,
        status=goal.status,
        product_code=goal.product_code,
        created_at=goal.created_at,
        progress=standing,
        plan=plan,
        savings=savings,
    )


def _client_plan(plan: Plan) -> ClientPlan:
    return ClientPlan(
        product_code=plan.product.code,
        product_name=plan.product.name,
        rate_percent=plan.product.rate_percent,
        term_days=plan.product.term_days,
        saving=plan.saving,
        savings=plan.savings,
        first_on=plan.first_on,
        last_on=plan.last_on,
        put_in=plan.put_in,
        interest=plan.interest,
        without_investing=plan.without_investing,
        raised_to_minimum=plan.raised_to_minimum,
    )


def _goal(row: Row[Any]) -> Goal:
    currency = Currency(row.currency)
    return Goal(
        id=row.id,
        client_id=row.client_id,
        name=row.name,
        category=GoalCategory(row.category),
        target=Money(row.target_minor, currency),
        target_date=row.target_date,
        product_code=row.product_code,
        status=GoalStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        closed_at=row.closed_at,
    )
