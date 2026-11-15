"""Bonds over HTTP: the issues on offer and their prices, and the staff changes to them.

* `/public/bonds`: the website. Live and paused issues with their coupon after tax and price.
  Cached for five minutes and readable from any origin.
* `/client/bonds`: the same for a signed-in client, with a quote for a face value.
* `/client/bond-holdings`: buying a face value at today's price, and the client's holdings.
  Paying for one is in the payments module, under `/client/bond-holdings/{holding_id}/...`.
* `/staff/bonds`, `/staff/bond-changes`: issues with their tax, and proposing and deciding new
  issues, prices, pauses and openings, a second person enforced in the database.
* `/staff/bond-holdings`: every client's holdings, for the books and the dealing desk.
* `/client/bond-repayments`, `/staff/bond-repayments`: holdings repaid at maturity, the face
  value and the last coupon, and sending on one that waits for a destination.
* `/client/bond-holdings/{id}/sale`, `/client/bond-sales`, `/staff/bond-sales`: a client's ask
  to sell a holding early, priced by the dealing desk and approved by a second person.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.bonds import catalogue, coupons, holdings, maturity, sales
from app.modules.bonds.domain.changes import BondChangeAction, BondChangeStatus
from app.modules.bonds.domain.holdings import HoldingStatus
from app.modules.bonds.domain.issues import (
    BondIssue,
    BondStatus,
    ClientQuote,
    Quote,
    default_tax_bps,
)
from app.modules.bonds.domain.sales import SaleStatus, allowed_steps
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.pins_api import Pins
from app.modules.identity.service import StaffSession
from app.modules.payouts import payouts
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

PUBLIC_CACHE = "public, max-age=300"
MAX_FACE_MINOR = 10**15

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

Code = Annotated[str, Field(min_length=1, max_length=24, pattern=r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$")]
Isin = Annotated[str, Field(min_length=12, max_length=12, pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")]
Name = Annotated[str, Field(min_length=1, max_length=80)]
Note = Annotated[str, Field(min_length=1, max_length=500)]
Bps = Annotated[int, Field(ge=0, le=9999)]
Price = Annotated[int, Field(ge=500_000, le=1_500_000)]
FaceMinor = Annotated[int, Field(ge=1, le=MAX_FACE_MINOR)]
CurrencyIn = Annotated[Currency, Field(strict=False)]
DateIn = Annotated[date, Field(strict=False)]
StatusIn = Annotated[BondStatus, Field(strict=False)]
PinConfirmation = Annotated[str | None, Field(max_length=200)]
SaleStep = Literal["price", "approve", "send_back", "withdraw"]


# Contracts --------------------------------------------------------------------------------------


class BondOut(Contract):
    """An issue as a client and the website see it. The coupon is after tax."""

    code: str
    isin: str
    name: str
    currency: Currency
    # "12.80": the yearly coupon after tax, as a label. No amount is worked out from it.
    coupon_percent: str
    issue_date: date
    maturity_date: date
    years: int
    next_coupon_date: date | None
    coupons_left: int
    minimum_face_minor: int
    face_step_minor: int
    # Per 100 of face value, 100.0000 = 1000000. None until staff set one.
    price: int | None
    status: BondStatus
    can_buy: bool


class PublicBonds(Contract):
    updated_on: date
    bonds: list[BondOut]


class BondQuoteOut(Contract):
    """What buying a face value costs today and pays to maturity, after tax."""

    code: str
    currency: Currency
    face_minor: int
    price: int
    # Interest earned since the last coupon, paid on top and returned in the next coupon.
    accrued_minor: int
    cost_minor: int
    coupon_minor: int
    coupons_left: int
    next_coupon_date: date
    total_back_minor: int
    gain_minor: int


class StaffBondOut(Contract):
    code: str
    isin: str
    name: str
    currency: Currency
    coupon_bps: int
    tax_bps: int
    issue_date: date
    maturity_date: date
    years: int
    minimum_face_minor: int
    face_step_minor: int
    status: BondStatus
    version: int
    price: int | None
    priced_at: datetime | None
    # The tax Bank of Uganda states for a bond of this length, for comparison.
    usual_tax_bps: int


class StaffQuoteOut(Contract):
    code: str
    currency: Currency
    face_minor: int
    price: int
    clean_minor: int
    accrued_minor: int
    cost_minor: int
    coupon_net_minor: int
    coupons_left: int
    next_coupon_date: date
    total_back_minor: int


class BondTermsOut(Contract):
    isin: str
    name: str
    currency: Currency
    coupon_bps: int
    tax_bps: int
    issue_date: date
    maturity_date: date
    minimum_face_minor: int
    face_step_minor: int


class BondChangeOut(Contract):
    id: PushId
    action: BondChangeAction
    code: str
    # A new issue as it will be. None for a price, pause or opening.
    terms: BondTermsOut | None
    price: int | None
    reason: str
    status: BondChangeStatus
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_note: str | None


class NewBondIn(Contract):
    """A new issue with its first price. It is created as a draft once a second person
    approves. Opening it to clients is a change of its own."""

    code: Code
    isin: Isin
    name: Name
    currency: CurrencyIn
    coupon_bps: Annotated[int, Field(ge=1, le=5000)]
    # Withholding tax on coupons. Leave out for Bank of Uganda's: 20% under ten years, 10% at
    # ten years or more.
    tax_bps: Bps | None = None
    issue_date: DateIn
    maturity_date: DateIn
    minimum_face_minor: FaceMinor = 100_000
    face_step_minor: FaceMinor = 100_000
    price: Price
    reason: Note
    # A product type of the bond model, from `GET /staff/product-types`. Left out, Treasury bond.
    type_code: Code | None = None


class BondPriceIn(Contract):
    code: Code
    price: Price
    reason: Note


class BondStatusIn(Contract):
    code: Code
    to: StatusIn
    reason: Note


class SendBack(Contract):
    note: Note


class BuyBondIn(Contract):
    code: Code
    face_minor: FaceMinor
    currency: CurrencyIn


class BondHoldingOut(Contract):
    """A client's holding as they see it. Every figure is after tax."""

    id: PushId
    code: str
    name: str
    currency: Currency
    face_minor: int
    price: int
    accrued_minor: int
    # What the client pays, or paid: the clean amount and the accrued interest.
    cost_minor: int
    status: HoldingStatus
    created_at: datetime
    # Until when an unpaid holding may be paid. None once it is not unpaid.
    pay_by: datetime | None
    start_date: date | None
    maturity_date: date
    coupon_minor: int
    coupons_left: int
    next_coupon_date: date | None
    # The coupons still to come and the face value at maturity.
    still_to_come_minor: int
    # Where coupons are paid. None until the client chooses: coupons then wait for one.
    payout_destination_id: PushId | None


class CouponDestinationIn(Contract):
    # One of the client's approved payout destinations, or None to clear the choice.
    destination_id: PushId | None


class CouponPayOutIn(Contract):
    destination_id: PushId
    pin_confirmation: PinConfirmation = None


class BondCouponOut(Contract):
    """A coupon paid to a client's holding, after tax."""

    id: PushId
    holding_id: PushId
    code: str
    name: str
    coupon_date: date
    currency: Currency
    amount_minor: int
    # `paid_out` once a payout carries it, `held` while it waits for a destination.
    status: str
    payout_id: PushId | None
    # Why it waits: `no_destination`, or the payout's refusal.
    held_because: str | None


class StaffBondCouponOut(BondCouponOut):
    client_id: PushId
    gross_minor: int
    tax_minor: int


class BondRepaymentOut(Contract):
    """A holding repaid at maturity: the face value and the last coupon, after tax."""

    id: PushId
    holding_id: PushId
    code: str
    name: str
    repaid_on: date
    currency: Currency
    face_minor: int
    # The last coupon after tax.
    coupon_minor: int
    # What the client is paid: the face value and the last coupon.
    amount_minor: int
    # `paid_out` once a payout carries it, `held` while it waits for a destination.
    status: str
    payout_id: PushId | None
    # Why it waits: `no_destination`, or the payout's refusal.
    held_because: str | None


class StaffBondRepaymentOut(BondRepaymentOut):
    client_id: PushId
    # What the client paid for the holding.
    cost_minor: int
    coupon_gross_minor: int
    coupon_tax_minor: int


class StaffBondHoldingOut(Contract):
    id: PushId
    client_id: PushId
    code: str
    currency: Currency
    face_minor: int
    price: int
    accrued_minor: int
    cost_minor: int
    quoted_on: date
    status: HoldingStatus
    created_at: datetime
    pay_by: datetime
    start_date: date | None


class SellBondIn(Contract):
    # One of the client's approved payout destinations, for the proceeds.
    destination_id: PushId
    pin_confirmation: PinConfirmation = None


class BondSaleOut(Contract):
    """A client's ask to sell a holding early, and what it paid, after tax."""

    id: PushId
    holding_id: PushId
    code: str
    name: str
    currency: Currency
    face_minor: int
    requested_at: datetime
    # `requested`, `priced` and `approved` while the desk sells it, then `sold`. `cancelled` by
    # the client, or `closed` when the bond matured first and was repaid instead.
    status: SaleStatus
    destination_id: PushId
    # The price it sold at, per 100 of face value, and the day it settled, once sold.
    price: int | None
    sold_on: date | None
    # What the client is paid: the price and the accrued interest, after tax. Once sold.
    amount_minor: int | None
    sold_at: datetime | None
    # `paid_out` once a payout carries it, `held` while it waits for a destination.
    payout_status: str | None
    payout_id: PushId | None
    held_because: str | None


class StaffBondSaleOut(Contract):
    id: PushId
    holding_id: PushId
    client_id: PushId
    code: str
    name: str
    currency: Currency
    face_minor: int
    # What the client paid for the holding.
    cost_minor: int
    destination_id: PushId
    requested_at: datetime
    status: SaleStatus
    price: int | None
    sold_on: date | None
    # The dealing desk's reference for the trade.
    reason: str | None
    priced_by: PushId | None
    priced_at: datetime | None
    decided_by: PushId | None
    decided_at: datetime | None
    # Why it was last sent back.
    note: str | None
    clean_minor: int | None
    accrued_minor: int | None
    tax_minor: int | None
    amount_minor: int | None
    sold_at: datetime | None
    payout_status: str | None
    payout_id: PushId | None
    held_because: str | None
    closed_at: datetime | None
    # What the reader may do with it now: `price`, `approve`, `send_back` or `withdraw`.
    allowed: list[SaleStep]


class PriceSaleIn(Contract):
    price: Price
    sold_on: DateIn
    reason: Note


# Wiring -----------------------------------------------------------------------------------------


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
DeskViewer = Annotated[StaffSession, Depends(require_permission("dealing_desk.view"))]
DeskChanger = Annotated[StaffSession, Depends(require_permission("dealing_orders.change"))]
DeskApprover = Annotated[StaffSession, Depends(require_confirmed("dealing_orders.change"))]
ProductsViewer = Annotated[StaffSession, Depends(require_permission("products.view"))]
ProductsChanger = Annotated[StaffSession, Depends(require_permission("products.change"))]
ProductsApprover = Annotated[StaffSession, Depends(require_confirmed("products.change"))]
InvestmentsViewer = Annotated[StaffSession, Depends(require_permission("investments.view"))]


@asynccontextmanager
async def _transaction(request: Request, actor: Actor | None) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    if actor is None:
        async with database.transaction() as session:
            yield session
    else:
        async with database.actor_transaction(actor) as session:
            yield session


def _public(response: Response) -> None:
    response.headers["Cache-Control"] = PUBLIC_CACHE
    response.headers["Access-Control-Allow-Origin"] = "*"


def _bond_out(bond: catalogue.ClientIssue) -> BondOut:
    return BondOut(
        code=bond.code,
        isin=bond.isin,
        name=bond.name,
        currency=bond.currency,
        coupon_percent=bond.coupon_percent,
        issue_date=bond.issue_date,
        maturity_date=bond.maturity_date,
        years=bond.years,
        next_coupon_date=bond.next_coupon_date,
        coupons_left=bond.coupons_left,
        minimum_face_minor=bond.minimum_face.minor,
        face_step_minor=bond.face_step.minor,
        price=bond.price,
        status=bond.status,
        can_buy=bond.can_buy,
    )


def _quote_out(code: str, found: ClientQuote) -> BondQuoteOut:
    return BondQuoteOut(
        code=code,
        currency=found.face.currency,
        face_minor=found.face.minor,
        price=found.price,
        accrued_minor=found.accrued.minor,
        cost_minor=found.cost.minor,
        coupon_minor=found.coupon_net.minor,
        coupons_left=found.coupons_left,
        next_coupon_date=found.next_coupon_date,
        total_back_minor=found.total_back.minor,
        gain_minor=found.gain.minor,
    )


def _staff_out(view: catalogue.IssueView) -> StaffBondOut:
    issue = view.issue
    return StaffBondOut(
        code=issue.code,
        isin=issue.isin,
        name=issue.name,
        currency=issue.currency,
        coupon_bps=issue.coupon_bps,
        tax_bps=issue.tax_bps,
        issue_date=issue.issue_date,
        maturity_date=issue.maturity_date,
        years=issue.years,
        minimum_face_minor=issue.minimum_face.minor,
        face_step_minor=issue.face_step.minor,
        status=issue.status,
        version=view.version,
        price=view.price,
        priced_at=view.priced_at,
        usual_tax_bps=default_tax_bps(issue.issue_date, issue.maturity_date),
    )


def _terms_out(issue: BondIssue) -> BondTermsOut:
    return BondTermsOut(
        isin=issue.isin,
        name=issue.name,
        currency=issue.currency,
        coupon_bps=issue.coupon_bps,
        tax_bps=issue.tax_bps,
        issue_date=issue.issue_date,
        maturity_date=issue.maturity_date,
        minimum_face_minor=issue.minimum_face.minor,
        face_step_minor=issue.face_step.minor,
    )


def _change_out(view: catalogue.ChangeView) -> BondChangeOut:
    return BondChangeOut(
        id=view.id,
        action=view.action,
        code=view.code,
        terms=_terms_out(view.target) if view.target is not None else None,
        price=view.price,
        reason=view.reason,
        status=view.status,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_note=view.decision_note,
    )


def _staff_quote_out(code: str, found: Quote) -> StaffQuoteOut:
    return StaffQuoteOut(
        code=code,
        currency=found.face.currency,
        face_minor=found.face.minor,
        price=found.price,
        clean_minor=found.clean.minor,
        accrued_minor=found.accrued.minor,
        cost_minor=found.cost.minor,
        coupon_net_minor=found.coupon_net.minor,
        coupons_left=found.coupons_left,
        next_coupon_date=found.next_coupon_date,
        total_back_minor=found.total_back.minor,
    )


# The website ------------------------------------------------------------------------------------

public_router = APIRouter(prefix="/public", tags=["public bonds"], responses=_REFUSALS)


@public_router.get("/bonds")
async def public_bonds(request: Request, response: Response, clock: AppClock) -> PublicBonds:
    async with _transaction(request, None) as session:
        bonds = await catalogue.client_issues(session, clock)
    _public(response)
    return PublicBonds(updated_on=business_date(clock.now()), bonds=[_bond_out(b) for b in bonds])


@public_router.get("/bonds/{code}/quote")
async def public_bond_quote(
    request: Request,
    response: Response,
    clock: AppClock,
    code: str,
    face_minor: Annotated[int, Query(ge=1, le=MAX_FACE_MINOR)],
) -> BondQuoteOut:
    async with _transaction(request, None) as session:
        found = await catalogue.client_bond_quote(session, code, face_minor, clock)
    _public(response)
    return _quote_out(code, found)


# The client -------------------------------------------------------------------------------------

client_router = APIRouter(prefix="/client", tags=["client bonds"], responses=_REFUSALS)


@client_router.get("/bonds")
async def client_bonds(request: Request, client: CurrentClient, clock: AppClock) -> list[BondOut]:
    async with _transaction(request, client.actor) as session:
        return [_bond_out(b) for b in await catalogue.client_issues(session, clock)]


@client_router.get("/bonds/{code}")
async def client_bond(
    request: Request, client: CurrentClient, clock: AppClock, code: str
) -> BondOut:
    async with _transaction(request, client.actor) as session:
        return _bond_out(await catalogue.client_issue(session, code, clock))


@client_router.get("/bonds/{code}/quote")
async def client_bond_quote(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    code: str,
    face_minor: Annotated[int, Query(ge=1, le=MAX_FACE_MINOR)],
) -> BondQuoteOut:
    async with _transaction(request, client.actor) as session:
        found = await catalogue.client_bond_quote(session, code, face_minor, clock)
    return _quote_out(code, found)


def _holding_out(view: holdings.ClientHolding) -> BondHoldingOut:
    return BondHoldingOut(
        id=view.id,
        code=view.code,
        name=view.name,
        currency=view.face.currency,
        face_minor=view.face.minor,
        price=view.price,
        accrued_minor=view.accrued.minor,
        cost_minor=view.cost.minor,
        status=view.status,
        created_at=view.created_at,
        pay_by=view.pay_by,
        start_date=view.start_date,
        maturity_date=view.maturity_date,
        coupon_minor=view.coupon_net.minor,
        coupons_left=view.coupons_left,
        next_coupon_date=view.next_coupon_date,
        still_to_come_minor=view.still_to_come.minor,
        payout_destination_id=view.payout_destination_id,
    )


@client_router.post("/bond-holdings", status_code=201)
async def client_buy_bond(
    request: Request, client: CurrentClient, clock: AppClock, key: IdempotencyKey, body: BuyBondIn
) -> BondHoldingOut:
    """Buy a face value of a live issue at today's price, as the quote showed. Nothing is paid
    yet: pay the cost within `pay_by` with `POST /client/bond-holdings/{id}/payments/...`.

    422 with a field code, as the quote refuses. 409 with five purchases already waiting.
    """
    async with _transaction(request, client.actor) as session:
        holding = await holdings.request_purchase(
            session,
            client.actor,
            idempotency_key=key,
            code=body.code,
            face=Money(body.face_minor, body.currency),
            clock=clock,
        )
        return _holding_out(
            await holdings.client_holding(session, client.client_id, holding.id, clock)
        )


@client_router.get("/bond-holdings")
async def client_bond_holdings(
    request: Request, client: CurrentClient, clock: AppClock
) -> list[BondHoldingOut]:
    """The client's holdings, unpaid and active, newest first."""
    async with _transaction(request, client.actor) as session:
        found = await holdings.client_holdings(session, client.client_id, clock)
    return [_holding_out(h) for h in found]


@client_router.get("/bond-holdings/{holding_id}")
async def client_bond_holding(
    request: Request, client: CurrentClient, clock: AppClock, holding_id: PushId
) -> BondHoldingOut:
    async with _transaction(request, client.actor) as session:
        return _holding_out(
            await holdings.client_holding(session, client.client_id, holding_id, clock)
        )


@client_router.put("/bond-holdings/{holding_id}/coupon-destination")
async def client_choose_coupon_destination(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    holding_id: PushId,
    body: CouponDestinationIn,
) -> BondHoldingOut:
    """Where this holding's coupons are paid: one of the client's approved payout destinations.
    Coupons paid before this was chosen wait, and are sent on one at a time."""
    async with _transaction(request, client.actor) as session:
        await coupons.choose_destination(
            session, client.actor, holding_id, destination_id=body.destination_id, clock=clock
        )
        return _holding_out(
            await holdings.client_holding(session, client.client_id, holding_id, clock)
        )


def _coupon_out(view: coupons.CouponView) -> BondCouponOut:
    return BondCouponOut(
        id=view.id,
        holding_id=view.holding_id,
        code=view.code,
        name=view.name,
        coupon_date=view.coupon_date,
        currency=view.net.currency,
        amount_minor=view.net.minor,
        status=view.status,
        payout_id=view.payout_id,
        held_because=view.payout_refusal,
    )


@client_router.get("/bond-coupons")
async def client_bond_coupons(request: Request, client: CurrentClient) -> list[BondCouponOut]:
    """The client's coupons, newest first, after tax."""
    async with _transaction(request, client.actor) as session:
        found = await coupons.client_coupons(session, client.client_id)
    return [_coupon_out(c) for c in found]


@client_router.post("/bond-coupons/{coupon_id}/pay-out", responses={428: {"model": ErrorEnvelope}})
async def client_pay_out_coupon(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    pins: Pins,
    coupon_id: PushId,
    body: CouponPayOutIn,
) -> BondCouponOut:
    """Send on a coupon that is waiting for a destination.

    A repeat after it was sent on is refused with 409 `conflict`, never paid twice. 428
    `pin_required` from the phone app once it has a PIN, unless `pin_confirmation` is a fresh
    confirmation for `withdrawal`.
    """
    try:
        async with _transaction(request, client.actor) as session:
            view = await coupons.pay_out_held(
                session,
                client.actor,
                coupon_id,
                destination_id=body.destination_id,
                clock=clock,
                confirm=lambda: pins.spend(
                    client, "withdrawal", body.pin_confirmation, session=session
                ),
            )
    except payouts.PayoutRefused as refused:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["destination_id"], code=refused.code)],
        ) from None
    return _coupon_out(view)


def _repayment_out(view: maturity.RedemptionView) -> BondRepaymentOut:
    return BondRepaymentOut(
        id=view.id,
        holding_id=view.holding_id,
        code=view.code,
        name=view.name,
        repaid_on=view.redeemed_on,
        currency=view.amount.currency,
        face_minor=view.face.minor,
        coupon_minor=view.coupon_net.minor,
        amount_minor=view.amount.minor,
        status=view.status,
        payout_id=view.payout_id,
        held_because=view.payout_refusal,
    )


@client_router.get("/bond-repayments")
async def client_bond_repayments(request: Request, client: CurrentClient) -> list[BondRepaymentOut]:
    """The client's bonds repaid at maturity, newest first, after tax."""
    async with _transaction(request, client.actor) as session:
        found = await maturity.client_redemptions(session, client.client_id)
    return [_repayment_out(r) for r in found]


@client_router.post(
    "/bond-repayments/{repayment_id}/pay-out", responses={428: {"model": ErrorEnvelope}}
)
async def client_pay_out_repayment(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    pins: Pins,
    repayment_id: PushId,
    body: CouponPayOutIn,
) -> BondRepaymentOut:
    """Send on a repayment that is waiting for a destination.

    A repeat after it was sent on is refused with 409 `conflict`, never paid twice. 428
    `pin_required` from the phone app once it has a PIN, unless `pin_confirmation` is a fresh
    confirmation for `withdrawal`.
    """
    try:
        async with _transaction(request, client.actor) as session:
            view = await maturity.pay_out_held(
                session,
                client.actor,
                repayment_id,
                destination_id=body.destination_id,
                clock=clock,
                confirm=lambda: pins.spend(
                    client, "withdrawal", body.pin_confirmation, session=session
                ),
            )
    except payouts.PayoutRefused as refused:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["destination_id"], code=refused.code)],
        ) from None
    return _repayment_out(view)


def _money(value: Money | None) -> int | None:
    return None if value is None else value.minor


def _sale_out(view: sales.SaleView) -> BondSaleOut:
    sold = view.status is SaleStatus.SOLD
    return BondSaleOut(
        id=view.id,
        holding_id=view.holding_id,
        code=view.code,
        name=view.name,
        currency=view.face.currency,
        face_minor=view.face.minor,
        requested_at=view.requested_at,
        status=view.status,
        destination_id=view.destination_id,
        price=view.price if sold else None,
        sold_on=view.sold_on if sold else None,
        amount_minor=_money(view.amount),
        sold_at=view.settled_at,
        payout_status=view.payout_status,
        payout_id=view.payout_id,
        held_because=view.payout_refusal,
    )


def _staff_sale_out(view: sales.SaleView, staff: StaffSession) -> StaffBondSaleOut:
    steps = allowed_steps(
        view.status,
        view.priced_by,
        staff.staff_id,
        may_change="dealing_orders.change" in staff.permissions,
    )
    return StaffBondSaleOut(
        id=view.id,
        holding_id=view.holding_id,
        client_id=view.client_id,
        code=view.code,
        name=view.name,
        currency=view.face.currency,
        face_minor=view.face.minor,
        cost_minor=view.cost.minor,
        destination_id=view.destination_id,
        requested_at=view.requested_at,
        status=view.status,
        price=view.price,
        sold_on=view.sold_on,
        reason=view.reason,
        priced_by=view.priced_by,
        priced_at=view.priced_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        note=view.note,
        clean_minor=_money(view.clean),
        accrued_minor=_money(view.accrued),
        tax_minor=_money(view.tax),
        amount_minor=_money(view.amount),
        sold_at=view.settled_at,
        payout_status=view.payout_status,
        payout_id=view.payout_id,
        held_because=view.payout_refusal,
        closed_at=view.closed_at,
        allowed=[cast(SaleStep, step) for step in steps],
    )


def _refused_destination(refused: payouts.PayoutRefused) -> ApplicationError:
    return ApplicationError(
        "validation_failed",
        fields=[FieldProblem(field_path=["destination_id"], code=refused.code)],
    )


@client_router.post(
    "/bond-holdings/{holding_id}/sale", status_code=201, responses={428: {"model": ErrorEnvelope}}
)
async def client_sell_bond(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    pins: Pins,
    key: IdempotencyKey,
    holding_id: PushId,
    body: SellBondIn,
) -> BondSaleOut:
    """Ask Finvest to sell the whole holding early, at the market price the dealing desk gets.
    Nothing moves until two members of staff have recorded and approved the sale.

    422 `holding_id` / `holding_not_active` or `matured`; 409 `holding_id` /
    `sale_already_asked`; 422 `destination_id` when it cannot be paid. 428 `pin_required` from
    the phone app once it has a PIN, unless `pin_confirmation` is a fresh confirmation for
    `withdrawal`.
    """
    async with _transaction(request, client.actor) as session:
        view = await sales.request_sale(
            session,
            client.actor,
            holding_id,
            idempotency_key=key,
            destination_id=body.destination_id,
            clock=clock,
            confirm=lambda: pins.spend(
                client, "withdrawal", body.pin_confirmation, session=session
            ),
        )
    return _sale_out(view)


@client_router.get("/bond-sales")
async def client_bond_sales(request: Request, client: CurrentClient) -> list[BondSaleOut]:
    """The client's asks to sell, newest first, with what each paid after tax."""
    async with _transaction(request, client.actor) as session:
        found = await sales.client_sales(session, client.client_id)
    return [_sale_out(v) for v in found]


@client_router.post("/bond-sales/{sale_id}/cancel")
async def client_cancel_bond_sale(
    request: Request, client: CurrentClient, clock: AppClock, sale_id: PushId
) -> BondSaleOut:
    """Cancel an ask to sell. 409 once the dealing desk has priced it."""
    async with _transaction(request, client.actor) as session:
        view = await sales.cancel_sale(session, client.actor, sale_id, clock)
    return _sale_out(view)


@client_router.post("/bond-sales/{sale_id}/pay-out", responses={428: {"model": ErrorEnvelope}})
async def client_pay_out_sale(
    request: Request,
    client: CurrentClient,
    clock: AppClock,
    pins: Pins,
    sale_id: PushId,
    body: CouponPayOutIn,
) -> BondSaleOut:
    """Send on the proceeds of a sale that wait for a destination. 409 when they do not wait."""
    try:
        async with _transaction(request, client.actor) as session:
            view = await sales.pay_out_held(
                session,
                client.actor,
                sale_id,
                destination_id=body.destination_id,
                clock=clock,
                confirm=lambda: pins.spend(
                    client, "withdrawal", body.pin_confirmation, session=session
                ),
            )
    except payouts.PayoutRefused as refused:
        raise _refused_destination(refused) from None
    return _sale_out(view)


# Staff ------------------------------------------------------------------------------------------

staff_router = APIRouter(prefix="/staff", tags=["staff bonds"], responses=_REFUSALS)


@staff_router.get("/bonds")
async def staff_bonds(request: Request, staff: ProductsViewer) -> list[StaffBondOut]:
    """Every issue, drafts included, with its tax and the price in force."""
    async with _transaction(request, staff.actor) as session:
        return [_staff_out(v) for v in await catalogue.staff_issues(session, staff.actor)]


@staff_router.get("/bonds/{code}/quote")
async def staff_bond_quote(
    request: Request,
    staff: ProductsViewer,
    clock: AppClock,
    code: str,
    face_minor: Annotated[int, Query(ge=1, le=MAX_FACE_MINOR)],
) -> StaffQuoteOut:
    """What a client buying this face value would pay today, with the clean amount."""
    async with _transaction(request, staff.actor) as session:
        view = await catalogue.staff_issue(session, staff.actor, code)
        _, found = await catalogue.staff_quote(
            session, code, Money(face_minor, view.issue.currency), clock
        )
    return _staff_quote_out(code, found)


@staff_router.get("/bond-holdings")
async def staff_bond_holdings(
    request: Request,
    staff: InvestmentsViewer,
    status: HoldingStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaffBondHoldingOut]:
    """Clients' holdings, newest first, with what each paid. Optionally only one status."""
    async with _transaction(request, staff.actor) as session:
        found = await holdings.staff_holdings(session, staff.actor, status, limit)
    return [
        StaffBondHoldingOut(
            id=h.id,
            client_id=h.client_id,
            code=h.code,
            currency=h.face.currency,
            face_minor=h.face.minor,
            price=h.price,
            accrued_minor=h.accrued.minor,
            cost_minor=h.cost.minor,
            quoted_on=h.quoted_on,
            status=h.status,
            created_at=h.created_at,
            pay_by=h.pay_by,
            start_date=h.start_date,
        )
        for h in found
    ]


@staff_router.get("/bond-coupons")
async def staff_bond_coupons(
    request: Request,
    staff: InvestmentsViewer,
    status: Annotated[str | None, Query(pattern="^(paid_out|held)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaffBondCouponOut]:
    """Coupons paid, newest first, with the tax withheld. `held` lists those still waiting."""
    async with _transaction(request, staff.actor) as session:
        found = await coupons.staff_coupons(session, staff.actor, status, limit)
    return [
        StaffBondCouponOut(
            **_coupon_out(c).model_dump(),
            client_id=c.client_id,
            gross_minor=c.gross.minor,
            tax_minor=c.tax.minor,
        )
        for c in found
    ]


@staff_router.get("/bond-repayments")
async def staff_bond_repayments(
    request: Request,
    staff: InvestmentsViewer,
    status: Annotated[str | None, Query(pattern="^(paid_out|held)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaffBondRepaymentOut]:
    """Holdings repaid at maturity, newest first, with the tax on the last coupon. `held` lists
    those still waiting for a destination."""
    async with _transaction(request, staff.actor) as session:
        found = await maturity.staff_redemptions(session, staff.actor, status, limit)
    return [
        StaffBondRepaymentOut(
            **_repayment_out(r).model_dump(),
            client_id=r.client_id,
            cost_minor=r.cost.minor,
            coupon_gross_minor=r.coupon_gross.minor,
            coupon_tax_minor=r.coupon_tax.minor,
        )
        for r in found
    ]


@staff_router.get("/bond-changes")
async def staff_bond_changes(
    request: Request, staff: ProductsViewer, status: BondChangeStatus | None = None
) -> list[BondChangeOut]:
    async with _transaction(request, staff.actor) as session:
        views = await catalogue.list_changes(session, staff.actor, status=status)
    return [_change_out(v) for v in views]


@staff_router.post("/bond-changes/new-issue", status_code=201)
async def staff_propose_bond(
    request: Request, staff: ProductsChanger, clock: AppClock, body: NewBondIn
) -> BondChangeOut:
    """Propose a new issue. A second member of staff must approve it, and it starts as a draft."""
    new = catalogue.NewIssue(
        code=body.code,
        isin=body.isin,
        name=body.name,
        currency=body.currency,
        coupon_bps=body.coupon_bps,
        tax_bps=body.tax_bps
        if body.tax_bps is not None
        else default_tax_bps(body.issue_date, body.maturity_date),
        issue_date=body.issue_date,
        maturity_date=body.maturity_date,
        minimum_face_minor=body.minimum_face_minor,
        face_step_minor=body.face_step_minor,
        price=body.price,
        type_code=body.type_code,
    )
    async with _transaction(request, staff.actor) as session:
        view = await catalogue.propose_issue(session, staff.actor, new, body.reason, clock)
    return _change_out(view)


@staff_router.post("/bond-changes/price", status_code=201)
async def staff_propose_bond_price(
    request: Request, staff: ProductsChanger, clock: AppClock, body: BondPriceIn
) -> BondChangeOut:
    """Propose today's price for an issue. It is in force once a second person approves."""
    async with _transaction(request, staff.actor) as session:
        view = await catalogue.propose_price(
            session, staff.actor, body.code, body.price, body.reason, clock
        )
    return _change_out(view)


@staff_router.post("/bond-changes/status", status_code=201)
async def staff_propose_bond_status(
    request: Request, staff: ProductsChanger, clock: AppClock, body: BondStatusIn
) -> BondChangeOut:
    """Propose opening (`live`) or pausing (`paused`) an issue."""
    async with _transaction(request, staff.actor) as session:
        view = await catalogue.propose_status(
            session, staff.actor, body.code, body.to, body.reason, clock
        )
    return _change_out(view)


@staff_router.post("/bond-changes/{change_id}/approve")
async def staff_approve_bond_change(
    request: Request, staff: ProductsApprover, clock: AppClock, change_id: str
) -> BondChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await catalogue.approve(session, staff.actor, change_id, clock)
    return _change_out(view)


@staff_router.post("/bond-changes/{change_id}/send-back")
async def staff_send_back_bond_change(
    request: Request, staff: ProductsChanger, clock: AppClock, change_id: str, body: SendBack
) -> BondChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await catalogue.send_back(session, staff.actor, change_id, body.note, clock)
    return _change_out(view)


@staff_router.post("/bond-changes/{change_id}/withdraw")
async def staff_withdraw_bond_change(
    request: Request, staff: ProductsChanger, clock: AppClock, change_id: str
) -> BondChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await catalogue.withdraw(session, staff.actor, change_id, clock)
    return _change_out(view)


@staff_router.get("/bond-sales")
async def staff_bond_sales(
    request: Request,
    staff: DeskViewer,
    status: SaleStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaffBondSaleOut]:
    """Clients' asks to sell, newest first. `requested` lists those for the desk to sell,
    `priced` those waiting for a second person."""
    async with _transaction(request, staff.actor) as session:
        found = await sales.staff_sales(session, staff.actor, status, limit)
    return [_staff_sale_out(v, staff) for v in found]


@staff_router.post("/bond-sales/{sale_id}/price")
async def staff_price_bond_sale(
    request: Request, staff: DeskChanger, clock: AppClock, sale_id: PushId, body: PriceSaleIn
) -> StaffBondSaleOut:
    """Record the price the desk sold the holding at, the day it settled and the desk's
    reference, for a second person to approve.

    422 `sold_on` / `sold_on_in_future`, `sold_on_before_start`, `sold_on_at_maturity` or
    `coupon_since_sale`. 409 when it is not waiting to be priced.
    """
    async with _transaction(request, staff.actor) as session:
        view = await sales.price_sale(
            session,
            staff.actor,
            sale_id,
            price=body.price,
            sold_on=body.sold_on,
            reason=body.reason,
            clock=clock,
        )
    return _staff_sale_out(view, staff)


@staff_router.post("/bond-sales/{sale_id}/approve")
async def staff_approve_bond_sale(
    request: Request, staff: DeskApprover, clock: AppClock, sale_id: PushId
) -> StaffBondSaleOut:
    """Approve a sale someone else priced. The worker then pays the client. Asks the person to
    confirm it is them."""
    async with _transaction(request, staff.actor) as session:
        view = await sales.approve_sale(session, staff.actor, sale_id, clock)
    return _staff_sale_out(view, staff)


@staff_router.post("/bond-sales/{sale_id}/send-back")
async def staff_send_back_bond_sale(
    request: Request, staff: DeskChanger, clock: AppClock, sale_id: PushId, body: SendBack
) -> StaffBondSaleOut:
    async with _transaction(request, staff.actor) as session:
        view = await sales.send_back_sale(
            session, staff.actor, sale_id, note=body.note, clock=clock
        )
    return _staff_sale_out(view, staff)


@staff_router.post("/bond-sales/{sale_id}/withdraw")
async def staff_withdraw_bond_sale(
    request: Request, staff: DeskChanger, clock: AppClock, sale_id: PushId
) -> StaffBondSaleOut:
    async with _transaction(request, staff.actor) as session:
        view = await sales.withdraw_sale(session, staff.actor, sale_id, clock)
    return _staff_sale_out(view, staff)


routers = (public_router, client_router, staff_router)
