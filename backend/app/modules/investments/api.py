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

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.settings import Settings
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.pins_api import Pins
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


# From POST /client/pin/confirm with action `withdrawal`. The phone app sends it once it has
# a PIN; a browser never needs it.
PinConfirmation = Annotated[str | None, Field(max_length=200)]


class PayOutRequest(Contract):
    destination_id: PushId
    pin_confirmation: PinConfirmation = None


class WithdrawalRequest(Contract):
    # One of the client's approved payout destinations.
    destination_id: PushId
    pin_confirmation: PinConfirmation = None


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
    # What waits for the client to say where to send it: all of it, or only the interest when
    # they chose to keep the principal invested. Null when nothing waits.
    waiting_minor: int | None = None
    # Whether the client may ask to withdraw it early now, and when they did. An early
    # withdrawal pays `value_minor`: interest stops when it is asked for.
    can_withdraw: bool
    withdrawal_requested_at: datetime | None
    # Whether the client may send part or all of it to another client now.
    can_send: bool = False
    # Locked through a goal: the first day it may be withdrawn early or sent on again. Null
    # when it is not locked.
    withdrawal_locked_until: date | None = None


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


KindIn = Annotated[ProductKind, Field(strict=False)]
InterestPaidIn = Annotated[InterestPaid, Field(strict=False)]
NewCode = Annotated[
    str, Field(min_length=1, max_length=24, pattern=r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$")
]
Name = Annotated[str, Field(min_length=1, max_length=80)]
TermDays = Annotated[int, Field(ge=1, le=3650)]
Bps = Annotated[int, Field(ge=0, le=9999)]


class NewProductIn(Contract):
    """A new product. It is created as a draft, with this first rate, once a second person
    approves. Opening it to clients is a change of its own."""

    code: NewCode
    name: Name
    kind: KindIn
    currency: CurrencyIn
    # None for an open term, which runs until the client withdraws.
    term_days: TermDays | None
    minimum_minor: AmountMinor
    interest_paid: InterestPaidIn
    annual_rate_bps: Bps
    tax_bps: Bps
    reason: Note


class NewTermsIn(Contract):
    """New terms for an existing product. The code and currency never change, and the kind and
    when interest is paid change only while it is a draft. The rate changes on its own route."""

    product_code: ProductCode
    name: Name
    kind: KindIn
    term_days: TermDays | None
    minimum_minor: AmountMinor
    interest_paid: InterestPaidIn
    reason: Note


class StatusChangeIn(Contract):
    product_code: ProductCode
    # `live` opens the product to new investments. `paused` stops them; running ones carry on.
    to: Literal["live", "paused"]
    reason: Note


class ProductChangeOut(Contract):
    id: PushId
    action: service.ProductAction
    product_code: str
    # The whole product as it will be once approved.
    name: str
    kind: ProductKind
    currency: Currency
    term_days: int | None
    minimum_minor: int
    interest_paid: InterestPaid
    product_status: ProductStatus
    # A new product's first rate. None for any other change.
    first_rate: RateOut | None
    reason: str
    status: ProposalStatus
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_note: str | None


class CorrectionIn(Contract):
    kind: service.CorrectionKind = Field(strict=False)
    investment_id: PushId
    reason: Note
    # A refund's payout account. Not sent for an unwind.
    destination_id: PushId | None = None


class CorrectionOut(Contract):
    id: PushId
    kind: service.CorrectionKind
    investment_id: PushId
    client_id: PushId
    amount_minor: int
    currency: Currency
    destination_id: PushId | None
    reason: str
    status: service.CorrectionStatus
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_note: str | None
    done_at: datetime | None
    entry_id: PushId | None
    payout_id: PushId | None
    failure_code: str | None


class RunRequestIn(Contract):
    kind: service.RunKind = Field(strict=False)
    run_date: date = Field(strict=False)
    reason: Note


class RunRequestOut(Contract):
    """A request to run a day's interest again, or today's maturities. It waits for a
    different person to approve it, and approving queues the run."""

    id: PushId
    kind: service.RunKind
    run_date: date
    reason: str
    status: Literal["proposed", "approved", "declined", "withdrawn"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_note: str | None
    job_id: PushId | None


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


class MaturityDayOut(Contract):
    day: date
    currency: Currency
    investments: int
    principal_minor: int
    rolled_over: int
    paid_out: int
    held: int
    # Not closed yet, by what the client chose.
    open_roll_over: int
    open_pay_out: int


class MaturitiesOut(Contract):
    """The maturity run around `today` (Kampala), and every term that needs a person."""

    today: date
    days: list[MaturityDayOut]
    # Past their maturity date and still running: the run failed them or has not run.
    not_closed: list[StaffInvestmentOut]
    # Matured, and the money waits in payouts for the client. `payout_refusal` says why.
    held: list[StaffInvestmentOut]


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
RatesChanger = Annotated[StaffSession, Depends(require_permission("rates.change"))]
# Deciding needs a passkey or app code from the last few minutes.
RatesApprover = Annotated[StaffSession, Depends(require_confirmed("rates.change"))]
ProductsChanger = Annotated[StaffSession, Depends(require_permission("products.change"))]
ProductsApprover = Annotated[StaffSession, Depends(require_confirmed("products.change"))]
InvestmentsViewer = Annotated[StaffSession, Depends(require_permission("investments.view"))]
MaturitiesViewer = Annotated[StaffSession, Depends(require_permission("maturities.view"))]
CorrectionsChanger = Annotated[
    StaffSession, Depends(require_permission("investment_corrections.change"))
]
CorrectionsApprover = Annotated[
    StaffSession, Depends(require_confirmed("investment_corrections.change"))
]


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


def _withdrawals_open(request: Request) -> bool:
    """Early withdrawal is off until both halves that pay it are wired (`early_withdrawal_open`)."""
    settings: Settings = request.app.state.settings
    return settings.early_withdrawal_open


def _client_out(view: service.ClientInvestment, request: Request) -> ClientInvestmentOut:
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
        waiting_minor=None if view.waiting is None else view.waiting.minor,
        can_withdraw=view.can_withdraw and _withdrawals_open(request),
        withdrawal_requested_at=view.withdrawal_requested_at,
        can_send=view.can_send,
        withdrawal_locked_until=view.withdrawal_locked_until,
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


def _product_change_out(view: service.ProductChangeView) -> ProductChangeOut:
    product = view.target
    first = (
        None
        if view.first_annual_rate_bps is None or view.first_tax_bps is None
        else RateVersion(
            business_date(view.decided_at or view.proposed_at),
            view.first_annual_rate_bps,
            view.first_tax_bps,
        )
    )
    return ProductChangeOut(
        id=view.id,
        action=view.action,
        product_code=product.code,
        name=product.name,
        kind=product.kind,
        currency=product.currency,
        term_days=product.term_days,
        minimum_minor=product.minimum.minor,
        interest_paid=product.interest_paid,
        product_status=product.status,
        first_rate=_rate_out(first),
        reason=view.reason,
        status=view.status,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_note=view.decision_note,
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


def _correction_out(correction: service.Correction) -> CorrectionOut:
    return CorrectionOut(
        id=correction.id,
        kind=correction.kind,
        investment_id=correction.investment_id,
        client_id=correction.client_id,
        amount_minor=correction.amount.minor,
        currency=correction.amount.currency,
        destination_id=correction.destination_id,
        reason=correction.reason,
        status=correction.status,
        proposed_by=correction.proposed_by,
        proposed_at=correction.proposed_at,
        decided_by=correction.decided_by,
        decided_at=correction.decided_at,
        decision_note=correction.decision_note,
        done_at=correction.done_at,
        entry_id=correction.entry_id,
        payout_id=correction.payout_id,
        failure_code=correction.failure_code,
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
    return _client_out(view, request)


@client_router.get("/investments")
async def client_investments(
    request: Request, client: CurrentClient, clock: AppClock
) -> list[ClientInvestmentOut]:
    async with _transaction(request, client.actor) as session:
        views = await service.client_investments(session, client.actor, clock)
    return [_client_out(view, request) for view in views]


@client_router.get("/investments/{investment_id}")
async def client_investment(
    request: Request, client: CurrentClient, clock: AppClock, investment_id: str
) -> ClientInvestmentOut:
    async with _transaction(request, client.actor) as session:
        view = await service.client_investment(session, client.actor, investment_id, clock)
    return _client_out(view, request)


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
    return _client_out(view, request)


@client_router.post(
    "/investments/{investment_id}/pay-out", responses={428: {"model": ErrorEnvelope}}
)
async def pay_out_matured(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    pins: Pins,
    investment_id: str,
    body: PayOutRequest,
) -> ClientInvestmentOut:
    """Send on the money of a matured investment that is waiting for a destination.

    Safe to repeat without an idempotency key: the payout's key is
    `maturity:<investment id>:<destination id>`, so the same destination asked twice is the
    same payout.

    428 `pin_required` from the phone app once it has a PIN, unless `pin_confirmation` is a
    fresh confirmation for `withdrawal`. It is used up only if the money is sent.
    """
    try:
        async with _transaction(request, client.actor) as session:
            await service.pay_out_matured(
                session,
                client.actor,
                investment_id,
                destination_id=body.destination_id,
                clock=clock,
                confirm=lambda: pins.spend(
                    client, "withdrawal", body.pin_confirmation, session=session
                ),
            )
            view = await service.client_investment(session, client.actor, investment_id, clock)
    except payouts.PayoutRefused as refused:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["destination_id"], code=refused.code)],
        ) from None
    return _client_out(view, request)


@client_router.post(
    "/investments/{investment_id}/withdraw", responses={428: {"model": ErrorEnvelope}}
)
async def withdraw_early(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    pins: Pins,
    key: IdempotencyKey,
    investment_id: str,
    body: WithdrawalRequest,
) -> ClientInvestmentOut:
    """Ask to withdraw a running investment early. It pays `value_minor` from the investment
    as shown: interest stops now. The money is sent once the dealing desk has funded it.

    Refused with `conflict` while early withdrawal is switched off, before anything is saved.

    428 `pin_required` from the phone app once it has a PIN, unless `pin_confirmation` is a
    fresh confirmation for `withdrawal`. It is used up only if the request is saved, and a
    replay of the same idempotency key asks for none.
    """
    if not _withdrawals_open(request):
        raise ApplicationError("conflict")
    async with _transaction(request, client.actor) as session:
        await service.request_withdrawal(
            session,
            client.actor,
            investment_id,
            destination_id=body.destination_id,
            idempotency_key=key,
            clock=clock,
            confirm=lambda: pins.spend(
                client, "withdrawal", body.pin_confirmation, session=session
            ),
        )
        view = await service.client_investment(session, client.actor, investment_id, clock)
    return _client_out(view, request)


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
    request: Request, staff: RatesChanger, clock: AppClock, body: ProposalIn
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
    request: Request, staff: RatesApprover, clock: AppClock, proposal_id: str
) -> ProposalOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.approve_rate(session, staff.actor, proposal_id, clock)
    return _proposal_out(view)


@staff_router.post("/rate-proposals/{proposal_id}/send-back")
async def staff_send_back_rate(
    request: Request, staff: RatesChanger, clock: AppClock, proposal_id: str, body: SendBack
) -> ProposalOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.send_back_rate(session, staff.actor, proposal_id, body.note, clock)
    return _proposal_out(view)


@staff_router.post("/rate-proposals/{proposal_id}/withdraw")
async def staff_withdraw_rate(
    request: Request, staff: RatesChanger, clock: AppClock, proposal_id: str
) -> ProposalOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.withdraw_rate(session, staff.actor, proposal_id, clock)
    return _proposal_out(view)


@staff_router.get("/product-changes")
async def staff_product_changes(
    request: Request, staff: ProductsViewer, status: ProposalStatus | None = None
) -> list[ProductChangeOut]:
    """New products, new terms, pauses and openings, newest first."""
    async with _transaction(request, staff.actor) as session:
        views = await service.list_product_changes(session, staff.actor, status=status)
    return [_product_change_out(view) for view in views]


@staff_router.post("/product-changes/new-product", status_code=201)
async def staff_propose_new_product(
    request: Request, staff: ProductsChanger, clock: AppClock, body: NewProductIn
) -> ProductChangeOut:
    """Propose a new product. A second member of staff must approve it, and it starts as a draft."""
    new = service.NewProduct(
        code=body.code,
        name=body.name,
        kind=body.kind,
        currency=body.currency,
        term_days=body.term_days,
        minimum_minor=body.minimum_minor,
        interest_paid=body.interest_paid,
        annual_rate_bps=body.annual_rate_bps,
        tax_bps=body.tax_bps,
    )
    async with _transaction(request, staff.actor) as session:
        view = await service.propose_new_product(session, staff.actor, new, body.reason, clock)
    return _product_change_out(view)


@staff_router.post("/product-changes/new-terms", status_code=201)
async def staff_propose_new_terms(
    request: Request, staff: ProductsChanger, clock: AppClock, body: NewTermsIn
) -> ProductChangeOut:
    """Propose new terms for a product. New investments take them once a second person approves."""
    terms = service.NewTerms(
        name=body.name,
        kind=body.kind,
        term_days=body.term_days,
        minimum_minor=body.minimum_minor,
        interest_paid=body.interest_paid,
    )
    async with _transaction(request, staff.actor) as session:
        view = await service.propose_new_terms(
            session, staff.actor, body.product_code, terms, body.reason, clock
        )
    return _product_change_out(view)


@staff_router.post("/product-changes/status", status_code=201)
async def staff_propose_status(
    request: Request, staff: ProductsChanger, clock: AppClock, body: StatusChangeIn
) -> ProductChangeOut:
    """Propose opening or pausing a product. A second member of staff must approve it."""
    async with _transaction(request, staff.actor) as session:
        view = await service.propose_status(
            session, staff.actor, body.product_code, ProductStatus(body.to), body.reason, clock
        )
    return _product_change_out(view)


@staff_router.post("/product-changes/{change_id}/approve")
async def staff_approve_product_change(
    request: Request, staff: ProductsApprover, clock: AppClock, change_id: str
) -> ProductChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.approve_product_change(session, staff.actor, change_id, clock)
    return _product_change_out(view)


@staff_router.post("/product-changes/{change_id}/send-back")
async def staff_send_back_product_change(
    request: Request, staff: ProductsChanger, clock: AppClock, change_id: str, body: SendBack
) -> ProductChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.send_back_product_change(
            session, staff.actor, change_id, body.note, clock
        )
    return _product_change_out(view)


@staff_router.post("/product-changes/{change_id}/withdraw")
async def staff_withdraw_product_change(
    request: Request, staff: ProductsChanger, clock: AppClock, change_id: str
) -> ProductChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.withdraw_product_change(session, staff.actor, change_id, clock)
    return _product_change_out(view)


@staff_router.get("/investment-corrections")
async def staff_corrections(
    request: Request,
    staff: InvestmentsViewer,
    status: service.CorrectionStatus | None = None,
    investment_id: PushId | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CorrectionOut]:
    async with _transaction(request, staff.actor) as session:
        found = await service.list_corrections(
            session, staff.actor, status=status, investment_id=investment_id, limit=limit
        )
    return [_correction_out(correction) for correction in found]


async def _correction_command(
    request: Request,
    staff: StaffSession,
    clock: Clock,
    *,
    operation: str,
    key: str,
    body: dict[str, object],
    command: Callable[[AsyncSession], Awaitable[service.Correction]],
) -> CorrectionOut:
    """Run one correction command once per key, and answer with the correction as it is now."""
    async with _transaction(request, staff.actor) as session:

        async def run() -> Receipt:
            correction = await command(session)
            return Receipt(200, {"correction_id": correction.id})

        outcome = await run_once(
            session,
            actor=staff.actor,
            operation=operation,
            key=key,
            request=body,
            now=clock.now(),
            command=run,
        )
        correction_id = outcome.receipt.response.get("correction_id")
        assert isinstance(correction_id, str)
        return _correction_out(await service.get_correction(session, staff.actor, correction_id))


@staff_router.post("/investment-corrections", status_code=201)
async def staff_propose_correction(
    request: Request,
    staff: CorrectionsChanger,
    clock: AppClock,
    key: IdempotencyKey,
    body: CorrectionIn,
) -> CorrectionOut:
    """Propose undoing an investment or refunding money awaiting start. A second person must
    approve it before anything moves."""
    return await _correction_command(
        request,
        staff,
        clock,
        operation="investments.propose_correction",
        key=key,
        body=body.model_dump(mode="json"),
        command=lambda session: service.propose_correction(
            session,
            staff.actor,
            kind=body.kind,
            investment_id=body.investment_id,
            reason=body.reason,
            destination_id=body.destination_id,
            clock=clock,
        ),
    )


@staff_router.post("/investment-corrections/{correction_id}/approve")
async def staff_approve_correction(
    request: Request,
    staff: CorrectionsApprover,
    clock: AppClock,
    key: IdempotencyKey,
    correction_id: PushId,
) -> CorrectionOut:
    """Approve someone else's correction. The worker then moves the money."""
    return await _correction_command(
        request,
        staff,
        clock,
        operation="investments.approve_correction",
        key=key,
        body={"correction_id": correction_id},
        command=lambda session: service.approve_correction(
            session, staff.actor, correction_id, clock
        ),
    )


@staff_router.post("/investment-corrections/{correction_id}/send-back")
async def staff_send_back_correction(
    request: Request,
    staff: CorrectionsChanger,
    clock: AppClock,
    key: IdempotencyKey,
    correction_id: PushId,
    body: SendBack,
) -> CorrectionOut:
    return await _correction_command(
        request,
        staff,
        clock,
        operation="investments.send_back_correction",
        key=key,
        body={"correction_id": correction_id, "note": body.note},
        command=lambda session: service.send_back_correction(
            session, staff.actor, correction_id, body.note, clock
        ),
    )


@staff_router.post("/investment-corrections/{correction_id}/withdraw")
async def staff_withdraw_correction(
    request: Request,
    staff: CorrectionsChanger,
    clock: AppClock,
    key: IdempotencyKey,
    correction_id: PushId,
) -> CorrectionOut:
    return await _correction_command(
        request,
        staff,
        clock,
        operation="investments.withdraw_correction",
        key=key,
        body={"correction_id": correction_id},
        command=lambda session: service.withdraw_correction(
            session, staff.actor, correction_id, clock
        ),
    )


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


@staff_router.get("/maturities")
async def staff_maturities(
    request: Request, staff: MaturitiesViewer, clock: AppClock
) -> MaturitiesOut:
    """The last week and next two weeks of maturities, and the terms that need a person."""
    async with _transaction(request, staff.actor) as session:
        found = await service.maturities(session, staff.actor, clock)
    return MaturitiesOut(
        today=found.today,
        days=[
            MaturityDayOut(
                day=day.day,
                currency=day.currency,
                investments=day.investments,
                principal_minor=day.principal.minor,
                rolled_over=day.rolled_over,
                paid_out=day.paid_out,
                held=day.held,
                open_roll_over=day.open_roll_over,
                open_pay_out=day.open_pay_out,
            )
            for day in found.days
        ],
        not_closed=[_staff_investment_out(investment) for investment in found.not_closed],
        held=[_staff_investment_out(investment) for investment in found.held],
    )


def _run_request_out(request: service.RunRequest) -> RunRequestOut:
    return RunRequestOut(
        id=request.id,
        kind=request.kind,
        run_date=request.run_date,
        reason=request.reason,
        status=request.status,
        proposed_by=request.proposed_by,
        proposed_at=request.proposed_at,
        decided_by=request.decided_by,
        decided_at=request.decided_at,
        decision_note=request.decision_note,
        job_id=request.job_id,
    )


@staff_router.get("/investment-run-requests")
async def staff_run_requests(
    request: Request,
    staff: InvestmentsViewer,
    status: Literal["proposed", "approved", "declined", "withdrawn"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunRequestOut]:
    """Requests to run interest or maturities again, newest first."""
    async with _transaction(request, staff.actor) as session:
        found = await service.list_runs(session, staff.actor, status=status, limit=limit)
    return [_run_request_out(found_request) for found_request in found]


async def _run_command(
    request: Request,
    staff: StaffSession,
    clock: Clock,
    *,
    operation: str,
    key: str,
    body: dict[str, object],
    command: Callable[[AsyncSession], Awaitable[service.RunRequest]],
) -> RunRequestOut:
    """Run one run-request command once per key, and answer with the request as it is now."""
    async with _transaction(request, staff.actor) as session:

        async def run() -> Receipt:
            found = await command(session)
            return Receipt(200, {"run_request_id": found.id})

        outcome = await run_once(
            session,
            actor=staff.actor,
            operation=operation,
            key=key,
            request=body,
            now=clock.now(),
            command=run,
        )
        request_id = outcome.receipt.response.get("run_request_id")
        assert isinstance(request_id, str)
        return _run_request_out(await service.get_run(session, staff.actor, request_id))


@staff_router.post("/investment-run-requests", status_code=201)
async def staff_propose_run(
    request: Request,
    staff: CorrectionsChanger,
    clock: AppClock,
    key: IdempotencyKey,
    body: RunRequestIn,
) -> RunRequestOut:
    """Ask for a day's interest run, or today's maturity run, again. A second person must
    approve it before it runs."""
    return await _run_command(
        request,
        staff,
        clock,
        operation="investments.propose_run",
        key=key,
        body=body.model_dump(mode="json"),
        command=lambda session: service.propose_run(
            session,
            staff.actor,
            kind=body.kind,
            run_date=body.run_date,
            reason=body.reason,
            clock=clock,
        ),
    )


@staff_router.post("/investment-run-requests/{request_id}/approve")
async def staff_approve_run(
    request: Request,
    staff: CorrectionsApprover,
    clock: AppClock,
    key: IdempotencyKey,
    request_id: PushId,
) -> RunRequestOut:
    """Approve someone else's request. The run is queued at once."""
    return await _run_command(
        request,
        staff,
        clock,
        operation="investments.approve_run",
        key=key,
        body={"run_request_id": request_id},
        command=lambda session: service.approve_run(session, staff.actor, request_id, clock),
    )


@staff_router.post("/investment-run-requests/{request_id}/decline")
async def staff_decline_run(
    request: Request,
    staff: CorrectionsApprover,
    clock: AppClock,
    key: IdempotencyKey,
    request_id: PushId,
    body: SendBack,
) -> RunRequestOut:
    return await _run_command(
        request,
        staff,
        clock,
        operation="investments.decline_run",
        key=key,
        body={"run_request_id": request_id, "note": body.note},
        command=lambda session: service.decline_run(
            session, staff.actor, request_id, body.note, clock
        ),
    )


@staff_router.post("/investment-run-requests/{request_id}/withdraw")
async def staff_withdraw_run(
    request: Request,
    staff: CorrectionsChanger,
    clock: AppClock,
    key: IdempotencyKey,
    request_id: PushId,
) -> RunRequestOut:
    return await _run_command(
        request,
        staff,
        clock,
        operation="investments.withdraw_run",
        key=key,
        body={"run_request_id": request_id},
        command=lambda session: service.withdraw_run(session, staff.actor, request_id, clock),
    )


routers = (public_router, client_router, staff_router)
