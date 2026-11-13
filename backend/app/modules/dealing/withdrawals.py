"""Funding early withdrawals from the liquidity buffer or by a sale. Works in the caller's
transaction.

The investing module owns the request, the price and the client's ledger entries. When it has
handed a withdrawal over, its `investments.withdrawn_early` event reaches the worker here
(`dealing.events.FUND_EARLY_WITHDRAWAL`), which records how the withdrawal is funded. A funded
withdrawal is announced as `dealing.withdrawal_funded`, and investing pays the client on it.
The triggers of migration 0084 hold whatever this code does.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import Consumer
from app.infrastructure.jobs.outbox import publish
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.dealing import buffer, holdings
from app.modules.dealing.domain.liquidity import BufferLevel, BufferRules, buffer_level, to_raise
from app.modules.dealing.domain.position import ProductPosition, money_to_place
from app.modules.dealing.domain.withdrawals import (
    CASH_CODES,
    CLAIM_CODES,
    FundingStatus,
    Source,
    buffer_from,
    decide,
    funded_by_sale,
    released_from_queue,
)
from app.modules.investments import service as investments
from app.modules.ledger import statements as ledger
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

FUNDED_TOPIC = "dealing.withdrawal_funded"
# Who acts on `dealing.withdrawal_funded`. Investing adds the consumer that pays the client.
FUNDED_CONSUMERS: tuple[Consumer, ...] = ()
FUNDING_RESOURCE = "dealing_withdrawal_funding"

_COLUMNS = (
    "id, investment_id, currency, amount_minor, source, status, asked_at, funded_at, "
    "sale_order_id, funded_by"
)
_BY_INVESTMENT = text(
    f"SELECT {_COLUMNS} FROM dealing_withdrawal_fundings "  # noqa: S608
    "WHERE investment_id = :investment_id"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM dealing_withdrawal_fundings "  # noqa: S608
    "WHERE status = 'waiting' AND currency = :currency AND source = :source "
    "ORDER BY asked_at, id"
)
_WAITING_CURRENCIES = text(
    "SELECT DISTINCT currency FROM dealing_withdrawal_fundings "
    "WHERE status = 'waiting' AND source = 'buffer' ORDER BY currency"
)
_INSERT = text(
    "INSERT INTO dealing_withdrawal_fundings (id, investment_id, currency, amount_minor, "
    "source, status, asked_at, funded_at) VALUES (:id, :investment_id, :currency, :amount, "
    ":source, :status, :now, :funded_at)"
)
_FUND = text(
    "UPDATE dealing_withdrawal_fundings SET status = 'funded', funded_at = :now "
    "WHERE id = :id AND status = 'waiting'"
)
_FUND_FROM_SALE = text(
    "UPDATE dealing_withdrawal_fundings SET status = 'funded', funded_at = :now, "
    "sale_order_id = :order_id, funded_by = :by WHERE id = :id AND status = 'waiting'"
)
_SALE = text(
    "SELECT kind, status, currency, result_amount_minor FROM dealing_orders WHERE id = :id"
)
_USED_BY_SALE = text(
    "SELECT coalesce(sum(amount_minor), 0)::bigint AS used FROM dealing_withdrawal_fundings "
    "WHERE sale_order_id = :id"
)
# One funding decision per currency at a time, so two withdrawals never count the same cash.
_LOCK_CURRENCY = text(
    "SELECT pg_advisory_xact_lock(hashtext('dealing_buffer'), hashtext(:currency))"
)
# The same lock the database takes before adding up what a sale has funded.
_LOCK_SALE = text(
    "SELECT pg_advisory_xact_lock(hashtext('dealing_withdrawal_sales'), hashtext(:id))"
)


@dataclass(frozen=True, slots=True)
class FundingView:
    id: str
    investment_id: str
    amount: Money
    source: Source
    status: FundingStatus
    asked_at: datetime
    funded_at: datetime | None
    sale_order_id: str | None
    funded_by: str | None


@dataclass(frozen=True, slots=True)
class BufferView:
    """The buffer as the desk sees it now, with the figures it comes from."""

    currency: Currency
    on: date
    rules: BufferRules
    # Cash in the trust bank account and the provider wallets.
    cash: Money
    # Client and tax money in that cash that no bill stands behind.
    owed: Money
    # Client money in running investments with no bill behind it yet.
    to_place: Money
    buffer: Money
    short: Money
    level: BufferLevel
    waiting_for_buffer: Money
    waiting_for_sale: Money
    # What the desk should raise by selling, for the withdrawals waiting and to reach target.
    to_raise: Money


# Queries -----------------------------------------------------------------------------------------


async def buffer_now(session: AsyncSession, currency: Currency, clock: Clock) -> BufferView:
    """The buffer today. For staff and the worker, who see every account."""
    on = business_date(clock.now())
    rules = await buffer.in_force(session, currency, on)
    cash = {code: await ledger.total_balance(session, code, currency) for code in CASH_CODES}
    claims = {code: await ledger.total_balance(session, code, currency) for code in CLAIM_CODES}
    to_place = await _to_place(session, currency, on)
    desk = buffer_from(currency, cash, claims, to_place)
    zero = Money.zero(currency)
    waiting_buffer = sum((f.amount for f in await _waiting(session, currency, Source.BUFFER)), zero)
    waiting_sale = sum((f.amount for f in await _waiting(session, currency, Source.SALE)), zero)
    return BufferView(
        currency=currency,
        on=on,
        rules=rules,
        cash=sum(cash.values(), zero),
        owed=sum(claims.values(), zero),
        to_place=to_place,
        buffer=desk.buffer,
        short=desk.short,
        level=buffer_level(desk.buffer, rules),
        waiting_for_buffer=waiting_buffer,
        waiting_for_sale=waiting_sale,
        to_raise=to_raise(desk.buffer, rules, waiting_sale),
    )


async def funding_for(session: AsyncSession, investment_id: str) -> FundingView | None:
    row = (await session.execute(_BY_INVESTMENT, {"investment_id": investment_id})).one_or_none()
    return None if row is None else _view(row)


async def waiting(session: AsyncSession, currency: Currency, source: Source) -> list[FundingView]:
    """Withdrawals waiting for the buffer or for a sale, in the order asked."""
    return await _waiting(session, currency, source)


# Commands ----------------------------------------------------------------------------------------


async def fund_early_withdrawal(
    session: AsyncSession, investment_id: str, amount: Money, clock: Clock
) -> FundingView:
    """Record how one early withdrawal is funded. Run by the worker.

    Call it once investing has handed the withdrawal over, so the buffer already counts it as
    owed. Asking again for the same investment returns the funding already recorded.
    """
    if not investment_id or not amount.is_positive:
        raise ApplicationError("validation_failed")
    await session.execute(_LOCK_CURRENCY, {"currency": amount.currency.value})
    existing = await funding_for(session, investment_id)
    if existing is not None:
        if existing.amount != amount:
            raise ApplicationError("conflict")
        return existing
    state = await buffer_now(session, amount.currency, clock)
    decision = decide(
        amount,
        state.buffer,
        state.rules,
        others_waiting_for_buffer=state.waiting_for_buffer.is_positive,
    )
    now = clock.now()
    funded = decision.status is FundingStatus.FUNDED
    funding_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": funding_id,
            "investment_id": investment_id,
            "currency": amount.currency.value,
            "amount": amount.minor,
            "source": decision.source.value,
            "status": decision.status.value,
            "now": now,
            "funded_at": now if funded else None,
        },
    )
    view = await _by_id(session, investment_id)
    if funded:
        await _announce(session, view, now)
    return view


async def release_queue(session: AsyncSession, clock: Clock) -> int:
    """Fund the withdrawals queued for the buffer that it can now pay, in the order asked.
    Run by the worker. Returns how many were funded."""
    currencies = [Currency(row.currency) for row in await session.execute(_WAITING_CURRENCIES)]
    released = 0
    for currency in currencies:
        await session.execute(_LOCK_CURRENCY, {"currency": currency.value})
        queue = await _waiting(session, currency, Source.BUFFER)
        if not queue:
            continue
        state = await buffer_now(session, currency, clock)
        count = released_from_queue([f.amount for f in queue], state.buffer, state.rules)
        now = clock.now()
        for funding in queue[:count]:
            await session.execute(_FUND, {"id": funding.id, "now": now})
            await _announce(session, await _by_id(session, funding.investment_id), now)
        released += count
    return released


async def fund_from_sale(
    session: AsyncSession, staff: Actor, order_id: str, clock: Clock
) -> list[FundingView]:
    """Fund the withdrawals waiting for a sale, in the order asked, from a settled sale's
    proceeds that no withdrawal has used yet. Returns those funded, which may be none."""
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    await session.execute(_LOCK_SALE, {"id": order_id})
    sale = (await session.execute(_SALE, {"id": order_id})).one_or_none()
    if sale is None:
        raise ApplicationError("not_found")
    if sale.kind != "secondary_sale" or sale.status != "settled":
        raise ApplicationError("conflict")
    currency = Currency(sale.currency)
    await session.execute(_LOCK_CURRENCY, {"currency": currency.value})
    used = int((await session.execute(_USED_BY_SALE, {"id": order_id})).one().used)
    left = Money(int(sale.result_amount_minor) - used, currency)
    queue = await _waiting(session, currency, Source.SALE)
    count = funded_by_sale([f.amount for f in queue], left)
    now = clock.now()
    funded = []
    for funding in queue[:count]:
        await session.execute(
            _FUND_FROM_SALE, {"id": funding.id, "now": now, "order_id": order_id, "by": staff.id}
        )
        view = await _by_id(session, funding.investment_id)
        await _announce(session, view, now)
        await audit.record(
            session,
            AuditEvent(
                category="change",
                action="dealing.withdrawal.funded_from_sale",
                resource_type=FUNDING_RESOURCE,
                resource_id=view.id,
                actor=AuditActor(staff.kind, staff.id),
                reason=f"Sale {order_id}",
            ),
            clock,
        )
        funded.append(view)
    return funded


# Internals ---------------------------------------------------------------------------------------


async def _to_place(session: AsyncSession, currency: Currency, on: date) -> Money:
    held: defaultdict[str, int] = defaultdict(int)
    for holding in await holdings.holdings_on(session, on):
        if holding.currency is currency:
            held[holding.product_code] += holding.cost_minor
    zero = Money.zero(currency)
    positions = [
        ProductPosition(
            product_code=owed.product_code,
            clients_owed=owed.principal,
            bills_held=Money(held[owed.product_code], currency),
            on_order=zero,
        )
        for owed in await investments.owed_by_product(session, on)
        if owed.currency is currency
    ]
    return money_to_place(positions, currency)


async def _waiting(session: AsyncSession, currency: Currency, source: Source) -> list[FundingView]:
    rows = await session.execute(_WAITING, {"currency": currency.value, "source": source.value})
    return [_view(row) for row in rows.all()]


async def _by_id(session: AsyncSession, investment_id: str) -> FundingView:
    view = await funding_for(session, investment_id)
    assert view is not None
    return view


async def _announce(session: AsyncSession, funding: FundingView, now: datetime) -> None:
    await publish(
        session,
        topic=FUNDED_TOPIC,
        idempotency_key=funding.id,
        payload={
            "funding_id": funding.id,
            "investment_id": funding.investment_id,
            "source": funding.source.value,
            "amount_minor": funding.amount.minor,
            "currency": funding.amount.currency.value,
        },
        consumers=FUNDED_CONSUMERS,
        now=now,
    )


def _view(row: Row[Any]) -> FundingView:
    return FundingView(
        id=row.id,
        investment_id=row.investment_id,
        amount=Money(row.amount_minor, Currency(row.currency)),
        source=Source(row.source),
        status=FundingStatus(row.status),
        asked_at=row.asked_at,
        funded_at=row.funded_at,
        sale_order_id=row.sale_order_id,
        funded_by=row.funded_by,
    )
