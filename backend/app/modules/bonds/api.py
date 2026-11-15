"""Bonds over HTTP: the issues on offer and their prices, and the staff changes to them.

* `/public/bonds`: the website. Live and paused issues with their coupon after tax and price.
  Cached for five minutes and readable from any origin.
* `/client/bonds`: the same for a signed-in client, with a quote for a face value.
* `/staff/bonds`, `/staff/bond-changes`: issues with their tax, and proposing and deciding new
  issues, prices, pauses and openings, a second person enforced in the database.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.bonds import catalogue
from app.modules.bonds.domain.changes import BondChangeAction, BondChangeStatus
from app.modules.bonds.domain.issues import (
    BondIssue,
    BondStatus,
    ClientQuote,
    Quote,
    default_tax_bps,
)
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope
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


# Wiring -----------------------------------------------------------------------------------------


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
ProductsViewer = Annotated[StaffSession, Depends(require_permission("products.view"))]
ProductsChanger = Annotated[StaffSession, Depends(require_permission("products.change"))]
ProductsApprover = Annotated[StaffSession, Depends(require_confirmed("products.change"))]


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


routers = (public_router, client_router, staff_router)
