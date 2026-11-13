"""Investment commands and queries. Every function works inside the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here. The
trigger and policies of migration 0061 then hold whatever this code does.

Only the ledger moves money. Starting an investment posts one entry: the payment leaves the
client's awaiting-start account and enters their holding for this investment.

Functions whose names start with `client_` return only figures after tax. Other modules import
these through `investments.service`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import publish
from app.modules.investments.catalogue import invalid, product_from_row, product_row, rates_for
from app.modules.investments.domain.interest import (
    ClientReturns,
    Terms,
    client_returns,
    day_accrual,
    days_earned,
)
from app.modules.investments.domain.investments import (
    MAX_AWAITING_PAYMENT,
    AtMaturity,
    InvestmentStatus,
    can_be_cancelled,
    can_be_paid,
    check_start,
    pay_by,
    start,
)
from app.modules.investments.domain.products import check_new_investment
from app.modules.investments.domain.rates import RateVersion, rate_in_force
from app.modules.kyc import service as kyc
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

REQUEST_OPERATION = "investments.request_investment"
STARTED_TOPIC = "investments.started"
AWAITING_START = "client.awaiting_start"
HOLDING = "client.holding"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
SWEEP_BATCH = 200
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Investment:
    """An investment as staff, the worker and the ledger see it. It shows the tax, so it is
    never sent to a client."""

    id: str
    client_id: str
    product_code: str
    product_name: str
    terms: Terms
    term_days: int | None
    at_maturity: AtMaturity | None
    status: InvestmentStatus
    created_at: datetime
    pay_by: datetime
    start_date: date | None
    maturity_date: date | None
    accrued_through: date | None
    accrued_gross: Money
    accrued_tax: Money

    @property
    def amount(self) -> Money:
        return self.terms.principal

    @property
    def accrued_net(self) -> Money:
        return self.accrued_gross - self.accrued_tax


@dataclass(frozen=True, slots=True)
class PayableInvestment:
    """What the payments module needs before it takes a deposit: who, and exactly how much."""

    investment_id: str
    client_id: str
    amount: Money


@dataclass(frozen=True, slots=True)
class ClientInvestment:
    """What a client sees of one investment. Every figure is after tax."""

    id: str
    product_code: str
    product_name: str
    status: InvestmentStatus
    amount: Money
    # "10.63": the yearly rate after tax that this investment earns.
    rate_percent: str
    term_days: int | None
    at_maturity: AtMaturity | None
    pay_by: datetime
    start_date: date | None
    maturity_date: date | None
    # Days of interest earned so far, as posted to the ledger.
    days_earned: int
    interest_so_far: Money
    # What was added by the latest daily run, if that run was for today.
    interest_today: Money
    value: Money
    # Interest and total at maturity. None for an open term.
    at_maturity_returns: ClientReturns | None


_COLUMNS = (
    "i.id, i.client_id, p.code AS product_code, p.name AS product_name, i.currency, "
    "i.principal_minor, i.annual_rate_bps, i.tax_bps, i.term_days, i.at_maturity, i.status, "
    "i.created_at, i.pay_by, i.start_date, i.maturity_date, i.accrued_through, "
    "i.accrued_gross_minor, i.accrued_tax_minor"
)
_FROM = "FROM investments i JOIN investment_products p ON p.id = i.product_id"
_BY_ID = text(f"SELECT {_COLUMNS} {_FROM} WHERE i.id = :id")
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} {_FROM} WHERE i.id = :id FOR UPDATE OF i")
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE i.client_id = :client_id "
    "AND i.status <> 'cancelled' ORDER BY i.created_at DESC, i.id DESC LIMIT :limit"
)
_FOR_STAFF = text(
    f"SELECT {_COLUMNS} {_FROM} "
    "WHERE (CAST(:status AS text) IS NULL OR i.status = :status) "
    "ORDER BY i.created_at DESC, i.id DESC LIMIT :limit"
)
_COUNT_AWAITING = text(
    "SELECT count(*) FROM investments WHERE client_id = :client_id "
    "AND status = 'awaiting_payment' AND pay_by > :now"
)
_INSERT = text(
    "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
    "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at) VALUES "
    "(:id, :client_id, :product_id, :currency, :principal_minor, :annual_rate_bps, :tax_bps, "
    ":term_days, :at_maturity, :now, :pay_by, :now)"
)
_START = text(
    "UPDATE investments SET status = 'active', start_date = :start_date, "
    "maturity_date = :maturity_date, started_at = :now, start_entry_id = :entry_id, "
    "updated_at = :now WHERE id = :id"
)
_CANCEL = text(
    "UPDATE investments SET status = 'cancelled', cancelled_at = :now, updated_at = :now "
    "WHERE id = :id"
)
_OVERDUE = text(
    f"SELECT {_COLUMNS} {_FROM} WHERE i.status = 'awaiting_payment' AND i.pay_by <= :cutoff "
    "ORDER BY i.pay_by LIMIT :limit FOR UPDATE OF i SKIP LOCKED"
)
# One client's requests run one at a time, so the cap on unpaid investments cannot be raced.
_CLIENT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# The client's commands --------------------------------------------------------------------------


async def request_investment(
    session: AsyncSession,
    client: Actor,
    *,
    idempotency_key: str,
    product_code: str,
    amount: Money,
    at_maturity: AtMaturity | None,
    clock: Clock,
) -> Investment:
    """Save the investment a client reviewed and confirmed. Nothing is paid and nothing moves.

    The terms are the product's rate today, and they hold for the payment window. A retry with
    the same key returns the same investment. The same key for a different request is refused.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    client_id = client.id

    async def command() -> Receipt:
        investment_id = await _create(
            session, client, client_id, product_code, amount, at_maturity, clock
        )
        return Receipt(201, {"investment_id": investment_id})

    outcome = await run_once(
        session,
        actor=client,
        operation=REQUEST_OPERATION,
        key=idempotency_key,
        request={
            "product_code": product_code,
            "amount_minor": amount.minor,
            "currency": amount.currency.value,
            "at_maturity": at_maturity.value if at_maturity else None,
        },
        now=clock.now(),
        command=command,
    )
    return await get_investment(session, str(outcome.receipt.response["investment_id"]))


async def _create(
    session: AsyncSession,
    client: Actor,
    client_id: str,
    product_code: str,
    amount: Money,
    at_maturity: AtMaturity | None,
    clock: Clock,
) -> str:
    if await kyc.verified_name(session, client_id) is None:
        # Nobody invests before their identity check is approved.
        raise ApplicationError("forbidden")
    now = clock.now()
    row = await product_row(session, product_code)
    product = product_from_row(row)
    refusal = check_new_investment(product, amount)
    if refusal is not None:
        raise invalid("amount", refusal.value)
    rate = rate_in_force(await rates_for(session, row.id), business_date(now))
    if rate is None:
        raise ApplicationError("not_found")
    if product.has_fixed_term:
        at_maturity = at_maturity or AtMaturity.ROLL_OVER
    elif at_maturity is not None:
        raise invalid("at_maturity", "open_term_has_no_maturity")
    await session.execute(_CLIENT_LOCK, {"lock": f"finvest.investments.client:{client_id}"})
    waiting = (
        await session.execute(_COUNT_AWAITING, {"client_id": client_id, "now": now})
    ).scalar_one()
    if waiting >= MAX_AWAITING_PAYMENT:
        raise ApplicationError("conflict")
    investment_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": investment_id,
            "client_id": client_id,
            "product_id": row.id,
            "currency": amount.currency.value,
            "principal_minor": amount.minor,
            "annual_rate_bps": rate.annual_rate_bps,
            "tax_bps": rate.tax_bps,
            "term_days": product.term_days,
            "at_maturity": at_maturity.value if at_maturity else None,
            "now": now,
            "pay_by": pay_by(now),
        },
    )
    logger.info("investment_requested", investment_id=investment_id, product=product.code)
    return investment_id


# What the payments module calls -----------------------------------------------------------------


async def payable_investment(
    session: AsyncSession, *, client_id: str, investment_id: str, clock: Clock
) -> PayableInvestment:
    """Who pays for an investment and exactly how much, checked before a deposit is taken.

    `not_found` if there is no such investment or it is another client's. `conflict` if it can
    no longer be paid for: it has started, was cancelled, or its payment window has closed.
    """
    row = (await session.execute(_BY_ID, {"id": investment_id})).one_or_none()
    if row is None or row.client_id != client_id:
        raise ApplicationError("not_found")
    investment = _investment(row)
    if not can_be_paid(investment.status, investment.pay_by, clock.now()):
        raise ApplicationError("conflict")
    return PayableInvestment(investment.id, investment.client_id, investment.amount)


# The worker -------------------------------------------------------------------------------------


async def start_investment(
    session: AsyncSession, investment_id: str, *, confirmed_on: date, clock: Clock
) -> Investment:
    """Start an investment whose payment was confirmed on `confirmed_on`. Worker only.

    The whole amount must be waiting in the client's awaiting-start account for this
    investment. It moves to their holding in one ledger entry, in this transaction. Calling
    again for an investment that already started changes nothing and returns it.

    Refused with `conflict` if the money waiting is not exactly the amount, or the investment
    was cancelled. The money then stays where it is, for staff.
    """
    row = (await session.execute(_BY_ID_LOCKED, {"id": investment_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    investment = _investment(row)
    if investment.status is InvestmentStatus.ACTIVE:
        return investment
    now = clock.now()
    today = business_date(now)
    currency = investment.amount.currency
    awaiting = await ledger.open_account(
        session,
        AccountSpec(
            AWAITING_START,
            AccountKind.LIABILITY,
            currency,
            "Paid in, awaiting start",
            owner_client_id=investment.client_id,
            reference=investment.id,
        ),
        clock,
    )
    refusal = check_start(
        investment.status,
        amount=investment.amount,
        waiting=await ledger.get_balance(session, awaiting.id),
        created_on=business_date(investment.created_at),
        confirmed_on=confirmed_on,
        today=today,
    )
    if refusal is not None:
        logger.warning(
            "investment_not_started", investment_id=investment.id, error_code=refusal.value
        )
        raise ApplicationError("conflict")
    holding = await ledger.open_account(session, holding_account(investment), clock)
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="investment.started",
            effective_date=today,
            lines=(debit(awaiting.id, investment.amount), credit(holding.id, investment.amount)),
            idempotency_key=f"investment-start:{investment.id}",
            source_type="investment",
            source_id=investment.id,
        ),
        clock,
    )
    dates = start(confirmed_on, investment.term_days)
    await session.execute(
        _START,
        {
            "id": investment.id,
            "start_date": dates.start_date,
            "maturity_date": dates.maturity_date,
            "entry_id": entry.id,
            "now": now,
        },
    )
    await publish(
        session,
        topic=STARTED_TOPIC,
        idempotency_key=investment.id,
        payload={
            "investment_id": investment.id,
            "client_id": investment.client_id,
            "product_code": investment.product_code,
            "principal_minor": investment.amount.minor,
            "currency": currency.value,
        },
        consumers=[],
        now=now,
    )
    logger.info("investment_started", investment_id=investment.id)
    return await get_investment(session, investment.id)


async def cancel_unpaid(session: AsyncSession, clock: Clock, *, limit: int = SWEEP_BATCH) -> int:
    """Cancel investments nobody paid for in time. Worker only. Returns how many.

    One with money waiting is left alone: that money arrived, so it starts or goes to staff.
    """
    now = clock.now()
    cancelled = 0
    rows = (await session.execute(_OVERDUE, {"cutoff": now, "limit": limit})).all()
    for row in rows:
        investment = _investment(row)
        awaiting = await ledger.open_account(
            session,
            AccountSpec(
                AWAITING_START,
                AccountKind.LIABILITY,
                investment.amount.currency,
                "Paid in, awaiting start",
                owner_client_id=investment.client_id,
                reference=investment.id,
            ),
            clock,
        )
        waiting = await ledger.get_balance(session, awaiting.id)
        if can_be_cancelled(investment.status, investment.pay_by, now, waiting):
            await session.execute(_CANCEL, {"id": investment.id, "now": now})
            cancelled += 1
    return cancelled


# Queries ----------------------------------------------------------------------------------------


async def get_investment(session: AsyncSession, investment_id: str) -> Investment:
    """The investment, if this transaction may see it. For staff and the worker."""
    row = (await session.execute(_BY_ID, {"id": investment_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _investment(row)


async def list_investments(
    session: AsyncSession,
    staff: Actor,
    *,
    status: InvestmentStatus | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[Investment]:
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(
        _FOR_STAFF,
        {"status": status.value if status else None, "limit": max(1, min(limit, MAX_PAGE_SIZE))},
    )
    return [_investment(row) for row in rows.all()]


async def client_investments(
    session: AsyncSession, client: Actor, clock: Clock, *, limit: int = MAX_PAGE_SIZE
) -> list[ClientInvestment]:
    """The client's portfolio, newest first, without cancelled ones. Figures after tax."""
    if client.kind != "client":
        raise ApplicationError("forbidden")
    today = business_date(clock.now())
    rows = await session.execute(
        _FOR_CLIENT, {"client_id": client.id, "limit": max(1, min(limit, MAX_PAGE_SIZE))}
    )
    return [_client_view(_investment(row), today) for row in rows.all()]


async def client_investment(
    session: AsyncSession, client: Actor, investment_id: str, clock: Clock
) -> ClientInvestment:
    if client.kind != "client":
        raise ApplicationError("forbidden")
    row = (await session.execute(_BY_ID, {"id": investment_id})).one_or_none()
    if row is None or row.client_id != client.id:
        raise ApplicationError("not_found")
    return _client_view(_investment(row), business_date(clock.now()))


# Internals --------------------------------------------------------------------------------------


def holding_account(investment: Investment) -> AccountSpec:
    return AccountSpec(
        HOLDING,
        AccountKind.LIABILITY,
        investment.amount.currency,
        "Client holding",
        owner_client_id=investment.client_id,
        reference=investment.id,
    )


def _investment(row: Row[Any]) -> Investment:
    currency = Currency(row.currency)
    return Investment(
        id=row.id,
        client_id=row.client_id,
        product_code=row.product_code,
        product_name=row.product_name,
        terms=Terms(Money(row.principal_minor, currency), row.annual_rate_bps, row.tax_bps),
        term_days=row.term_days,
        at_maturity=AtMaturity(row.at_maturity) if row.at_maturity else None,
        status=InvestmentStatus(row.status),
        created_at=row.created_at,
        pay_by=row.pay_by,
        start_date=row.start_date,
        maturity_date=row.maturity_date,
        accrued_through=row.accrued_through,
        accrued_gross=Money(row.accrued_gross_minor, currency),
        accrued_tax=Money(row.accrued_tax_minor, currency),
    )


def _client_view(investment: Investment, today: date) -> ClientInvestment:
    terms = investment.terms
    earned = (
        0
        if investment.start_date is None or investment.accrued_through is None
        else days_earned(investment.start_date, investment.accrued_through, investment.term_days)
    )
    added_today = investment.accrued_through == today and earned > 0
    return ClientInvestment(
        id=investment.id,
        product_code=investment.product_code,
        product_name=investment.product_name,
        status=investment.status,
        amount=investment.amount,
        rate_percent=RateVersion(
            investment.created_at.date(), terms.annual_rate_bps, terms.tax_bps
        ).client_rate_percent,
        term_days=investment.term_days,
        at_maturity=investment.at_maturity,
        pay_by=investment.pay_by,
        start_date=investment.start_date,
        maturity_date=investment.maturity_date,
        days_earned=earned,
        interest_so_far=investment.accrued_net,
        interest_today=day_accrual(terms, earned).net
        if added_today
        else Money.zero(terms.principal.currency),
        value=terms.principal + investment.accrued_net,
        at_maturity_returns=None
        if investment.term_days is None
        else client_returns(terms, investment.term_days),
    )
