"""Investing over HTTP: the public catalogue, the client's investments, and the staff screens.

Three audiences, three routers:

* `/public`: the website. Nobody is signed in. Live and paused products and the returns
  calculator, after tax only. Cached for five minutes and readable from any origin.
* `/client`: a signed-in client. Their products, quotes and investments, after tax only. Every
  response is built from a `Client...` type, which carries no tax figure.
* `/staff`: a signed-in member of staff, with the permission each route names. Staff see rates
  before tax and the tax itself.

Each request opens one transaction for the caller and does all its work inside it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import CurrentClient, require_permission
from app.modules.identity.service import StaffSession
from app.modules.investments import service
from app.modules.investments.domain.interest import ClientReturns
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.investments.domain.products import InterestPaid, ProductKind, ProductStatus
from app.modules.investments.domain.rates import ProposalStatus, RateVersion
from app.modules.payouts import payouts
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

PUBLIC_CACHE = "public, max-age=300"
# Whole units of the smallest currency unit. A bound well above any real amount, so a typing
# mistake is refused at the edge instead of reaching the domain.
MAX_AMOUNT_MINOR = 10**15

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

AmountMinor = Annotated[int, Field(ge=1, le=MAX_AMOUNT_MINOR)]
ProductCode = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9-]+$")]
Note = Annotated[str, Field(min_length=1, max_length=500)]
# A request body arrives as JSON, where an enum is its value and a date is ISO text. Only these
# fields are read leniently; amounts stay strict, so "1000" or 1000.0 is still refused.
CurrencyIn = Annotated[Currency, Field(strict=False)]
AtMaturityIn = Annotated[AtMaturity, Field(strict=False)]
DateIn = Annotated[date, Field(strict=False)]


# Contracts --------------------------------------------------------------------------------------


class ProductOut(Contract):
    code: str
    name: str
    kind: ProductKind
    currency: Currency
    # None for an open term.
    term_days: int | None
    minimum_minor: int
    interest_paid: InterestPaid
    status: ProductStatus
    # "10.63": the yearly rate after tax, as a label. No amount is worked out from it.
    client_rate_percent: str


class PublicProducts(Contract):
    updated_on: date
    products: list[ProductOut]


class QuoteOut(Contract):
    product: str
    amount_minor: int
    currency: Currency
    starts_on: date
    # None for an open term, which has no maturity and no fixed interest to quote.
    matures_on: date | None
    interest_minor: int | None
    total_minor: int | None


class InvestmentRequest(Contract):
    product_code: ProductCode
    amount_minor: AmountMinor
    currency: CurrencyIn
    # Fixed terms only. Rolling over is the default.
    at_maturity: AtMaturityIn | None = None


class MaturityChoice(Contract):
    at_maturity: AtMaturityIn
    # Required to be paid out: one of the client's approved payout destinations.
    destination_id: PushId | None = None


class PayOutRequest(Contract):
    destination_id: PushId


class ClientInvestmentOut(Contract):
    id: PushId
    product_code: str
    product_name: str
    status: InvestmentStatus
    amount_minor: int
    currency: Currency
    client_rate_percent: str
    term_days: int | None
    at_maturity: AtMaturity | None
    payout_destination_id: PushId | None
    can_change_maturity: bool
    pay_by: datetime
    start_date: date | None
    maturity_date: date | None
    days_earned: int
    interest_so_far_minor: int
    interest_today_minor: int
    value_minor: int
    # Interest after tax and total at maturity. None for an open term.
    interest_at_maturity_minor: int | None
    total_at_maturity_minor: int | None
    rolled_into_id: PushId | None
    payout_id: PushId | None
    waiting_for_destination: bool


class RateOut(Contract):
    effective_from: date
    annual_rate_bps: int
    tax_bps: int
    client_rate_percent: str


class StaffProductOut(Contract):
    id: PushId
    code: str
    name: str
    kind: ProductKind
    currency: Currency
    term_days: int | None
    minimum_minor: int
    interest_paid: InterestPaid
    status: ProductStatus
    rate: RateOut | None
    next_rate: RateOut | None


class ProposalIn(Contract):
    product_code: ProductCode
    annual_rate_bps: int
    tax_bps: int
    effective_from: DateIn
    reason: Note


class SendBack(Contract):
    note: Note


class ProposalOut(Contract):
    id: PushId
    product_code: str
    rate: RateOut
    reason: str
    status: ProposalStatus
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_note: str | None


class StaffInvestmentOut(Contract):
    id: PushId
    client_id: PushId
    product_code: str
    product_name: str
    status: InvestmentStatus
    amount_minor: int
    currency: Currency
    annual_rate_bps: int
    tax_bps: int
    term_days: int | None
    at_maturity: AtMaturity | None
    created_at: datetime
    pay_by: datetime
    start_date: date | None
    maturity_date: date | None
    accrued_through: date | None
    interest_gross_minor: int
    tax_minor: int
    interest_net_minor: int
    rolled_into_id: PushId | None
    rolled_from_id: PushId | None
    payout_id: PushId | None
    payout_refusal: str | None


class InterestRunOut(Contract):
    run_date: date
    currency: Currency
    investments: int
    gross_minor: int
    tax_minor: int
    net_minor: int
    behind: int
    started_at: datetime
    finished_at: datetime | None


# Wiring -----------------------------------------------------------------------------------------


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]


@asynccontextmanager
async def _transaction(request: Request, actor: Actor | None) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    if actor is None:
        async with database.transaction() as session:
            yield session
    else:
        async with database.actor_transaction(actor) as session:
            yield session


ProductsViewer = Annotated[StaffSession, Depends(require_permission("products.view"))]
RatesViewer = Annotated[StaffSession, Depends(require_permission("rates.view"))]
InvestmentsViewer = Annotated[StaffSession, Depends(require_permission("investments.view"))]


def _money(minor: int, currency: Currency) -> Money:
    return Money(minor, currency)


def _product_out(product: service.ClientProduct) -> ProductOut:
    return ProductOut(
        code=product.code,
        name=product.name,
        kind=product.kind,
        currency=product.currency,
        term_days=product.term_days,
        minimum_minor=product.minimum.minor,
        interest_paid=product.interest_paid,
        status=product.status,
        client_rate_percent=product.rate_percent,
    )


def _quote_out(quote: service.ClientQuote) -> QuoteOut:
    returns: ClientReturns | None = quote.returns
    return QuoteOut(
        product=quote.product.code,
        amount_minor=quote.amount.minor,
        currency=quote.amount.currency,
        starts_on=quote.starts_on,
        matures_on=quote.matures_on,
        interest_minor=None if returns is None else returns.interest.minor,
        total_minor=None if returns is None else returns.total.minor,
    )


def _client_out(view: service.ClientInvestment) -> ClientInvestmentOut:
    at_maturity = view.at_maturity_returns
    return ClientInvestmentOut(
        id=view.id,
        product_code=view.product_code,
        product_name=view.product_name,
        status=view.status,
        amount_minor=view.amount.minor,
        currency=view.amount.currency,
        client_rate_percent=view.rate_percent,
        term_days=view.term_days,
        at_maturity=view.at_maturity,
        payout_destination_id=view.payout_destination_id,
        can_change_maturity=view.can_change_maturity,
        pay_by=view.pay_by,
        start_date=view.start_date,
        maturity_date=view.maturity_date,
        days_earned=view.days_earned,
        interest_so_far_minor=view.interest_so_far.minor,
        interest_today_minor=view.interest_today.minor,
        value_minor=view.value.minor,
        interest_at_maturity_minor=None if at_maturity is None else at_maturity.interest.minor,
        total_at_maturity_minor=None if at_maturity is None else at_maturity.total.minor,
        rolled_into_id=view.rolled_into_id,
        payout_id=view.payout_id,
        waiting_for_destination=view.waiting_for_destination,
    )


def _rate_out(rate: RateVersion | None) -> RateOut | None:
    if rate is None:
        return None
    return RateOut(
        effective_from=rate.effective_from,
        annual_rate_bps=rate.annual_rate_bps,
        tax_bps=rate.tax_bps,
        client_rate_percent=rate.client_rate_percent,
    )


def _staff_product_out(view: service.ProductView) -> StaffProductOut:
    product = view.product
    return StaffProductOut(
        id=view.id,
        code=product.code,
        name=product.name,
        kind=product.kind,
        currency=product.currency,
        term_days=product.term_days,
        minimum_minor=product.minimum.minor,
        interest_paid=product.interest_paid,
        status=product.status,
        rate=_rate_out(view.rate),
        next_rate=_rate_out(view.next_rate),
    )


def _proposal_out(view: service.ProposalView) -> ProposalOut:
    rate = _rate_out(view.rate)
    assert rate is not None
    return ProposalOut(
        id=view.id,
        product_code=view.product_code,
        rate=rate,
        reason=view.reason,
        status=view.status,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_note=view.decision_note,
    )


def _staff_investment_out(investment: service.Investment) -> StaffInvestmentOut:
    terms = investment.terms
    return StaffInvestmentOut(
        id=investment.id,
        client_id=investment.client_id,
        product_code=investment.product_code,
        product_name=investment.product_name,
        status=investment.status,
        amount_minor=investment.amount.minor,
        currency=investment.amount.currency,
        annual_rate_bps=terms.annual_rate_bps,
        tax_bps=terms.tax_bps,
        term_days=investment.term_days,
        at_maturity=investment.at_maturity,
        created_at=investment.created_at,
        pay_by=investment.pay_by,
        start_date=investment.start_date,
        maturity_date=investment.maturity_date,
        accrued_through=investment.accrued_through,
        interest_gross_minor=investment.accrued_gross.minor,
        tax_minor=investment.accrued_tax.minor,
        interest_net_minor=investment.accrued_net.minor,
        rolled_into_id=investment.rolled_into_id,
        rolled_from_id=investment.rolled_from_id,
        payout_id=investment.payout_id,
        payout_refusal=investment.payout_refusal,
    )


def _run_out(run: service.InterestRun) -> InterestRunOut:
    return InterestRunOut(
        run_date=run.run_date,
        currency=run.currency,
        investments=run.investments,
        gross_minor=run.gross.minor,
        tax_minor=run.tax.minor,
        net_minor=run.net.minor,
        behind=run.behind,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _public(response: Response) -> None:
    """The website reads these from the browser, on any of its hosts, with no credentials."""
    response.headers["Cache-Control"] = PUBLIC_CACHE
    response.headers["Access-Control-Allow-Origin"] = "*"


def _product_currency(products: list[service.ClientProduct], code: str) -> Currency:
    for product in products:
        if product.code == code:
            return product.currency
    raise ApplicationError("not_found")


# The website ------------------------------------------------------------------------------------

public_router = APIRouter(prefix="/public", tags=["public investing"], responses=_REFUSALS)


@public_router.get("/products")
async def public_products(request: Request, response: Response, clock: AppClock) -> PublicProducts:
    async with _transaction(request, None) as session:
        products = await service.public_products(session, clock)
    _public(response)
    return PublicProducts(
        updated_on=business_date(clock.now()), products=[_product_out(p) for p in products]
    )


@public_router.get("/quote")
async def public_quote(
    request: Request,
    response: Response,
    clock: AppClock,
    product: Annotated[str, Query(min_length=1, max_length=32, pattern=r"^[A-Z0-9-]+$")],
    amount_minor: Annotated[int, Query(ge=1, le=MAX_AMOUNT_MINOR)],
) -> QuoteOut:
    async with _transaction(request, None) as session:
        products = await service.public_products(session, clock)
        currency = _product_currency(products, product)
        quote = await service.client_quote(session, product, _money(amount_minor, currency), clock)
    _public(response)
    return _quote_out(quote)


# The client -------------------------------------------------------------------------------------

client_router = APIRouter(prefix="/client", tags=["client investing"], responses=_REFUSALS)


@client_router.get("/products")
async def client_products(
    request: Request, client: CurrentClient, clock: AppClock
) -> list[ProductOut]:
    async with _transaction(request, client.actor) as session:
        return [_product_out(p) for p in await service.client_products(session, clock)]


@client_router.get("/products/{code}/quote")
async def client_quote(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    code: str,
    amount_minor: Annotated[int, Query(ge=1, le=MAX_AMOUNT_MINOR)],
) -> QuoteOut:
    async with _transaction(request, client.actor) as session:
        products = await service.client_products(session, clock)
        currency = _product_currency(products, code)
        quote = await service.client_quote(session, code, _money(amount_minor, currency), clock)
    return _quote_out(quote)


@client_router.post("/investments", status_code=201)
async def request_investment(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    key: IdempotencyKey,
    body: InvestmentRequest,
) -> ClientInvestmentOut:
    """Review and confirm: save the investment at today's terms, waiting for its payment."""
    async with _transaction(request, client.actor) as session:
        saved = await service.request_investment(
            session,
            client.actor,
            idempotency_key=key,
            product_code=body.product_code,
            amount=_money(body.amount_minor, body.currency),
            at_maturity=body.at_maturity,
            clock=clock,
        )
        view = await service.client_investment(session, client.actor, saved.id, clock)
    return _client_out(view)


@client_router.get("/investments")
async def client_investments(
    request: Request, client: CurrentClient, clock: AppClock
) -> list[ClientInvestmentOut]:
    async with _transaction(request, client.actor) as session:
        views = await service.client_investments(session, client.actor, clock)
    return [_client_out(view) for view in views]


@client_router.get("/investments/{investment_id}")
async def client_investment(
    request: Request, client: CurrentClient, clock: AppClock, investment_id: str
) -> ClientInvestmentOut:
    async with _transaction(request, client.actor) as session:
        view = await service.client_investment(session, client.actor, investment_id, clock)
    return _client_out(view)


@client_router.put("/investments/{investment_id}/at-maturity")
async def choose_at_maturity(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    investment_id: str,
    body: MaturityChoice,
) -> ClientInvestmentOut:
    """What happens when the term ends. Changeable up to the day before maturity."""
    async with _transaction(request, client.actor) as session:
        await service.choose_at_maturity(
            session,
            client.actor,
            investment_id,
            at_maturity=body.at_maturity,
            destination_id=body.destination_id,
            clock=clock,
        )
        view = await service.client_investment(session, client.actor, investment_id, clock)
    return _client_out(view)


@client_router.post("/investments/{investment_id}/pay-out")
async def pay_out_matured(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    investment_id: str,
    body: PayOutRequest,
) -> ClientInvestmentOut:
    """Send on the money of a matured investment that is waiting for a destination.

    Safe to repeat: the same destination asked twice is the same payout.
    """
    try:
        async with _transaction(request, client.actor) as session:
            await service.pay_out_matured(
                session,
                client.actor,
                investment_id,
                destination_id=body.destination_id,
                clock=clock,
            )
            view = await service.client_investment(session, client.actor, investment_id, clock)
    except payouts.PayoutRefused as refused:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["destination_id"], code=refused.code)],
        ) from None
    return _client_out(view)


# Staff ------------------------------------------------------------------------------------------

staff_router = APIRouter(prefix="/staff", tags=["staff investing"], responses=_REFUSALS)


@staff_router.get("/products")
async def staff_products(
    request: Request, staff: ProductsViewer, clock: AppClock
) -> list[StaffProductOut]:
    async with _transaction(request, staff.actor) as session:
        return [_staff_product_out(v) for v in await service.list_products(session, clock)]


@staff_router.get("/products/{code}/rates")
async def staff_rate_history(request: Request, staff: RatesViewer, code: str) -> list[RateOut]:
    async with _transaction(request, staff.actor) as session:
        rates = await service.rate_history(session, code)
    return [out for out in (_rate_out(rate) for rate in rates) if out is not None]


@staff_router.get("/rate-proposals")
async def staff_rate_proposals(
    request: Request,
    staff: RatesViewer,
    status: ProposalStatus | None = None,
) -> list[ProposalOut]:
    async with _transaction(request, staff.actor) as session:
        views = await service.list_proposals(session, staff.actor, status=status)
    return [_proposal_out(view) for view in views]


@staff_router.post("/rate-proposals", status_code=201)
async def staff_propose_rate(
    request: Request, staff: RatesViewer, clock: AppClock, body: ProposalIn
) -> ProposalOut:
    """Propose a new rate. A second member of staff must approve it."""
    async with _transaction(request, staff.actor) as session:
        view = await service.propose_rate(
            session,
            staff.actor,
            product_code=body.product_code,
            annual_rate_bps=body.annual_rate_bps,
            tax_bps=body.tax_bps,
            effective_from=body.effective_from,
            reason=body.reason,
            clock=clock,
        )
    return _proposal_out(view)


@staff_router.post("/rate-proposals/{proposal_id}/approve")
async def staff_approve_rate(
    request: Request, staff: RatesViewer, clock: AppClock, proposal_id: str
) -> ProposalOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.approve_rate(session, staff.actor, proposal_id, clock)
    return _proposal_out(view)


@staff_router.post("/rate-proposals/{proposal_id}/send-back")
async def staff_send_back_rate(
    request: Request, staff: RatesViewer, clock: AppClock, proposal_id: str, body: SendBack
) -> ProposalOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.send_back_rate(session, staff.actor, proposal_id, body.note, clock)
    return _proposal_out(view)


@staff_router.post("/rate-proposals/{proposal_id}/withdraw")
async def staff_withdraw_rate(
    request: Request, staff: RatesViewer, clock: AppClock, proposal_id: str
) -> ProposalOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.withdraw_rate(session, staff.actor, proposal_id, clock)
    return _proposal_out(view)


@staff_router.get("/investments")
async def staff_investments(
    request: Request,
    staff: InvestmentsViewer,
    status: InvestmentStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[StaffInvestmentOut]:
    async with _transaction(request, staff.actor) as session:
        found = await service.list_investments(session, staff.actor, status=status, limit=limit)
    return [_staff_investment_out(investment) for investment in found]


@staff_router.get("/investments/{investment_id}")
async def staff_investment(
    request: Request, staff: InvestmentsViewer, investment_id: str
) -> StaffInvestmentOut:
    async with _transaction(request, staff.actor) as session:
        investment = await service.get_investment(session, investment_id)
    return _staff_investment_out(investment)


@staff_router.get("/interest-runs")
async def staff_interest_runs(request: Request, staff: InvestmentsViewer) -> list[InterestRunOut]:
    async with _transaction(request, staff.actor) as session:
        runs = await service.interest_runs(session, staff.actor)
    return [_run_out(run) for run in runs]


routers = (public_router, client_router, staff_router)
