"""Automatic investing routes for the client app.

A plan only says when to ask for a payment. Nothing here moves money. Amounts are integers in
the smallest unit with their currency, never decimals, and no tax rate appears.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.autoinvest import service
from app.modules.identity.api import CurrentClient
from app.modules.identity.service import ClientSession
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity
from app.modules.payments.domain.deposits import Network
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

router = APIRouter(prefix="/client/autoinvest/plans", tags=["autoinvest"], responses=_REFUSALS)

AmountMinor = Annotated[int, Field(gt=0, le=10**15)]
# JSON has no enum type, so these are read from their text forms. Amounts stay strict.
CurrencyCode = Annotated[Currency, Field(strict=False)]
FrequencyCode = Annotated[service.Frequency, Field(strict=False)]
NetworkCode = Annotated[Network, Field(strict=False)]
AtMaturityCode = Annotated[AtMaturity, Field(strict=False)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


DatabaseDep = Annotated[Database, Depends(_database)]
ClockDep = Annotated[Clock, Depends(_clock)]


# Contracts --------------------------------------------------------------------------------------


class PlanDetails(Contract):
    """What a plan does. The same shape sets a plan up and changes it."""

    product_code: Annotated[str, Field(min_length=1, max_length=32)]
    amount_minor: AmountMinor
    currency: CurrencyCode = Currency.UGX
    frequency: FrequencyCode
    # Weekly: 1 for Monday to 7 for Sunday. Monthly: 1 to 31, the last day in a shorter month.
    day: Annotated[int, Field(ge=1, le=31)]
    network: NetworkCode
    # Leave out to use the number on the account. Any other number is refused.
    phone: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    # Leave out for a product with no fixed term.
    at_maturity: AtMaturityCode | None = None


class RunView(Contract):
    run_id: PushId
    # The Kampala day it was due.
    due_on: date
    status: service.RunStatus
    attempt: int
    # The day of the one retry after a failed first try, while it is still to come.
    retry_on: date | None
    # Why it failed or was missed.
    code: str | None
    investment_id: PushId | None


class PlanView(Contract):
    plan_id: PushId
    product_code: str
    product_name: str
    currency: Currency
    amount_minor: int
    frequency: service.Frequency
    day: int
    network: Network
    # E.164: the number on the client's account when the plan was set up.
    payer_msisdn: str
    at_maturity: AtMaturity | None
    status: service.PlanStatus
    # Null while paused or stopped.
    next_run_on: date | None
    created_at: datetime
    paused_at: datetime | None
    stopped_at: datetime | None
    # Latest due day first. Only the plan's own page carries them; the list leaves them empty.
    runs: list[RunView]


class PlanList(Contract):
    plans: list[PlanView]


def _terms(body: PlanDetails, client: ClientSession) -> service.PlanTerms:
    try:
        amount = Money(body.amount_minor, body.currency)
    except ValueError:
        raise _invalid("amount_minor") from None
    try:
        schedule = service.Schedule(body.frequency, body.day)
    except ValueError as error:
        raise _invalid("day", str(error)) from None
    return service.PlanTerms(
        product_code=body.product_code,
        amount=amount,
        schedule=schedule,
        network=body.network,
        phone=body.phone or client.phone,
        at_maturity=body.at_maturity,
    )


def _invalid(field: str, code: str = "invalid") -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


async def _names(session: AsyncSession, clock: Clock) -> dict[str, str]:
    return {
        product.code: product.name for product in await investments.client_products(session, clock)
    }


def _view(
    plan: service.Plan, names: dict[str, str], runs: list[service.Run] | None = None
) -> PlanView:
    return PlanView(
        plan_id=plan.id,
        product_code=plan.product_code,
        # A product no longer offered keeps its code as its name.
        product_name=names.get(plan.product_code, plan.product_code),
        currency=plan.amount.currency,
        amount_minor=plan.amount.minor,
        frequency=plan.schedule.frequency,
        day=plan.schedule.day,
        network=plan.network,
        payer_msisdn=plan.payer_msisdn,
        at_maturity=plan.at_maturity,
        status=plan.status,
        next_run_on=plan.next_run_on,
        created_at=plan.created_at,
        paused_at=plan.paused_at,
        stopped_at=plan.stopped_at,
        runs=[
            RunView(
                run_id=run.id,
                due_on=run.due_on,
                status=run.status,
                attempt=run.attempt,
                retry_on=run.retry_on,
                code=run.code,
                investment_id=run.investment_id,
            )
            for run in runs or []
        ],
    )


async def _detail(session: AsyncSession, plan: service.Plan, clock: Clock) -> PlanView:
    return _view(plan, await _names(session, clock), await service.plan_runs(session, plan.id))


# Routes -----------------------------------------------------------------------------------------


@router.get("")
async def list_plans(
    client: CurrentClient, database: DatabaseDep, clock: ClockDep, include_stopped: bool = False
) -> PlanList:
    """The client's plans, newest first. Stopped plans only when asked for."""
    async with database.actor_transaction(client.actor) as session:
        plans = await service.client_plans(session, client.actor, include_stopped=include_stopped)
        names = await _names(session, clock)
    return PlanList(plans=[_view(plan, names) for plan in plans])


@router.post("", status_code=201, responses={422: {"model": ErrorEnvelope}})
async def create_plan(
    body: PlanDetails,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> PlanView:
    """Set up a plan. The first run is after today. Sending the same key again returns the same
    plan; the same key with other details is refused with `idempotency_key_reused`."""
    async with database.actor_transaction(client.actor) as session:
        plan = await service.create_plan(
            session,
            client.actor,
            _terms(body, client),
            idempotency_key=idempotency_key,
            account_phone=client.phone,
            clock=clock,
        )
        return await _detail(session, plan, clock)


@router.get("/{plan_id}")
async def get_plan(
    plan_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> PlanView:
    async with database.actor_transaction(client.actor) as session:
        plan = await service.client_plan(session, client.actor, plan_id)
        return await _detail(session, plan, clock)


@router.put("/{plan_id}", responses={422: {"model": ErrorEnvelope}})
async def change_plan(
    plan_id: PushId,
    body: PlanDetails,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
) -> PlanView:
    """Replace what a plan does. Sending the same details again changes nothing."""
    async with database.actor_transaction(client.actor) as session:
        plan = await service.change_plan(
            session,
            client.actor,
            plan_id,
            _terms(body, client),
            account_phone=client.phone,
            clock=clock,
        )
        return await _detail(session, plan, clock)


# Pause, resume and stop carry no key: each sets a state, so sending one again changes nothing.


@router.post("/{plan_id}/pause")
async def pause_plan(
    plan_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> PlanView:
    async with database.actor_transaction(client.actor) as session:
        plan = await service.pause_plan(session, client.actor, plan_id, clock)
        return await _detail(session, plan, clock)


@router.post("/{plan_id}/resume")
async def resume_plan(
    plan_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> PlanView:
    async with database.actor_transaction(client.actor) as session:
        plan = await service.resume_plan(session, client.actor, plan_id, clock)
        return await _detail(session, plan, clock)


@router.post("/{plan_id}/stop")
async def stop_plan(
    plan_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> PlanView:
    async with database.actor_transaction(client.actor) as session:
        plan = await service.stop_plan(session, client.actor, plan_id, clock)
        return await _detail(session, plan, clock)


routers: list[APIRouter] = [router]
