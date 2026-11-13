"""Goal routes for the client app and the console.

Amounts are integers in the smallest unit with their currency, never decimals. Client
responses carry figures after tax only, and never a tax rate.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.goals import service
from app.modules.goals.domain.goals import (
    MAX_NAME_LENGTH,
    GoalCategory,
    GoalStatus,
    Progress,
    Standing,
)
from app.modules.identity.api import CurrentClient, require_permission
from app.modules.identity.service import StaffSession
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

client_router = APIRouter(prefix="/client/goals", tags=["goals"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff/goals", tags=["goals"], responses=_REFUSALS)
StaffWithView = Annotated[StaffSession, Depends(require_permission("goals.view"))]

AmountMinor = Annotated[int, Field(gt=0, le=10**15)]
# JSON has no date or enum type, so these are read from their text forms. Amounts stay strict.
Day = Annotated[date, Field(strict=False)]
Category = Annotated[GoalCategory, Field(strict=False)]
CurrencyCode = Annotated[Currency, Field(strict=False)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


DatabaseDep = Annotated[Database, Depends(_database)]
ClockDep = Annotated[Clock, Depends(_clock)]


def _money(amount_minor: int, currency: Currency) -> Money:
    try:
        return Money(amount_minor, currency)
    except ValueError:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["amount_minor"], code="invalid")],
        ) from None


# Contracts --------------------------------------------------------------------------------------


class GoalDetails(Contract):
    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH * 2)]
    category: Category
    target_minor: AmountMinor
    currency: CurrencyCode = Currency.UGX
    # Leave out for a goal with no date, such as an emergency fund.
    target_date: Day | None = None


class NewSaving(Contract):
    amount_minor: AmountMinor
    currency: CurrencyCode = Currency.UGX


class PlanView(Contract):
    """An estimate at today's rates, after tax. Never a promise."""

    product_code: str
    product_name: str
    rate_percent: str
    term_days: int
    currency: Currency
    saving_minor: int
    savings: int
    first_on: date
    last_on: date
    put_in_minor: int
    interest_minor: int
    without_investing_minor: int
    raised_to_minimum: bool


class ProgressView(Contract):
    value_minor: int
    interest_minor: int
    awaiting_payment_minor: int
    percent: int
    standing: Standing


class SavingView(Contract):
    """One investment made for the goal. Figures after tax."""

    investment_id: PushId
    product_code: str
    product_name: str
    status: InvestmentStatus
    amount_minor: int
    value_minor: int
    interest_minor: int
    rate_percent: str
    at_maturity: AtMaturity | None
    pay_by: datetime
    start_date: date | None
    maturity_date: date | None


class GoalView(Contract):
    goal_id: PushId
    name: str
    category: GoalCategory
    currency: Currency
    target_minor: int
    target_date: date | None
    status: GoalStatus
    product_code: str
    created_at: datetime
    progress: ProgressView
    plan: PlanView | None
    savings: list[SavingView]


class GoalList(Contract):
    goals: list[GoalView]


class StaffSavingView(Contract):
    investment_id: PushId
    product_code: str
    status: InvestmentStatus
    amount_minor: int
    accrued_net_minor: int
    start_date: date | None
    maturity_date: date | None


class StaffGoalView(Contract):
    goal_id: PushId
    client_id: PushId
    name: str
    category: GoalCategory
    currency: Currency
    target_minor: int
    target_date: date | None
    status: GoalStatus
    product_code: str
    created_at: datetime
    closed_at: datetime | None


class StaffGoalList(Contract):
    goals: list[StaffGoalView]


class StaffGoalDetail(Contract):
    goal: StaffGoalView
    progress: ProgressView
    savings: list[StaffSavingView]


def _plan_view(plan: service.ClientPlan) -> PlanView:
    return PlanView(
        product_code=plan.product_code,
        product_name=plan.product_name,
        rate_percent=plan.rate_percent,
        term_days=plan.term_days,
        currency=plan.saving.currency,
        saving_minor=plan.saving.minor,
        savings=plan.savings,
        first_on=plan.first_on,
        last_on=plan.last_on,
        put_in_minor=plan.put_in.minor,
        interest_minor=plan.interest.minor,
        without_investing_minor=plan.without_investing.minor,
        raised_to_minimum=plan.raised_to_minimum,
    )


def _progress_view(progress: Progress) -> ProgressView:
    return ProgressView(
        value_minor=progress.value.minor,
        interest_minor=progress.interest.minor,
        awaiting_payment_minor=progress.awaiting_payment.minor,
        percent=progress.percent,
        standing=progress.standing,
    )


def _saving_view(saving: investments.ClientInvestment) -> SavingView:
    return SavingView(
        investment_id=saving.id,
        product_code=saving.product_code,
        product_name=saving.product_name,
        status=saving.status,
        amount_minor=saving.amount.minor,
        value_minor=saving.value.minor,
        interest_minor=saving.interest_so_far.minor,
        rate_percent=saving.rate_percent,
        at_maturity=saving.at_maturity,
        pay_by=saving.pay_by,
        start_date=saving.start_date,
        maturity_date=saving.maturity_date,
    )


def _goal_view(goal: service.ClientGoal) -> GoalView:
    return GoalView(
        goal_id=goal.id,
        name=goal.name,
        category=goal.category,
        currency=goal.target.currency,
        target_minor=goal.target.minor,
        target_date=goal.target_date,
        status=goal.status,
        product_code=goal.product_code,
        created_at=goal.created_at,
        progress=_progress_view(goal.progress),
        plan=None if goal.plan is None else _plan_view(goal.plan),
        savings=[_saving_view(s) for s in goal.savings],
    )


def _staff_goal_view(goal: service.Goal) -> StaffGoalView:
    return StaffGoalView(
        goal_id=goal.id,
        client_id=goal.client_id,
        name=goal.name,
        category=goal.category,
        currency=goal.target.currency,
        target_minor=goal.target.minor,
        target_date=goal.target_date,
        status=goal.status,
        product_code=goal.product_code,
        created_at=goal.created_at,
        closed_at=goal.closed_at,
    )


# Client routes ----------------------------------------------------------------------------------


@client_router.get("/plan", responses={422: {"model": ErrorEnvelope}})
async def plan(
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    target_minor: Annotated[int, Query(gt=0, le=10**15)],
    target_date: date,
    currency: Currency = Currency.UGX,
) -> PlanView | None:
    """What to save each month to reach a target by a date, before the goal is saved.

    `null` when no product's term ends before the date.
    """
    async with database.actor_transaction(client.actor) as session:
        found = await service.client_plan(
            session, target=_money(target_minor, currency), target_date=target_date, clock=clock
        )
    return None if found is None else _plan_view(found)


@client_router.post("", status_code=201, responses={422: {"model": ErrorEnvelope}})
async def create_goal(
    body: GoalDetails,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.create_goal(
            session,
            client.actor,
            idempotency_key=idempotency_key,
            name=body.name,
            category=body.category,
            target=_money(body.target_minor, body.currency),
            target_date=body.target_date,
            clock=clock,
        )
    return _goal_view(goal)


@client_router.get("")
async def list_goals(client: CurrentClient, database: DatabaseDep, clock: ClockDep) -> GoalList:
    async with database.actor_transaction(client.actor) as session:
        goals = await service.client_goals(session, client.actor, clock)
    return GoalList(goals=[_goal_view(g) for g in goals])


@client_router.get("/{goal_id}")
async def get_goal(
    goal_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.client_goal(session, client.actor, goal_id, clock)
    return _goal_view(goal)


@client_router.put("/{goal_id}", responses={422: {"model": ErrorEnvelope}})
async def update_goal(
    goal_id: PushId,
    body: GoalDetails,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.update_goal(
            session,
            client.actor,
            goal_id,
            name=body.name,
            category=body.category,
            target=_money(body.target_minor, body.currency),
            target_date=body.target_date,
            clock=clock,
        )
    return _goal_view(goal)


@client_router.post("/{goal_id}/close")
async def close_goal(
    goal_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.close_goal(session, client.actor, goal_id, clock)
    return _goal_view(goal)


@client_router.post(
    "/{goal_id}/savings", status_code=201, responses={422: {"model": ErrorEnvelope}}
)
async def save_to_goal(
    goal_id: PushId,
    body: NewSaving,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> SavingView:
    """Confirm a saving: a new investment awaiting payment. Pay for it as for any investment."""
    async with database.actor_transaction(client.actor) as session:
        saving = await service.save_to_goal(
            session,
            client.actor,
            goal_id,
            idempotency_key=idempotency_key,
            amount=_money(body.amount_minor, body.currency),
            clock=clock,
        )
    return _saving_view(saving)


# Staff routes -----------------------------------------------------------------------------------


@staff_router.get("")
async def staff_list_goals(
    staff: StaffWithView,
    database: DatabaseDep,
    client_id: PushId | None = None,
    status: GoalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = service.DEFAULT_PAGE_SIZE,
) -> StaffGoalList:
    async with database.actor_transaction(staff.actor) as session:
        goals = await service.list_goals(
            session, staff.actor, client_id=client_id, status=status, limit=limit
        )
    return StaffGoalList(goals=[_staff_goal_view(g) for g in goals])


@staff_router.get("/{goal_id}")
async def staff_get_goal(
    goal_id: PushId, staff: StaffWithView, database: DatabaseDep, clock: ClockDep
) -> StaffGoalDetail:
    async with database.actor_transaction(staff.actor) as session:
        found = await service.staff_goal(session, staff.actor, goal_id, clock)
    return StaffGoalDetail(
        goal=_staff_goal_view(found.goal),
        progress=_progress_view(found.progress),
        savings=[
            StaffSavingView(
                investment_id=s.id,
                product_code=s.product_code,
                status=s.status,
                amount_minor=s.amount.minor,
                accrued_net_minor=s.accrued_net.minor,
                start_date=s.start_date,
                maturity_date=s.maturity_date,
            )
            for s in found.savings
        ],
    )


routers: list[APIRouter] = [client_router, staff_router]
