"""Plan commands and queries. Every function works inside the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here. The
triggers and policies of migration 0120 then hold whatever this code does.

Nothing here moves money. A plan only says when to ask for a payment; each run is paid for by
its own mobile money prompt, which the client approves on their phone.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.autoinvest.domain.plans import (
    MAX_OPEN_PLANS,
    Frequency,
    PlanStatus,
    RunStatus,
    Schedule,
    can_move_plan,
    check_amount,
    check_day,
    first_run,
)
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity
from app.modules.kyc import service as kyc
from app.modules.payments.domain.deposits import InvalidDeposit, Network, normalise_msisdn
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

CREATE_OPERATION = "autoinvest.create_plan"
PLAN_RESOURCE = "autoinvest_plan"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PlanTerms:
    """What a client asks a plan to do. The same shape sets a plan up and changes it."""

    product_code: str
    amount: Money
    schedule: Schedule
    network: Network
    # The number to prompt. It must be the number on the client's own account.
    phone: str
    at_maturity: AtMaturity | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    id: str
    client_id: str
    product_code: str
    amount: Money
    schedule: Schedule
    network: Network
    payer_msisdn: str
    at_maturity: AtMaturity | None
    status: PlanStatus
    next_run_on: date | None
    created_at: datetime
    updated_at: datetime
    paused_at: datetime | None
    stopped_at: datetime | None


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    plan_id: str
    client_id: str
    due_on: date
    status: RunStatus
    attempt: int
    retry_on: date | None
    investment_id: str | None
    deposit_id: str | None
    code: str | None
    created_at: datetime
    updated_at: datetime


_PLAN_COLUMNS = (
    "id, client_id, product_code, currency, amount_minor, frequency, run_day, network, "
    "payer_msisdn, at_maturity, status, next_run_on, created_at, updated_at, paused_at, "
    "stopped_at"
)
_RUN_COLUMNS = (
    "id, plan_id, client_id, due_on, status, attempt, retry_on, investment_id, deposit_id, code, "
    "created_at, updated_at"
)
_PLAN = text(f"SELECT {_PLAN_COLUMNS} FROM autoinvest_plans WHERE id = :id")  # noqa: S608
_PLAN_LOCKED = text(
    f"SELECT {_PLAN_COLUMNS} FROM autoinvest_plans WHERE id = :id FOR UPDATE"  # noqa: S608
)
_CLIENT_PLANS = text(
    f"SELECT {_PLAN_COLUMNS} FROM autoinvest_plans WHERE client_id = :client_id "  # noqa: S608
    "AND (CAST(:include_stopped AS boolean) OR status <> 'stopped') "
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)
_COUNT_OPEN = text(
    "SELECT count(*) FROM autoinvest_plans WHERE client_id = :client_id AND status <> 'stopped'"
)
_PLAN_RUNS = text(
    f"SELECT {_RUN_COLUMNS} FROM autoinvest_runs WHERE plan_id = :plan_id "  # noqa: S608
    "ORDER BY due_on DESC, id DESC LIMIT :limit"
)
_RUNS = text(
    f"SELECT {_RUN_COLUMNS} FROM autoinvest_runs "  # noqa: S608 - constant column list
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND due_on BETWEEN :date_from AND :date_to "
    "AND (CAST(:before_id AS text) IS NULL OR id < :before_id) "
    "ORDER BY id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO autoinvest_plans (id, client_id, product_code, currency, amount_minor, "
    "frequency, run_day, network, payer_msisdn, at_maturity, status, next_run_on, created_at, "
    "updated_at) VALUES (:id, :client_id, :product_code, :currency, :amount_minor, :frequency, "
    ":run_day, :network, :payer_msisdn, :at_maturity, 'active', :next_run_on, :now, :now)"
)
_UPDATE_TERMS = text(
    "UPDATE autoinvest_plans SET product_code = :product_code, amount_minor = :amount_minor, "
    "frequency = :frequency, run_day = :run_day, network = :network, "
    "payer_msisdn = :payer_msisdn, at_maturity = :at_maturity, next_run_on = :next_run_on, "
    "updated_at = :now WHERE id = :id"
)
_UPDATE_STATUS = text(
    "UPDATE autoinvest_plans SET status = :status, next_run_on = :next_run_on, "
    "paused_at = :paused_at, stopped_at = :stopped_at, updated_at = :now WHERE id = :id"
)
_CLIENT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# The client's commands --------------------------------------------------------------------------


async def create_plan(
    session: AsyncSession,
    client: Actor,
    terms: PlanTerms,
    *,
    idempotency_key: str,
    account_phone: str,
    clock: Clock,
) -> Plan:
    """Set up a plan that invests `terms.amount` on each due day, from tomorrow at the earliest.

    `account_phone` is the number on the client's own account, from their session and never
    from what they sent. A retry with the same key returns the same plan.
    """
    client_id = _client_id(client)
    payer = await _checked(session, client_id, terms, account_phone, clock)

    async def command() -> Receipt:
        await session.execute(_CLIENT_LOCK, {"lock": f"finvest.autoinvest.client:{client_id}"})
        count = (await session.execute(_COUNT_OPEN, {"client_id": client_id})).scalar_one()
        if count >= MAX_OPEN_PLANS:
            raise ApplicationError("conflict")
        plan_id = push_id()
        now = clock.now()
        await session.execute(
            _INSERT,
            {
                "id": plan_id,
                "client_id": client_id,
                "now": now,
                "next_run_on": first_run(terms.schedule, business_date(now)),
                **_terms_values(terms, payer),
            },
        )
        await _audit(session, client, "autoinvest.plan.created", plan_id, terms, clock)
        return Receipt(201, {"plan_id": plan_id})

    outcome = await run_once(
        session,
        actor=client,
        operation=CREATE_OPERATION,
        key=idempotency_key,
        request={**_terms_values(terms, payer), "currency": terms.amount.currency.value},
        now=clock.now(),
        command=command,
    )
    plan_id = str(outcome.receipt.response["plan_id"])
    if not outcome.replayed:
        logger.info("autoinvest_plan_created", plan_id=plan_id)
    return await get_plan(session, plan_id)


async def change_plan(
    session: AsyncSession,
    client: Actor,
    plan_id: str,
    terms: PlanTerms,
    *,
    account_phone: str,
    clock: Clock,
) -> Plan:
    """Replace what a plan does. An active plan's next due day is worked out again from today,
    so a change never brings a collection forward to today. A run already under way is not
    affected. Sending the same terms again changes nothing."""
    client_id = _client_id(client)
    payer = await _checked(session, client_id, terms, account_phone, clock)
    plan = await _own_plan(session, client_id, plan_id)
    if plan.status is PlanStatus.STOPPED:
        raise ApplicationError("conflict")
    values = _terms_values(terms, payer)
    if values == _terms_values(_plan_terms(plan), plan.payer_msisdn):
        return plan
    now = clock.now()
    next_run_on = plan.next_run_on
    if plan.status is PlanStatus.ACTIVE and terms.schedule != plan.schedule:
        next_run_on = first_run(terms.schedule, business_date(now))
    await session.execute(
        _UPDATE_TERMS, {"id": plan.id, "now": now, "next_run_on": next_run_on, **values}
    )
    await _audit(session, client, "autoinvest.plan.changed", plan.id, terms, clock)
    return await get_plan(session, plan.id)


async def pause_plan(session: AsyncSession, client: Actor, plan_id: str, clock: Clock) -> Plan:
    """No runs until the client resumes. Pausing a paused plan changes nothing."""
    return await _move(session, client, plan_id, PlanStatus.PAUSED, clock)


async def resume_plan(session: AsyncSession, client: Actor, plan_id: str, clock: Clock) -> Plan:
    """Runs again from the next due day after today. Days that passed while paused are not
    made up. Resuming an active plan changes nothing."""
    return await _move(session, client, plan_id, PlanStatus.ACTIVE, clock)


async def stop_plan(session: AsyncSession, client: Actor, plan_id: str, clock: Clock) -> Plan:
    """End a plan for good. It is kept as a record. Stopping a stopped plan changes nothing."""
    return await _move(session, client, plan_id, PlanStatus.STOPPED, clock)


# Queries ----------------------------------------------------------------------------------------


async def get_plan(session: AsyncSession, plan_id: str) -> Plan:
    row = (await session.execute(_PLAN, {"id": plan_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _plan(row)


async def client_plans(
    session: AsyncSession, client: Actor, *, include_stopped: bool = False
) -> list[Plan]:
    """A client's plans, newest first. Their row policy fences out everyone else's."""
    client_id = _client_id(client)
    rows = await session.execute(
        _CLIENT_PLANS,
        {"client_id": client_id, "include_stopped": include_stopped, "limit": MAX_OPEN_PLANS * 5},
    )
    return [_plan(row) for row in rows]


async def client_plan(session: AsyncSession, client: Actor, plan_id: str) -> Plan:
    return await _own_plan(session, _client_id(client), plan_id)


async def plan_runs(
    session: AsyncSession, plan_id: str, *, limit: int = DEFAULT_PAGE_SIZE
) -> list[Run]:
    """A plan's runs, latest due day first. A client sees only their own plan's runs."""
    rows = await session.execute(_PLAN_RUNS, {"plan_id": plan_id, "limit": _bounded(limit)})
    return [run_from_row(row) for row in rows]


async def list_runs(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    status: RunStatus | None = None,
    before_id: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[Run]:
    """Runs due between two days, newest first, for the console. Staff only."""
    rows = await session.execute(
        _RUNS,
        {
            "status": status.value if status else None,
            "date_from": date_from,
            "date_to": date_to,
            "before_id": before_id,
            "limit": _bounded(limit),
        },
    )
    return [run_from_row(row) for row in rows]


# Helpers ----------------------------------------------------------------------------------------


async def _move(
    session: AsyncSession, client: Actor, plan_id: str, to: PlanStatus, clock: Clock
) -> Plan:
    client_id = _client_id(client)
    plan = await _own_plan(session, client_id, plan_id, lock=True)
    if plan.status is to:
        return plan
    if not can_move_plan(plan.status, to):
        raise ApplicationError("conflict")
    now = clock.now()
    await session.execute(
        _UPDATE_STATUS,
        {
            "id": plan.id,
            "status": to.value,
            "next_run_on": (
                first_run(plan.schedule, business_date(now)) if to is PlanStatus.ACTIVE else None
            ),
            "paused_at": now if to is PlanStatus.PAUSED else None,
            "stopped_at": now if to is PlanStatus.STOPPED else None,
            "now": now,
        },
    )
    action = {
        PlanStatus.PAUSED: "autoinvest.plan.paused",
        PlanStatus.ACTIVE: "autoinvest.plan.resumed",
        PlanStatus.STOPPED: "autoinvest.plan.stopped",
    }[to]
    await _audit(session, client, action, plan.id, None, clock)
    logger.info("autoinvest_plan_moved", plan_id=plan.id, status=to.value)
    return await get_plan(session, plan.id)


async def _checked(
    session: AsyncSession, client_id: str, terms: PlanTerms, account_phone: str, clock: Clock
) -> str:
    """Refuse terms a run could never be paid for, and return the payer's number."""
    if await kyc.verified_name(session, client_id) is None:
        # Nobody invests before their identity check is approved.
        raise ApplicationError("forbidden")
    if refusal := check_day(terms.schedule.frequency, terms.schedule.day):
        raise _invalid("day", refusal.value)
    if refusal := check_amount(terms.amount):
        raise _invalid("amount", refusal.value)
    try:
        payer = normalise_msisdn(terms.phone)
        if payer != normalise_msisdn(account_phone):
            raise InvalidDeposit("not_account_phone")
    except InvalidDeposit as error:
        raise _invalid("phone", error.code) from None
    # Refused here with `validation_failed` on `amount` if the product would not take it.
    quote = await investments.client_quote(session, terms.product_code, terms.amount, clock)
    if quote.product.currency is not Currency.UGX:
        raise _invalid("product_code", "unsupported_currency")
    if quote.product.term_days is None and terms.at_maturity is not None:
        raise _invalid("at_maturity", "open_term_has_no_maturity")
    return payer


async def _own_plan(
    session: AsyncSession, client_id: str, plan_id: str, *, lock: bool = False
) -> Plan:
    row = (await session.execute(_PLAN_LOCKED if lock else _PLAN, {"id": plan_id})).one_or_none()
    if row is None or row.client_id != client_id:
        raise ApplicationError("not_found")
    return _plan(row)


def _client_id(client: Actor) -> str:
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    return client.id


def _terms_values(terms: PlanTerms, payer: str) -> dict[str, object]:
    return {
        "product_code": terms.product_code,
        "currency": terms.amount.currency.value,
        "amount_minor": terms.amount.minor,
        "frequency": terms.schedule.frequency.value,
        "run_day": terms.schedule.day,
        "network": terms.network.value,
        "payer_msisdn": payer,
        "at_maturity": terms.at_maturity.value if terms.at_maturity else None,
    }


def _plan_terms(plan: Plan) -> PlanTerms:
    return PlanTerms(
        plan.product_code,
        plan.amount,
        plan.schedule,
        plan.network,
        plan.payer_msisdn,
        plan.at_maturity,
    )


async def _audit(
    session: AsyncSession,
    client: Actor,
    action: str,
    plan_id: str,
    terms: PlanTerms | None,
    clock: Clock,
) -> None:
    details: dict[str, object] = {}
    if terms is not None:
        # What kind of plan, never how much or from which number.
        details = {
            "product_code": terms.product_code,
            "frequency": terms.schedule.frequency.value,
        }
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=action,
            resource_type=PLAN_RESOURCE,
            resource_id=plan_id,
            actor=AuditActor(client.kind, client.id),
            details=details,
        ),
        clock,
    )


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _bounded(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def _plan(row: Row[Any]) -> Plan:
    return Plan(
        id=row.id,
        client_id=row.client_id,
        product_code=row.product_code,
        amount=Money(row.amount_minor, Currency(row.currency)),
        schedule=Schedule(Frequency(row.frequency), row.run_day),
        network=Network(row.network),
        payer_msisdn=row.payer_msisdn,
        at_maturity=AtMaturity(row.at_maturity) if row.at_maturity else None,
        status=PlanStatus(row.status),
        next_run_on=row.next_run_on,
        created_at=row.created_at,
        updated_at=row.updated_at,
        paused_at=row.paused_at,
        stopped_at=row.stopped_at,
    )


def run_from_row(row: Row[Any]) -> Run:
    return Run(
        id=row.id,
        plan_id=row.plan_id,
        client_id=row.client_id,
        due_on=row.due_on,
        status=RunStatus(row.status),
        attempt=row.attempt,
        retry_on=row.retry_on,
        investment_id=row.investment_id,
        deposit_id=row.deposit_id,
        code=row.code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


_LIVE_FOR_CLIENT = text(
    "SELECT count(*) FROM autoinvest_plans WHERE client_id = :client_id "
    "AND status IN ('active', 'paused')"
)


async def live_plan_count(session: AsyncSession, client_id: str) -> int:
    """How many of the client's plans are not stopped, so may still ask for a payment."""
    return int(await session.scalar(_LIVE_FOR_CLIENT, {"client_id": client_id}) or 0)
