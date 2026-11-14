"""A goal's saving rule: a monthly amount collected on a set day, by itself.

A rule is a monthly automatic investing plan in the goal's product, set up from the goal. The
plan is the autoinvest module's: on its day the worker saves the investment and the mobile
money deposit, and the client's phone is prompted. Goals keeps only which plan saves towards
which goal (`goal_saving_rules`, migration 0301), and links each run's investment to the goal
as a saving when autoinvest publishes `autoinvest.run_requested`.

Every function works inside the caller's transaction. A goal has at most one rule that has not
ended. Ending a rule stops its plan, and closing a goal ends its rule.
"""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.autoinvest import service as autoinvest
from app.modules.goals.domain.goals import GoalStatus
from app.modules.investments.domain.investments import AtMaturity
from app.modules.payments.domain.deposits import Network
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Money
from app.shared.push_ids import push_id

SET_UP_OPERATION = "goals.set_up_saving_rule"
logger = structlog.get_logger()
# What the database guards raise when they refuse a change.
_REFUSED = "42501"

_OPEN_RULE = text(
    "SELECT id, plan_id, created_at FROM goal_saving_rules "
    "WHERE goal_id = :goal_id AND ended_at IS NULL"
)
_OPEN_RULES = text(
    "SELECT goal_id, plan_id FROM goal_saving_rules "
    "WHERE goal_id = ANY(CAST(:goal_ids AS text[])) AND ended_at IS NULL"
)
_INSERT_RULE = text(
    "INSERT INTO goal_saving_rules (id, goal_id, client_id, plan_id, created_at) "
    "VALUES (:id, :goal_id, :client_id, :plan_id, :now)"
)
_END_RULE = text("UPDATE goal_saving_rules SET ended_at = :now WHERE id = :id")
_RULE_OF_PLAN = text(
    "SELECT r.id, r.goal_id, r.client_id, g.status FROM goal_saving_rules r "
    "JOIN goals g ON g.id = r.goal_id WHERE r.plan_id = :plan_id"
)
_INSERT_SAVING = text(
    "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at, rule_id) "
    "VALUES (:investment_id, :goal_id, :client_id, :now, :rule_id) "
    "ON CONFLICT (investment_id) DO NOTHING"
)


@dataclass(frozen=True, slots=True)
class ClientSavingRule:
    """What a client sees of their goal's saving rule."""

    plan_id: str
    amount: Money
    # The day of the month, 1 to 31. In a shorter month the saving is on its last day.
    day: int
    network: Network
    payer_msisdn: str
    status: autoinvest.PlanStatus
    # The next day a saving is collected. None while the plan is paused.
    next_run_on: date | None
    created_at: datetime


async def set_up_rule(
    session: AsyncSession,
    client: Actor,
    goal_id: str,
    *,
    amount: Money,
    day: int,
    network: Network,
    idempotency_key: str,
    account_phone: str,
    clock: Clock,
) -> ClientSavingRule:
    """Save `amount` towards the goal on `day` of every month, from the next such day.

    `account_phone` is the number on the client's account, from their session: the plan prompts
    only that number. The amount is checked as for any automatic investing plan: whole
    shillings, at most UGX 5,000,000 and at least the product's minimum. A goal with a rule that
    has not ended is refused with `conflict`, as is a closed goal. A retry with the same key
    returns the same rule.
    """
    client_id = _client_id(client)

    async def command() -> Receipt:
        goal = await _locked_goal(session, client_id, goal_id)
        if goal.status != GoalStatus.ACTIVE.value:
            raise ApplicationError("conflict")
        if amount.currency.value != goal.currency:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["currency"], code="not_goal_currency")],
            )
        open_rule = (await session.execute(_OPEN_RULE, {"goal_id": goal_id})).one_or_none()
        if open_rule is not None:
            stopped = await autoinvest.get_plan(session, open_rule.plan_id)
            if stopped.status is not autoinvest.PlanStatus.STOPPED:
                raise ApplicationError("conflict")
            # Its plan was stopped from automatic investing, so the rule is over already.
            await session.execute(_END_RULE, {"id": open_rule.id, "now": clock.now()})
        plan = await autoinvest.create_plan(
            session,
            client,
            autoinvest.PlanTerms(
                product_code=goal.product_code,
                amount=amount,
                schedule=_schedule(day),
                network=network,
                phone=account_phone,
                # A goal's savings roll over, so the money keeps working towards it.
                at_maturity=AtMaturity.ROLL_OVER,
            ),
            idempotency_key=_plan_key(goal_id, idempotency_key),
            account_phone=account_phone,
            clock=clock,
        )
        await session.execute(
            _INSERT_RULE,
            {
                "id": push_id(),
                "goal_id": goal_id,
                "client_id": client_id,
                "plan_id": plan.id,
                "now": clock.now(),
            },
        )
        logger.info("goal_saving_rule_set_up", goal_id=goal_id, plan_id=plan.id)
        return Receipt(201, {"plan_id": plan.id})

    await run_once(
        session,
        actor=client,
        operation=SET_UP_OPERATION,
        key=idempotency_key,
        request={
            "goal_id": goal_id,
            "amount_minor": amount.minor,
            "currency": amount.currency.value,
            "day": day,
            "network": network.value,
        },
        now=clock.now(),
        command=command,
    )
    rule = await client_rule(session, goal_id)
    assert rule is not None
    return rule


async def end_rule(session: AsyncSession, client: Actor, goal_id: str, clock: Clock) -> None:
    """End the goal's saving rule and stop its plan. A goal with no open rule changes nothing.

    Savings already asked for carry on: a prompt already sent can still be paid, and its
    investment still counts towards the goal.
    """
    _client_id(client)
    row = (await session.execute(_OPEN_RULE, {"goal_id": goal_id})).one_or_none()
    if row is None:
        return
    # Stopping a plan that is stopped already changes nothing.
    await autoinvest.stop_plan(session, client, row.plan_id, clock)
    await session.execute(_END_RULE, {"id": row.id, "now": clock.now()})
    logger.info("goal_saving_rule_ended", goal_id=goal_id, plan_id=row.plan_id)


async def client_rule(session: AsyncSession, goal_id: str) -> ClientSavingRule | None:
    """The goal's open saving rule, if this transaction may see it."""
    row = (await session.execute(_OPEN_RULE, {"goal_id": goal_id})).one_or_none()
    if row is None:
        return None
    plan = await autoinvest.get_plan(session, row.plan_id)
    if plan.status is autoinvest.PlanStatus.STOPPED:
        # Stopped from the automatic investing screens: the goal has no monthly saving now.
        return None
    return ClientSavingRule(
        plan_id=plan.id,
        amount=plan.amount,
        day=plan.schedule.day,
        network=plan.network,
        payer_msisdn=plan.payer_msisdn,
        status=plan.status,
        next_run_on=plan.next_run_on if plan.status is autoinvest.PlanStatus.ACTIVE else None,
        created_at=row.created_at,
    )


async def goals_with_rules(session: AsyncSession, goal_ids: list[str]) -> dict[str, str]:
    """Which of these goals are being saved by their rule now, and its plan.

    Only a rule whose plan is active counts. A paused plan collects nothing, and a plan stopped
    from the automatic investing screens has ended the rule in effect, so their goals are
    treated as having no rule: they are reminded to save by hand again.
    """
    if not goal_ids:
        return {}
    rows = (await session.execute(_OPEN_RULES, {"goal_ids": goal_ids})).all()
    saving: dict[str, str] = {}
    for row in rows:
        plan = await autoinvest.get_plan(session, row.plan_id)
        if plan.status is autoinvest.PlanStatus.ACTIVE:
            saving[row.goal_id] = row.plan_id
    return saving


async def link_plan_run(
    session: AsyncSession, *, plan_id: str, client_id: str, investment_id: str, clock: Clock
) -> bool:
    """Record a run's investment as a saving towards the goal whose rule the plan is. Worker.

    False for a plan that is no goal's rule, or whose goal is closed. Also False, and logged,
    for a run said to be another client's than the rule's: nothing is linked across clients.
    Running it again, or for the retry of a run, which asks for the same investment, changes
    nothing.
    """
    row = (await session.execute(_RULE_OF_PLAN, {"plan_id": plan_id})).one_or_none()
    if row is None or row.status != GoalStatus.ACTIVE.value:
        return False
    if row.client_id != client_id:
        logger.warning("goal_saving_rule_client_mismatch", plan_id=plan_id, error_code="mismatch")
        return False
    try:
        # A savepoint, so a goal closed since the read above leaves the job's transaction usable.
        async with session.begin_nested():
            await session.execute(
                _INSERT_SAVING,
                {
                    "investment_id": investment_id,
                    "goal_id": row.goal_id,
                    "client_id": row.client_id,
                    "now": clock.now(),
                    "rule_id": row.id,
                },
            )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != _REFUSED:
            raise
        return False
    return True


_GOAL_LOCKED = text(
    "SELECT id, client_id, status, currency, product_code FROM goals WHERE id = :id FOR UPDATE"
)


async def _locked_goal(session: AsyncSession, client_id: str, goal_id: str) -> Row[Any]:
    row = (await session.execute(_GOAL_LOCKED, {"id": goal_id})).one_or_none()
    if row is None or row.client_id != client_id:
        raise ApplicationError("not_found")
    return row


def _schedule(day: int) -> autoinvest.Schedule:
    try:
        return autoinvest.Schedule(autoinvest.Frequency.MONTHLY, day)
    except ValueError:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["day"], code="invalid_day")]
        ) from None


def _plan_key(goal_id: str, key: str) -> str:
    """The plan's own idempotency key, so the client's key for the rule is not reused as-is."""
    return "goal-rule." + hashlib.sha256(f"{goal_id}:{key}".encode()).hexdigest()[:40]


def _client_id(client: Actor) -> str:
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    return client.id
