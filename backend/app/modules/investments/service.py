"""Catalogue and rate commands and queries. Every function works inside the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here. The
triggers and policies of migration 0060 then hold whatever this code does: a rate exists only
for a proposal that a second member of staff approved.

Which staff may propose or approve is a permission, checked by the route before it calls in.

Functions whose names start with `client_` return only figures after tax. They are the only
ones a client-facing route may call.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments.domain.interest import (
    ClientReturns,
    Terms,
    client_returns,
    maturity_date,
)
from app.modules.investments.domain.products import (
    InterestPaid,
    Product,
    ProductKind,
    ProductStatus,
    check_new_investment,
)
from app.modules.investments.domain.rates import (
    InvalidRate,
    ProposalStatus,
    RateProposal,
    RateVersion,
    Refusal,
    check_approval,
    check_proposal,
    check_send_back,
    check_withdrawal,
    rate_in_force,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

PROPOSAL_RESOURCE = "investment_rate_proposal"
MAX_PROPOSALS = 200


@dataclass(frozen=True, slots=True)
class ProductView:
    """A product with its rates, for staff. It shows the tax, so it never goes to a client."""

    id: str
    product: Product
    # The rate a new investment would get today. None if no rate has started yet.
    rate: RateVersion | None
    # An approved rate that has not started yet, if there is one. The earliest, if several.
    next_rate: RateVersion | None


@dataclass(frozen=True, slots=True)
class ProposalView:
    id: str
    product_id: str
    product_code: str
    rate: RateVersion
    reason: str
    status: ProposalStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None

    @property
    def proposal(self) -> RateProposal:
        return RateProposal(self.rate, self.proposed_by, self.reason, self.status)


@dataclass(frozen=True, slots=True)
class ClientProduct:
    """What a client sees of a product. The rate is after tax, and no tax figure is here."""

    code: str
    name: str
    kind: ProductKind
    currency: Currency
    term_days: int | None
    minimum: Money
    interest_paid: InterestPaid
    # "10.63": the yearly rate after tax, two decimal places.
    rate_percent: str


@dataclass(frozen=True, slots=True)
class ClientQuote:
    """The returns calculator and the review screen: what this amount earns, after tax."""

    product: ClientProduct
    amount: Money
    starts_on: date
    # None for an open term, which also has no fixed interest to quote.
    matures_on: date | None
    returns: ClientReturns | None


_PRODUCT_COLUMNS = "id, code, name, kind, currency, term_days, minimum_minor, interest_paid, status"
_PRODUCTS = text(f"SELECT {_PRODUCT_COLUMNS} FROM investment_products ORDER BY created_at, id")  # noqa: S608
_PRODUCT_BY_CODE = text(f"SELECT {_PRODUCT_COLUMNS} FROM investment_products WHERE code = :code")  # noqa: S608
_RATES = text(
    "SELECT product_id, effective_from, annual_rate_bps, tax_bps FROM investment_rates "
    "ORDER BY product_id, effective_from"
)
_RATES_FOR = text(
    "SELECT product_id, effective_from, annual_rate_bps, tax_bps FROM investment_rates "
    "WHERE product_id = :product_id ORDER BY effective_from"
)
_PROPOSAL_COLUMNS = (
    "p.id, p.product_id, c.code AS product_code, p.effective_from, p.annual_rate_bps, p.tax_bps, "
    "p.reason, p.status, p.proposed_by, p.proposed_at, p.decided_by, p.decided_at, p.decision_note"
)
_PROPOSAL_FROM = (
    "FROM investment_rate_proposals p JOIN investment_products c ON c.id = p.product_id"
)
_PROPOSAL_BY_ID = text(f"SELECT {_PROPOSAL_COLUMNS} {_PROPOSAL_FROM} WHERE p.id = :id")
_PROPOSAL_LOCK = text("SELECT id FROM investment_rate_proposals WHERE id = :id FOR UPDATE")
_PROPOSALS = text(
    f"SELECT {_PROPOSAL_COLUMNS} {_PROPOSAL_FROM} "
    "WHERE (CAST(:status AS text) IS NULL OR p.status = :status) "
    "ORDER BY p.proposed_at DESC, p.id DESC LIMIT :limit"
)
_INSERT_PROPOSAL = text(
    "INSERT INTO investment_rate_proposals (id, product_id, effective_from, annual_rate_bps, "
    "tax_bps, reason, proposed_by, proposed_at) VALUES (:id, :product_id, :effective_from, "
    ":annual_rate_bps, :tax_bps, :reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE investment_rate_proposals SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note WHERE id = :id"
)
_INSERT_RATE = text(
    "INSERT INTO investment_rates (id, product_id, effective_from, annual_rate_bps, tax_bps, "
    "proposal_id, created_at) VALUES (:id, :product_id, :effective_from, :annual_rate_bps, "
    ":tax_bps, :proposal_id, :now)"
)
# Serialises proposals and approvals for one product, so two changes cannot both pass a check
# that each would fail after the other. The application roles cannot lock the product row.
_PRODUCT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# Queries for staff ------------------------------------------------------------------------------


async def list_products(session: AsyncSession, clock: Clock) -> list[ProductView]:
    today = business_date(clock.now())
    rates = await _all_rates(session)
    return [
        _product_view(row, rates.get(row.id, []), today)
        for row in (await session.execute(_PRODUCTS)).all()
    ]


async def get_product(session: AsyncSession, code: str, clock: Clock) -> ProductView:
    row = await _product_row(session, code)
    return _product_view(row, await _rates_for(session, row.id), business_date(clock.now()))


async def rate_history(session: AsyncSession, code: str) -> list[RateVersion]:
    """Every approved rate for a product, oldest first, including ones yet to start."""
    return await _rates_for(session, (await _product_row(session, code)).id)


async def list_proposals(
    session: AsyncSession,
    staff: Actor,
    *,
    status: ProposalStatus | None = None,
    limit: int = MAX_PROPOSALS,
) -> list[ProposalView]:
    _staff_only(staff)
    rows = await session.execute(
        _PROPOSALS,
        {"status": status.value if status else None, "limit": max(1, min(limit, MAX_PROPOSALS))},
    )
    return [_proposal_view(row) for row in rows.all()]


# Rate changes -----------------------------------------------------------------------------------


async def propose_rate(
    session: AsyncSession,
    staff: Actor,
    *,
    product_code: str,
    annual_rate_bps: int,
    tax_bps: int,
    effective_from: date,
    reason: str,
    clock: Clock,
) -> ProposalView:
    """Save a rate change for a second member of staff to approve. Nothing changes yet."""
    _staff_only(staff)
    try:
        rate = RateVersion(effective_from, annual_rate_bps, tax_bps)
    except InvalidRate as error:
        raise _invalid("annual_rate_bps", "out_of_range") from error
    product = await _product_row(session, product_code)
    await _lock_product(session, product.id)
    refusal = check_proposal(
        rate, reason, await _rates_for(session, product.id), business_date(clock.now())
    )
    if refusal is not None:
        raise _refused(refusal)
    proposal_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_PROPOSAL,
                {
                    "id": proposal_id,
                    "product_id": product.id,
                    "effective_from": effective_from,
                    "annual_rate_bps": annual_rate_bps,
                    "tax_bps": tax_bps,
                    "reason": reason.strip(),
                    "proposed_by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # Another change for this product is already waiting for approval.
        raise ApplicationError("conflict") from error
    await _audit(
        session,
        staff,
        "change",
        "investments.rate.proposed",
        proposal_id,
        clock,
        reason=reason,
        details=_rate_details(product.code, rate),
    )
    return await _proposal(session, proposal_id)


async def approve_rate(
    session: AsyncSession, staff: Actor, proposal_id: str, clock: Clock
) -> ProposalView:
    """Approve a proposal as the second person. The rate is saved in this same transaction."""
    view = await _for_decision(session, staff, proposal_id)
    refusal = check_approval(
        view.proposal,
        _id(staff),
        await _rates_for(session, view.product_id),
        business_date(clock.now()),
    )
    if refusal is not None:
        raise _refused(refusal)
    now = clock.now()
    await session.execute(
        _DECIDE,
        {"id": view.id, "status": "approved", "decided_by": staff.id, "now": now, "note": None},
    )
    await session.execute(
        _INSERT_RATE,
        {
            "id": push_id(),
            "product_id": view.product_id,
            "effective_from": view.rate.effective_from,
            "annual_rate_bps": view.rate.annual_rate_bps,
            "tax_bps": view.rate.tax_bps,
            "proposal_id": view.id,
            "now": now,
        },
    )
    await _audit(
        session,
        staff,
        "approval",
        "investments.rate.approved",
        view.id,
        clock,
        reason=view.reason,
        details=_rate_details(view.product_code, view.rate),
    )
    return await _proposal(session, view.id)


async def send_back_rate(
    session: AsyncSession, staff: Actor, proposal_id: str, note: str, clock: Clock
) -> ProposalView:
    """Decline a proposal as the second person, with a note for the proposer."""
    view = await _for_decision(session, staff, proposal_id)
    refusal = check_send_back(view.proposal, _id(staff), note)
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": "sent_back",
            "decided_by": staff.id,
            "now": clock.now(),
            "note": note.strip(),
        },
    )
    await _audit(
        session, staff, "approval", "investments.rate.sent_back", view.id, clock, reason=note
    )
    return await _proposal(session, view.id)


async def withdraw_rate(
    session: AsyncSession, staff: Actor, proposal_id: str, clock: Clock
) -> ProposalView:
    """Take back a proposal nobody has decided. Only its proposer can."""
    view = await _for_decision(session, staff, proposal_id)
    refusal = check_withdrawal(view.proposal, _id(staff))
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": "withdrawn",
            "decided_by": staff.id,
            "now": clock.now(),
            "note": None,
        },
    )
    await _audit(session, staff, "change", "investments.rate.withdrawn", view.id, clock)
    return await _proposal(session, view.id)


# What clients see --------------------------------------------------------------------------------


async def client_products(session: AsyncSession, clock: Clock) -> list[ClientProduct]:
    """The products a client can invest in today, with the yearly rate after tax."""
    return [
        _client_product(view.product, view.rate)
        for view in await list_products(session, clock)
        if view.product.status is ProductStatus.LIVE and view.rate is not None
    ]


async def client_quote(
    session: AsyncSession, product_code: str, amount: Money, clock: Clock
) -> ClientQuote:
    """What `amount` would earn if the investment started today, after tax.

    Refused with `validation_failed` on `amount` if the product would not take it. The start
    date is today's business date: the real one is the day the payment is confirmed.
    """
    view = await get_product(session, product_code, clock)
    if view.rate is None:
        raise ApplicationError("not_found")
    refusal = check_new_investment(view.product, amount)
    if refusal is not None:
        raise _invalid("amount", refusal.value)
    today = business_date(clock.now())
    term = view.product.term_days
    return ClientQuote(
        product=_client_product(view.product, view.rate),
        amount=amount,
        starts_on=today,
        matures_on=None if term is None else maturity_date(today, term),
        returns=None
        if term is None
        else client_returns(Terms(amount, view.rate.annual_rate_bps, view.rate.tax_bps), term),
    )


# Internals --------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


async def _for_decision(session: AsyncSession, staff: Actor, proposal_id: str) -> ProposalView:
    """The proposal, locked, with its product locked too, read after both locks are held."""
    _staff_only(staff)
    locked = (await session.execute(_PROPOSAL_LOCK, {"id": proposal_id})).one_or_none()
    if locked is None:
        raise ApplicationError("not_found")
    view = await _proposal(session, proposal_id)
    await _lock_product(session, view.product_id)
    return view


async def _lock_product(session: AsyncSession, product_id: str) -> None:
    await session.execute(_PRODUCT_LOCK, {"lock": f"finvest.investment_rates:{product_id}"})


async def _proposal(session: AsyncSession, proposal_id: str) -> ProposalView:
    row = (await session.execute(_PROPOSAL_BY_ID, {"id": proposal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _proposal_view(row)


async def _product_row(session: AsyncSession, code: str) -> Row[Any]:
    row = (await session.execute(_PRODUCT_BY_CODE, {"code": code})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return row


async def _rates_for(session: AsyncSession, product_id: str) -> list[RateVersion]:
    rows = await session.execute(_RATES_FOR, {"product_id": product_id})
    return [_rate(row) for row in rows.all()]


async def _all_rates(session: AsyncSession) -> dict[str, list[RateVersion]]:
    rates: dict[str, list[RateVersion]] = {}
    for row in (await session.execute(_RATES)).all():
        rates.setdefault(row.product_id, []).append(_rate(row))
    return rates


def _rate(row: Row[Any]) -> RateVersion:
    return RateVersion(row.effective_from, row.annual_rate_bps, row.tax_bps)


def _product(row: Row[Any]) -> Product:
    currency = Currency(row.currency)
    return Product(
        code=row.code,
        name=row.name,
        kind=ProductKind(row.kind),
        currency=currency,
        term_days=row.term_days,
        minimum=Money(row.minimum_minor, currency),
        interest_paid=InterestPaid(row.interest_paid),
        status=ProductStatus(row.status),
    )


def _product_view(row: Row[Any], rates: list[RateVersion], today: date) -> ProductView:
    upcoming = [rate for rate in rates if rate.effective_from > today]
    return ProductView(
        id=row.id,
        product=_product(row),
        rate=rate_in_force(rates, today),
        next_rate=min(upcoming, key=lambda rate: rate.effective_from) if upcoming else None,
    )


def _proposal_view(row: Row[Any]) -> ProposalView:
    return ProposalView(
        id=row.id,
        product_id=row.product_id,
        product_code=row.product_code,
        rate=_rate(row),
        reason=row.reason,
        status=ProposalStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


def _client_product(product: Product, rate: RateVersion) -> ClientProduct:
    return ClientProduct(
        code=product.code,
        name=product.name,
        kind=product.kind,
        currency=product.currency,
        term_days=product.term_days,
        minimum=product.minimum,
        interest_paid=product.interest_paid,
        rate_percent=rate.client_rate_percent,
    )


def _rate_details(product_code: str, rate: RateVersion) -> dict[str, object]:
    # Rates are business settings, not personal data, so they belong in the trail.
    return {
        "product": product_code,
        "annual_rate_bps": rate.annual_rate_bps,
        "tax_bps": rate.tax_bps,
        "effective_from": rate.effective_from.isoformat(),
    }


_FIELDS = {
    Refusal.REASON_REQUIRED: "reason",
    Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE: "effective_from",
    Refusal.DATE_ALREADY_TAKEN: "effective_from",
    Refusal.NOTHING_CHANGES: "annual_rate_bps",
}


def _refused(refusal: Refusal) -> ApplicationError:
    """A problem with what was sent names the field. The rest say who may not do this, or when."""
    if refusal in _FIELDS:
        return _invalid(_FIELDS[refusal], refusal.value)
    if refusal is Refusal.ALREADY_DECIDED:
        return ApplicationError("conflict")
    return ApplicationError("forbidden")


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    proposal_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=PROPOSAL_RESOURCE,
            resource_id=proposal_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details or {},
        ),
        clock,
    )
