"""Product changes: one member of staff proposes, a different one approves. Inside the caller's
transaction, which `Database.actor_transaction(actor)` opened for the same actor passed here.

The rules are in `domain/product_changes.py`. The triggers of migration 0360 hold them in the
database too: a product row is written only by an approval, with exactly the approved values.

Which staff may propose or approve is a permission, checked by the route before it calls in.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments.catalogue import (
    invalid,
    product_from_row,
    product_row,
    rates_for,
)
from app.modules.investments.domain.product_changes import (
    Current,
    ProductAction,
    ProductChange,
    Refusal,
    check_approval,
    check_proposal,
    check_send_back,
    check_withdrawal,
    with_status,
)
from app.modules.investments.domain.products import (
    InterestPaid,
    InvalidProduct,
    Product,
    ProductKind,
    ProductStatus,
)
from app.modules.investments.domain.rates import (
    InvalidRate,
    ProposalStatus,
    RateVersion,
    rate_in_force,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

CHANGE_RESOURCE = "investment_product_change"
MAX_CHANGES = 200


@dataclass(frozen=True, slots=True)
class ProductChangeView:
    id: str
    action: ProductAction
    # The product as it will be once approved. The console sets it beside the product as it is.
    target: Product
    # A new product's first rate, before tax and the tax. None for any other action.
    first_annual_rate_bps: int | None
    first_tax_bps: int | None
    base_version: int | None
    reason: str
    status: ProposalStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None

    def change(self) -> ProductChange:
        first = (
            None
            if self.first_annual_rate_bps is None or self.first_tax_bps is None
            else RateVersion(
                self.proposed_at.date(), self.first_annual_rate_bps, self.first_tax_bps
            )
        )
        return ProductChange(
            action=self.action,
            target=self.target,
            reason=self.reason,
            proposed_by=self.proposed_by,
            base_version=self.base_version,
            first_rate=first,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class NewProduct:
    code: str
    name: str
    kind: ProductKind
    currency: Currency
    term_days: int | None
    minimum_minor: int
    interest_paid: InterestPaid
    annual_rate_bps: int
    tax_bps: int


@dataclass(frozen=True, slots=True)
class NewTerms:
    name: str
    kind: ProductKind
    term_days: int | None
    minimum_minor: int
    interest_paid: InterestPaid


_COLUMNS = (
    "id, action, product_id, base_version, code, name, kind, currency, term_days, minimum_minor, "
    "interest_paid, product_status, first_annual_rate_bps, first_tax_bps, reason, status, "
    "proposed_by, proposed_at, decided_by, decided_at, decision_note"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM investment_product_changes WHERE id = :id")  # noqa: S608
_LOCK = text("SELECT id FROM investment_product_changes WHERE id = :id FOR UPDATE")
_LIST = text(
    f"SELECT {_COLUMNS} FROM investment_product_changes "  # noqa: S608
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO investment_product_changes (id, action, product_id, base_version, code, name, "
    "kind, currency, term_days, minimum_minor, interest_paid, product_status, "
    "first_annual_rate_bps, first_tax_bps, reason, proposed_by, proposed_at) VALUES (:id, "
    ":action, :product_id, :base_version, :code, :name, :kind, :currency, :term_days, "
    ":minimum_minor, :interest_paid, :product_status, :first_annual_rate_bps, :first_tax_bps, "
    ":reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE investment_product_changes SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note WHERE id = :id"
)
_VERSION = text("SELECT id, version FROM investment_products WHERE code = :code")
_CODE_TAKEN = text(
    "SELECT EXISTS (SELECT FROM investment_products WHERE code = :code) "
    "OR EXISTS (SELECT FROM investment_product_changes "
    "WHERE code = :code AND action = 'create' AND status = 'proposed' "
    "AND (CAST(:except AS text) IS NULL OR id <> :except))"
)
_INSERT_PRODUCT = text(
    "INSERT INTO investment_products (id, code, name, kind, currency, term_days, minimum_minor, "
    "interest_paid, status, version, change_id, created_at, updated_at) VALUES (:id, :code, "
    ":name, :kind, :currency, :term_days, :minimum_minor, :interest_paid, :status, 1, "
    ":change_id, :now, :now)"
)
_UPDATE_PRODUCT = text(
    "UPDATE investment_products SET name = :name, kind = :kind, term_days = :term_days, "
    "minimum_minor = :minimum_minor, interest_paid = :interest_paid, status = :status, "
    "version = version + 1, change_id = :change_id, updated_at = :now WHERE id = :id"
)
_INSERT_FIRST_RATE = text(
    "INSERT INTO investment_rates (id, product_id, effective_from, annual_rate_bps, tax_bps, "
    "product_change_id, created_at) VALUES (:id, :product_id, :effective_from, "
    ":annual_rate_bps, :tax_bps, :change_id, :now)"
)
# Serialises every change to one product, and with it rate changes, which take the same lock.
_ADVISORY = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# Queries ---------------------------------------------------------------------------------------


async def list_product_changes(
    session: AsyncSession,
    staff: Actor,
    *,
    status: ProposalStatus | None = None,
    limit: int = MAX_CHANGES,
) -> list[ProductChangeView]:
    _staff_only(staff)
    rows = await session.execute(
        _LIST,
        {"status": status.value if status else None, "limit": max(1, min(limit, MAX_CHANGES))},
    )
    return [_view(row) for row in rows.all()]


async def get_product_change(
    session: AsyncSession, staff: Actor, change_id: str
) -> ProductChangeView:
    _staff_only(staff)
    return await _change(session, change_id)


# Proposals -------------------------------------------------------------------------------------


async def propose_new_product(
    session: AsyncSession, staff: Actor, new: NewProduct, reason: str, clock: Clock
) -> ProductChangeView:
    """Propose a new product: a draft with its first rate, once a second person approves."""
    _staff_only(staff)
    target = _product(
        new.code,
        new.name,
        new.kind,
        new.currency,
        new.term_days,
        new.minimum_minor,
        new.interest_paid,
        ProductStatus.DRAFT,
    )
    try:
        first = RateVersion(business_date(clock.now()), new.annual_rate_bps, new.tax_bps)
    except InvalidRate as error:
        raise invalid("annual_rate_bps", "out_of_range") from error
    await _lock(session, f"code:{target.code}")
    change = ProductChange(ProductAction.CREATE, target, reason, _id(staff), first_rate=first)
    refusal = check_proposal(change, None, code_taken=await _code_taken(session, target.code))
    if refusal is not None:
        raise _refused(refusal)
    return await _save(session, staff, change, None, clock)


async def propose_new_terms(
    session: AsyncSession,
    staff: Actor,
    product_code: str,
    terms: NewTerms,
    reason: str,
    clock: Clock,
) -> ProductChangeView:
    """Propose new terms for a product. New investments take them once a second person approves."""
    _staff_only(staff)
    product_id, current = await _current(session, product_code, clock)
    target = _product(
        current.product.code,
        terms.name,
        terms.kind,
        current.product.currency,
        terms.term_days,
        terms.minimum_minor,
        terms.interest_paid,
        current.product.status,
    )
    change = ProductChange(
        ProductAction.EDIT, target, reason, _id(staff), base_version=current.version
    )
    return await _propose_existing(session, staff, change, product_id, current, clock)


async def propose_status(
    session: AsyncSession,
    staff: Actor,
    product_code: str,
    to: ProductStatus,
    reason: str,
    clock: Clock,
) -> ProductChangeView:
    """Propose pausing (`paused`) or opening (`live`) a product."""
    _staff_only(staff)
    if to is ProductStatus.DRAFT:
        raise invalid("to", "unknown_status")
    product_id, current = await _current(session, product_code, clock)
    action = ProductAction.OPEN if to is ProductStatus.LIVE else ProductAction.PAUSE
    change = ProductChange(
        action,
        with_status(current.product, to),
        reason,
        _id(staff),
        base_version=current.version,
    )
    return await _propose_existing(session, staff, change, product_id, current, clock)


# Decisions -------------------------------------------------------------------------------------


async def approve_product_change(
    session: AsyncSession, staff: Actor, change_id: str, clock: Clock
) -> ProductChangeView:
    """Approve as the second person. The product is written in this same transaction."""
    view = await _for_decision(session, staff, change_id)
    change = view.change()
    now = clock.now()
    if change.action is ProductAction.CREATE:
        refusal = check_approval(
            change,
            _id(staff),
            None,
            code_taken=await _code_taken(session, view.target.code, except_change=view.id),
        )
    else:
        _, current = await _current(session, view.target.code, clock)
        refusal = check_approval(change, _id(staff), current, code_taken=False)
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {"id": view.id, "status": "approved", "decided_by": staff.id, "now": now, "note": None},
    )
    values = _values(view.target)
    if change.action is ProductAction.CREATE:
        product_id = push_id()
        await session.execute(
            _INSERT_PRODUCT, {**values, "id": product_id, "change_id": view.id, "now": now}
        )
        await session.execute(
            _INSERT_FIRST_RATE,
            {
                "id": push_id(),
                "product_id": product_id,
                # A draft takes no investments, so its first rate can start the day it exists.
                "effective_from": business_date(now),
                "annual_rate_bps": view.first_annual_rate_bps,
                "tax_bps": view.first_tax_bps,
                "change_id": view.id,
                "now": now,
            },
        )
    else:
        row = (await session.execute(_VERSION, {"code": view.target.code})).one()
        await session.execute(
            _UPDATE_PRODUCT, {**values, "id": row.id, "change_id": view.id, "now": now}
        )
    await _audit(
        session,
        staff,
        "approval",
        f"investments.product.{change.action.value}_approved",
        view.id,
        clock,
        reason=view.reason,
        details=_details(view),
    )
    return await _change(session, view.id)


async def send_back_product_change(
    session: AsyncSession, staff: Actor, change_id: str, note: str, clock: Clock
) -> ProductChangeView:
    view = await _for_decision(session, staff, change_id)
    refusal = check_send_back(view.change(), _id(staff), note)
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
        session, staff, "approval", "investments.product.sent_back", view.id, clock, reason=note
    )
    return await _change(session, view.id)


async def withdraw_product_change(
    session: AsyncSession, staff: Actor, change_id: str, clock: Clock
) -> ProductChangeView:
    view = await _for_decision(session, staff, change_id)
    refusal = check_withdrawal(view.change(), _id(staff))
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
    await _audit(session, staff, "change", "investments.product.withdrawn", view.id, clock)
    return await _change(session, view.id)


# Internals ------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def _product(
    code: str,
    name: str,
    kind: ProductKind,
    currency: Currency,
    term_days: int | None,
    minimum_minor: int,
    interest_paid: InterestPaid,
    status: ProductStatus,
) -> Product:
    """The product a change names, or `validation_failed` on the field that is wrong."""
    if type(minimum_minor) is not int or minimum_minor <= 0:
        raise invalid("minimum_minor", "out_of_range")
    try:
        return Product(
            code=code,
            name=name,
            kind=kind,
            currency=currency,
            term_days=term_days,
            minimum=Money(minimum_minor, currency),
            interest_paid=interest_paid,
            status=status,
        )
    except InvalidProduct as error:
        raise invalid(_field_for(str(error)), "invalid") from error


def _field_for(message: str) -> str:
    # The domain names the rule in words. Map it to the field the console shows it beside.
    words = message.lower()
    if "open term" in words:
        return "interest_paid"
    for word, field in (("code", "code"), ("name", "name"), ("term", "term_days")):
        if word in words:
            return field
    return "minimum_minor" if "minimum" in words else "product"


async def _current(session: AsyncSession, code: str, clock: Clock) -> tuple[str, Current]:
    row = await product_row(session, code)
    version = (await session.execute(_VERSION, {"code": code})).one().version
    rates = await rates_for(session, row.id)
    return row.id, Current(
        product=product_from_row(row),
        version=version,
        has_rate=rate_in_force(rates, business_date(clock.now())) is not None,
    )


async def _propose_existing(
    session: AsyncSession,
    staff: Actor,
    change: ProductChange,
    product_id: str,
    current: Current,
    clock: Clock,
) -> ProductChangeView:
    await _lock(session, product_id)
    refusal = check_proposal(change, current, code_taken=False)
    if refusal is not None:
        raise _refused(refusal)
    return await _save(session, staff, change, product_id, clock)


async def _save(
    session: AsyncSession,
    staff: Actor,
    change: ProductChange,
    product_id: str | None,
    clock: Clock,
) -> ProductChangeView:
    change_id = push_id()
    first = change.first_rate
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    **_values(change.target),
                    "id": change_id,
                    "action": change.action.value,
                    "product_id": product_id,
                    "base_version": change.base_version,
                    "product_status": change.target.status.value,
                    "first_annual_rate_bps": first.annual_rate_bps if first else None,
                    "first_tax_bps": first.tax_bps if first else None,
                    "reason": change.reason.strip(),
                    "proposed_by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # Another change for this product, or this new code, is already waiting for approval.
        raise ApplicationError("conflict") from error
    view = await _change(session, change_id)
    await _audit(
        session,
        staff,
        "change",
        f"investments.product.{change.action.value}_proposed",
        change_id,
        clock,
        reason=change.reason,
        details=_details(view),
    )
    return view


async def _for_decision(session: AsyncSession, staff: Actor, change_id: str) -> ProductChangeView:
    """The change, locked, with its product (or new code) locked too, read after both."""
    _staff_only(staff)
    if (await session.execute(_LOCK, {"id": change_id})).one_or_none() is None:
        raise ApplicationError("not_found")
    view = await _change(session, change_id)
    row = (await session.execute(_VERSION, {"code": view.target.code})).one_or_none()
    if view.action is ProductAction.CREATE:
        await _lock(session, f"code:{view.target.code}")
    elif row is not None:
        await _lock(session, row.id)
    return await _change(session, change_id)


async def _lock(session: AsyncSession, key: str) -> None:
    # The same key as rate changes use for a product, so a rate and a product change for one
    # product never pass their checks side by side.
    await session.execute(_ADVISORY, {"lock": f"finvest.investment_rates:{key}"})


async def _code_taken(
    session: AsyncSession, code: str, *, except_change: str | None = None
) -> bool:
    taken = await session.execute(_CODE_TAKEN, {"code": code, "except": except_change})
    return bool(taken.scalar_one())


async def _change(session: AsyncSession, change_id: str) -> ProductChangeView:
    row = (await session.execute(_BY_ID, {"id": change_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


def _view(row: Row[Any]) -> ProductChangeView:
    currency = Currency(row.currency)
    return ProductChangeView(
        id=row.id,
        action=ProductAction(row.action),
        target=Product(
            code=row.code,
            name=row.name,
            kind=ProductKind(row.kind),
            currency=currency,
            term_days=row.term_days,
            minimum=Money(row.minimum_minor, currency),
            interest_paid=InterestPaid(row.interest_paid),
            status=ProductStatus(row.product_status),
        ),
        first_annual_rate_bps=row.first_annual_rate_bps,
        first_tax_bps=row.first_tax_bps,
        base_version=row.base_version,
        reason=row.reason,
        status=ProposalStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


def _values(product: Product) -> dict[str, object]:
    return {
        "code": product.code,
        "name": product.name,
        "kind": product.kind.value,
        "currency": product.currency.value,
        "term_days": product.term_days,
        "minimum_minor": product.minimum.minor,
        "interest_paid": product.interest_paid.value,
        "status": product.status.value,
    }


def _details(view: ProductChangeView) -> dict[str, object]:
    # Products are business settings, not personal data, so they belong in the trail.
    product = view.target
    details: dict[str, object] = {
        "product": product.code,
        "action": view.action.value,
        "name": product.name,
        "kind": product.kind.value,
        "currency": product.currency.value,
        "term_days": product.term_days,
        "minimum_minor": product.minimum.minor,
        "interest_paid": product.interest_paid.value,
        "status": product.status.value,
    }
    if view.first_annual_rate_bps is not None:
        details["annual_rate_bps"] = view.first_annual_rate_bps
        details["tax_bps"] = view.first_tax_bps
    return details


# Problems with what was sent name a field. The rest say who may not do this, or that it is stale.
_FIELDS = {
    Refusal.REASON_REQUIRED: "reason",
    Refusal.CODE_TAKEN: "code",
    Refusal.NOT_A_DRAFT: "status",
    Refusal.CODE_FIXED: "code",
    Refusal.CURRENCY_FIXED: "currency",
    Refusal.KIND_FIXED: "kind",
    Refusal.INTEREST_PAID_FIXED: "interest_paid",
    Refusal.STATUS_FIXED: "status",
    Refusal.NOTHING_CHANGES: "product",
    Refusal.ALREADY_LIVE: "status",
    Refusal.NOT_LIVE: "status",
    Refusal.NO_RATE: "status",
    Refusal.CURRENCY_NOT_OPEN: "currency",
    Refusal.PRODUCT_CHANGED: "product",
}


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal in _FIELDS:
        return invalid(_FIELDS[refusal], refusal.value)
    if refusal is Refusal.ALREADY_DECIDED:
        return ApplicationError("conflict")
    return ApplicationError("forbidden")


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    change_id: str,
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
            resource_type=CHANGE_RESOURCE,
            resource_id=change_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details or {},
        ),
        clock,
    )


# Who a change reaches -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductReach:
    """Investments in a product that a change does not alter, and the clients holding them.

    A rate or term change, or a pause, applies to new investments only. Running investments and
    ones waiting for payment keep the terms they were quoted. These counts tell staff how many
    that is, so the second person approves knowing it.
    """

    running: int
    awaiting_payment: int
    clients: int


_REACH = text(
    "SELECT count(*) FILTER (WHERE i.status = 'active') AS running, "
    "count(*) FILTER (WHERE i.status = 'awaiting_payment') AS awaiting_payment, "
    "count(DISTINCT i.client_id) AS clients "
    "FROM investments i WHERE i.product_id = :product_id "
    "AND i.status IN ('active', 'awaiting_payment')"
)


async def product_reach(session: AsyncSession, staff: Actor, code: str) -> ProductReach:
    _staff_only(staff)
    row = await product_row(session, code)
    counts = (await session.execute(_REACH, {"product_id": row.id})).one()
    return ProductReach(
        running=counts.running, awaiting_payment=counts.awaiting_payment, clients=counts.clients
    )
