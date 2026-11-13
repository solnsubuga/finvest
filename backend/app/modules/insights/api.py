"""The client's insights route. Read-only, after tax, for the signed-in client only."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.infrastructure.database import Database
from app.modules.identity.api import CurrentClient
from app.modules.insights import service
from app.modules.insights.domain.months import DEFAULT_MONTHS, MAX_MONTHS
from app.modules.investments.domain.investments import AtMaturity
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/client",
    tags=["client insights"],
    responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
)


class MonthView(Contract):
    month: date
    invested_minor: int
    earned_minor: int
    paid_out_minor: int


class UpcomingView(Contract):
    on: date
    investment_id: PushId
    product_name: str
    at_maturity: AtMaturity | None
    amount_minor: int


class InsightsView(Contract):
    """Amounts are integers in the smallest unit of `currency`, all after tax."""

    currency: str
    months: list[MonthView]
    invested_now_minor: int
    earned_so_far_minor: int
    upcoming: list[UpcomingView]


@router.get("/insights")
async def client_insights(
    request: Request,
    client: CurrentClient,
    months: Annotated[int, Query(ge=1, le=MAX_MONTHS)] = DEFAULT_MONTHS,
) -> InsightsView:
    database: Database = request.app.state.database
    clock: Clock = request.app.state.clock
    async with database.actor_transaction(client.actor) as session:
        insights = await service.client_insights(session, client.actor, clock, months=months)
    return InsightsView(
        currency=insights.currency.value,
        months=[
            MonthView(
                month=month.month,
                invested_minor=month.invested.minor,
                earned_minor=month.earned.minor,
                paid_out_minor=month.paid_out.minor,
            )
            for month in insights.months
        ],
        invested_now_minor=insights.invested_now.minor,
        earned_so_far_minor=insights.earned_so_far.minor,
        upcoming=[
            UpcomingView(
                on=item.on,
                investment_id=item.investment_id,
                product_name=item.product_name,
                at_maturity=item.at_maturity,
                amount_minor=item.amount.minor,
            )
            for item in insights.upcoming
        ],
    )
