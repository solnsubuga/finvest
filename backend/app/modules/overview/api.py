"""GET /staff/overview: the figures on the console's Overview.

Every section is null when the reader's role may not see it. The approvals inbox it sits beside
is GET /staff/approvals.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.infrastructure.database import Database
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.modules.overview import service
from app.modules.overview.domain.figures import AttentionCode, Tone
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope
from app.shared.money import Currency, Money

Reader = Annotated[StaffSession, Depends(require_permission("overview.view"))]
CurrencyCode = Literal["UGX", "USD"]


class Amount(Contract):
    minor: int
    currency: CurrencyCode


def _amount(money: Money) -> Amount:
    return Amount(minor=money.minor, currency=money.currency.value)


class InterestOut(Contract):
    run_date: date
    investments: int
    # After tax: what clients earned.
    net: Amount
    finished_at: datetime | None
    # Investments the run had not paid when it ended. Above zero needs a person.
    behind: int


class DayFlowOut(Contract):
    day: date
    money_in: Amount
    money_out: Amount


class MoneyOut(Contract):
    # Everything Finvest owes its clients in this currency.
    client_money_held: Amount
    # Handed to payouts and not yet left.
    in_transit: Amount
    # Paid in, waiting for its investment to start.
    awaiting_start: Amount
    # Clients with a running investment, and clients whose identity check is approved.
    clients_invested: int
    clients_verified: int
    # The latest daily interest run in this currency.
    interest: InterestOut | None
    # The last seven days, today last.
    days: list[DayFlowOut]


class FloatOut(Contract):
    network: Literal["mtn", "airtel"]
    balance: Amount
    warning_line: Amount
    low: bool


class BalancingOut(Contract):
    # The last day two people signed off as balancing with the providers.
    last_closed: date | None
    # A day prepared by one person and waiting for a second.
    waiting_for_approval: date | None
    days_behind: int


class WeekOut(Contract):
    first_day: date
    last_day: date
    investments: int
    principal: Amount
    # Principal set to pay out at maturity: cash that leaves, before interest.
    pay_out_principal: Amount


class HoldingOut(Contract):
    product_code: str
    product_name: str
    investments: int
    clients: int
    principal: Amount


class AttentionOut(Contract):
    code: AttentionCode
    tone: Tone
    count: int
    since: datetime | None
    subject: str | None


class OverviewOut(Contract):
    today: date
    currency: CurrencyCode
    # money_movement.view
    money: MoneyOut | None
    # money_movement.view, and UGX only: mobile money is in shillings.
    floats: list[FloatOut] | None
    # reconciliation.view
    balancing: BalancingOut | None
    # maturities.view: this week and the next three.
    maturities: list[WeekOut] | None
    # investments.view: running investments by product, the largest first.
    holdings: list[HoldingOut] | None
    # Only what the reader may see, warnings first.
    attention: list[AttentionOut]


router = APIRouter(
    prefix="/staff",
    tags=["staff overview"],
    responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
)
routers = [router]


@router.get("/overview")
async def overview(
    request: Request, staff: Reader, currency: Annotated[CurrencyCode, Query()] = "UGX"
) -> OverviewOut:
    """The Overview's figures that this member of staff may see. Reads only."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        found = await service.overview(session, staff, Currency(currency), request.app.state.clock)
    money = found.money
    return OverviewOut(
        today=found.today,
        currency=currency,
        money=None
        if money is None
        else MoneyOut(
            client_money_held=_amount(money.client_money_held),
            in_transit=_amount(money.in_transit),
            awaiting_start=_amount(money.awaiting_start),
            clients_invested=money.clients_invested,
            clients_verified=money.clients_verified,
            interest=None
            if money.interest is None
            else InterestOut(
                run_date=money.interest.run_date,
                investments=money.interest.investments,
                net=_amount(money.interest.net),
                finished_at=money.interest.finished_at,
                behind=money.interest.behind,
            ),
            days=[
                DayFlowOut(
                    day=day.day, money_in=_amount(day.money_in), money_out=_amount(day.money_out)
                )
                for day in money.days
            ],
        ),
        floats=None
        if found.floats is None
        else [
            FloatOut(
                network=each.network,  # type: ignore[arg-type]
                balance=_amount(each.balance),
                warning_line=_amount(each.warning_line),
                low=each.low,
            )
            for each in found.floats
        ],
        balancing=None
        if found.balancing is None
        else BalancingOut(
            last_closed=found.balancing.last_closed,
            waiting_for_approval=found.balancing.waiting_for_approval,
            days_behind=found.balancing.days_behind,
        ),
        maturities=None
        if found.maturities is None
        else [
            WeekOut(
                first_day=week.first_day,
                last_day=week.last_day,
                investments=week.investments,
                principal=_amount(week.principal),
                pay_out_principal=_amount(week.pay_out_principal),
            )
            for week in found.maturities
        ],
        holdings=None
        if found.holdings is None
        else [
            HoldingOut(
                product_code=each.product_code,
                product_name=each.product_name,
                investments=each.investments,
                clients=each.clients,
                principal=_amount(each.principal),
            )
            for each in found.holdings
        ],
        attention=[
            AttentionOut(
                code=each.code,
                tone=each.tone,
                count=each.count,
                since=each.since,
                subject=each.subject,
            )
            for each in found.attention
        ],
    )
