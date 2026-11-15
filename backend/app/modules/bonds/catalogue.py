"""The bond catalogue: issues, their prices, and the changes two members of staff make to them.
Every function works inside the caller's transaction, which `Database.actor_transaction(actor)`
(or `Database.transaction()` for the public website) opened for the same actor passed here.

The rules are in `domain/issues.py` and `domain/changes.py`. The triggers of migration 0490
hold the important ones in the database too: an issue or a price is written only by an
approval, with exactly the approved values, by the person the transaction acts for.

Which staff may propose or approve is a permission, checked by the route before it calls in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.bonds.domain.changes import (
    BondChangeAction,
    BondChangeStatus,
    Current,
    Refusal,
    check_create,
    check_decision,
    check_price_change,
    check_status_change,
    check_withdrawal,
    status_after,
    valid_text,
)
from app.modules.bonds.domain.issues import (
    BondIssue,
    BondStatus,
    ClientQuote,
    InvalidIssue,
    Quote,
    check_purchase,
    client_quote,
    coupon_label,
    quote,
    remaining_coupons,
)
from app.modules.bonds.domain.issues import Refusal as BuyRefusal
from app.modules.product_types import service as product_types
from app.modules.product_types.domain.types import ReturnModel
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.limits import largest_single_amount
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

CHANGE_RESOURCE = "bond_change"
MAX_CHANGES = 200


@dataclass(frozen=True, slots=True)
class IssueView:
    """An issue as staff see it: with its tax and today's price."""

    id: str
    issue: BondIssue
    version: int
    # The price in force, and when it was approved. None until the first is approved.
    price: int | None
    priced_at: datetime | None


@dataclass(frozen=True, slots=True)
class ClientIssue:
    """What a client and the website see of an issue: the coupon after tax, never the tax."""

    code: str
    isin: str
    name: str
    currency: Currency
    # "12.80": the yearly coupon after tax.
    coupon_percent: str
    issue_date: date
    maturity_date: date
    years: int
    next_coupon_date: date | None
    coupons_left: int
    minimum_face: Money
    face_step: Money
    price: int | None
    status: BondStatus
    # Whether a client can buy it today.
    can_buy: bool


@dataclass(frozen=True, slots=True)
class ChangeView:
    id: str
    action: BondChangeAction
    # The issue as it will be once approved. For a price change, the issue as it is.
    code: str
    target: BondIssue | None
    price: int | None
    base_version: int | None
    reason: str
    status: BondChangeStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


@dataclass(frozen=True, slots=True)
class NewIssue:
    code: str
    isin: str
    name: str
    currency: Currency
    coupon_bps: int
    tax_bps: int
    issue_date: date
    maturity_date: date
    minimum_face_minor: int
    face_step_minor: int
    price: int
    # The product type, by code, of the bond model. None is the built-in Treasury bond.
    type_code: str | None = None


_ISSUE_COLUMNS = (
    "b.id, b.code, b.isin, b.name, b.currency, b.coupon_bps, b.tax_bps, b.issue_date, "
    "b.maturity_date, b.minimum_face_minor, b.face_step_minor, b.status, b.version, "
    "p.price, p.effective_at AS priced_at"
)
_ISSUE_FROM = (
    "FROM bond_issues b LEFT JOIN LATERAL (SELECT price, effective_at FROM bond_prices "
    "WHERE issue_id = b.id ORDER BY effective_at DESC, id DESC LIMIT 1) p ON true"
)
_ISSUES = text(
    f"SELECT {_ISSUE_COLUMNS} {_ISSUE_FROM} "
    "WHERE status = ANY(CAST(:statuses AS text[])) ORDER BY b.maturity_date, b.code"
)
_ISSUE = text(f"SELECT {_ISSUE_COLUMNS} {_ISSUE_FROM} WHERE b.code = :code")
_ISSUE_BY_ID = text(f"SELECT {_ISSUE_COLUMNS} {_ISSUE_FROM} WHERE b.id = :id")
_CHANGE_COLUMNS = (
    "c.id, c.action, c.issue_id, c.base_version, COALESCE(c.code, b.code) AS issue_code, "
    "c.code, c.isin, c.name, c.currency, c.coupon_bps, c.tax_bps, c.issue_date, "
    "c.maturity_date, c.minimum_face_minor, c.face_step_minor, c.price, c.reason, c.status, "
    "c.proposed_by, c.proposed_at, c.decided_by, c.decided_at, c.decision_note"
)
_CHANGES = text(
    f"SELECT {_CHANGE_COLUMNS} FROM bond_changes c LEFT JOIN bond_issues b ON b.id = c.issue_id "  # noqa: S608
    "WHERE (CAST(:status AS text) IS NULL OR c.status = :status) "
    "ORDER BY c.proposed_at DESC, c.id DESC LIMIT :limit"
)
_CHANGE = text(
    f"SELECT {_CHANGE_COLUMNS} FROM bond_changes c LEFT JOIN bond_issues b ON b.id = c.issue_id "  # noqa: S608
    "WHERE c.id = :id"
)
_LOCK_CHANGE = text("SELECT id FROM bond_changes WHERE id = :id FOR UPDATE")
_TAKEN = text(
    "SELECT EXISTS (SELECT FROM bond_issues WHERE code = :code) "
    "OR EXISTS (SELECT FROM bond_changes WHERE code = :code AND action = 'create' "
    "AND status = 'proposed' AND (CAST(:except AS text) IS NULL OR id <> :except)) AS code, "
    "EXISTS (SELECT FROM bond_issues WHERE isin = :isin) "
    "OR EXISTS (SELECT FROM bond_changes WHERE isin = :isin AND action = 'create' "
    "AND status = 'proposed' AND (CAST(:except AS text) IS NULL OR id <> :except)) AS isin"
)
_WAITING = text(
    "SELECT EXISTS (SELECT FROM bond_changes WHERE issue_id = :issue_id AND status = 'proposed')"
)
_INSERT_CHANGE = text(
    "INSERT INTO bond_changes (id, action, issue_id, base_version, code, isin, name, currency, "
    "coupon_bps, tax_bps, issue_date, maturity_date, minimum_face_minor, face_step_minor, price, "
    "reason, proposed_by, proposed_at, type_id) VALUES (:id, :action, :issue_id, "
    ":base_version, :code, :isin, :name, :currency, :coupon_bps, :tax_bps, :issue_date, "
    ":maturity_date, :minimum_face_minor, :face_step_minor, :price, :reason, :proposed_by, "
    ":now, :type_id)"
)
_DECIDE = text(
    "UPDATE bond_changes SET status = :status, decided_by = :decided_by, decided_at = :now, "
    "decision_note = :note WHERE id = :id"
)
_INSERT_ISSUE = text(
    "INSERT INTO bond_issues (id, code, isin, name, currency, coupon_bps, tax_bps, issue_date, "
    "maturity_date, minimum_face_minor, face_step_minor, status, version, change_id, "
    "created_at, updated_at, type_id) VALUES (:id, :code, :isin, :name, :currency, "
    ":coupon_bps, :tax_bps, :issue_date, :maturity_date, :minimum_face_minor, "
    ":face_step_minor, 'draft', 1, :change_id, :now, :now, "
    "(SELECT type_id FROM bond_changes WHERE id = CAST(:change_id AS push_id)))"
)
_UPDATE_STATUS = text(
    "UPDATE bond_issues SET status = :status, version = version + 1, change_id = :change_id, "
    "updated_at = :now WHERE id = :id AND version = :version"
)
_INSERT_PRICE = text(
    "INSERT INTO bond_prices (id, issue_id, price, change_id, effective_at) "
    "VALUES (:id, :issue_id, :price, :change_id, :now)"
)
# Serialises every change to one issue, and the creation of one code or ISIN.
_ADVISORY = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# Reads ------------------------------------------------------------------------------------------


async def staff_issues(session: AsyncSession, staff: Actor) -> list[IssueView]:
    _staff_only(staff)
    return await _issues(session, (BondStatus.DRAFT, BondStatus.LIVE, BondStatus.PAUSED))


async def staff_issue(session: AsyncSession, staff: Actor, code: str) -> IssueView:
    _staff_only(staff)
    return await issue_view(session, code)


async def client_issues(session: AsyncSession, clock: Clock) -> list[ClientIssue]:
    """Live and paused issues that have not matured, as clients and the website see them."""
    today = business_date(clock.now())
    found = await _issues(session, (BondStatus.LIVE, BondStatus.PAUSED))
    return [_client(view, today) for view in found if view.issue.maturity_date > today]


async def client_issue(session: AsyncSession, code: str, clock: Clock) -> ClientIssue:
    view = await issue_view(session, code)
    today = business_date(clock.now())
    if view.issue.status is BondStatus.DRAFT or view.issue.maturity_date <= today:
        raise ApplicationError("not_found")
    return _client(view, today)


async def staff_quote(
    session: AsyncSession, code: str, face: Money, clock: Clock
) -> tuple[IssueView, Quote]:
    """What buying `face` costs today, refused as a client's purchase would be."""
    view = await issue_view(session, code)
    today = business_date(clock.now())
    if view.price is None:
        raise invalid("product_code", BuyRefusal.NO_PRICE.value)
    if face.currency is not view.issue.currency:
        raise invalid("face_minor", BuyRefusal.CURRENCY_NOT_SUPPORTED.value)
    refusal = check_purchase(view.issue, face, today, largest_single_amount(face.currency))
    if refusal is not None:
        field = "product_code" if refusal in _ISSUE_REFUSALS else "face_minor"
        raise invalid(field, refusal.value)
    return view, quote(view.issue, face, view.price, today)


async def client_bond_quote(
    session: AsyncSession, code: str, face_minor: int, clock: Clock
) -> ClientQuote:
    view = await issue_view(session, code)
    if view.issue.status is BondStatus.DRAFT:
        raise ApplicationError("not_found")
    _, found = await staff_quote(session, code, Money(face_minor, view.issue.currency), clock)
    return client_quote(found)


async def issue_view(session: AsyncSession, code: str) -> IssueView:
    row = (await session.execute(_ISSUE, {"code": code})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _issue_view(row)


async def issue_view_by_id(session: AsyncSession, issue_id: str) -> IssueView:
    row = (await session.execute(_ISSUE_BY_ID, {"id": issue_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _issue_view(row)


async def list_changes(
    session: AsyncSession,
    staff: Actor,
    *,
    status: BondChangeStatus | None = None,
    limit: int = MAX_CHANGES,
) -> list[ChangeView]:
    _staff_only(staff)
    rows = await session.execute(
        _CHANGES,
        {"status": status.value if status else None, "limit": max(1, min(limit, MAX_CHANGES))},
    )
    return [_change_view(row) for row in rows.all()]


# Proposals --------------------------------------------------------------------------------------


async def propose_issue(
    session: AsyncSession, staff: Actor, new: NewIssue, reason: str, clock: Clock
) -> ChangeView:
    """Propose a new issue with its first price. It is a draft once a second person approves."""
    _staff_only(staff)
    _reason(reason)
    try:
        target = BondIssue(
            code=new.code,
            isin=new.isin,
            name=new.name,
            currency=new.currency,
            coupon_bps=new.coupon_bps,
            tax_bps=new.tax_bps,
            issue_date=new.issue_date,
            maturity_date=new.maturity_date,
            minimum_face=Money(new.minimum_face_minor, new.currency),
            face_step=Money(new.face_step_minor, new.currency),
        )
        code_taken, isin_taken = await _taken(session, target)
        refusal = check_create(target, new.price, code_taken=code_taken, isin_taken=isin_taken)
    except InvalidIssue as error:
        raise invalid("issue", "invalid") from error
    if refusal is not None:
        raise _refused(refusal)
    bond_type = await product_types.type_for(session, new.type_code, ReturnModel.TREASURY_BOND)
    change_id = push_id()
    await session.execute(
        _INSERT_CHANGE,
        {
            **_values(target),
            "type_id": bond_type.id,
            "id": change_id,
            "action": BondChangeAction.CREATE.value,
            "issue_id": None,
            "base_version": None,
            "price": new.price,
            "reason": reason,
            "proposed_by": staff.id,
            "now": clock.now(),
        },
    )
    return await _proposed(session, staff, change_id, reason, clock)


async def propose_price(
    session: AsyncSession, staff: Actor, code: str, price: int, reason: str, clock: Clock
) -> ChangeView:
    """Propose today's price for an issue. It is in force once a second person approves."""
    _staff_only(staff)
    _reason(reason)
    view = await _locked_issue(session, code)
    try:
        refusal = check_price_change(_current(view), price)
    except InvalidIssue as error:
        raise invalid("price", "out_of_range") from error
    if refusal is not None:
        raise _refused(refusal)
    return await _propose_existing(
        session, staff, view, BondChangeAction.PRICE, price, reason, clock
    )


async def propose_status(
    session: AsyncSession, staff: Actor, code: str, to: BondStatus, reason: str, clock: Clock
) -> ChangeView:
    """Propose pausing (`paused`) or opening (`live`) an issue."""
    _staff_only(staff)
    _reason(reason)
    if to is BondStatus.DRAFT:
        raise invalid("to", "unknown_status")
    view = await _locked_issue(session, code)
    action = BondChangeAction.OPEN if to is BondStatus.LIVE else BondChangeAction.PAUSE
    refusal = check_status_change(
        _current(view), action, can_be_bought=_can_be_bought(view, business_date(clock.now()))
    )
    if refusal is not None:
        raise _refused(refusal)
    return await _propose_existing(session, staff, view, action, None, reason, clock)


# Decisions --------------------------------------------------------------------------------------


async def approve(session: AsyncSession, staff: Actor, change_id: str, clock: Clock) -> ChangeView:
    """Approve as the second person. The issue or price is written in this same transaction."""
    view = await _for_decision(session, staff, change_id)
    now = clock.now()
    issue: IssueView | None = None
    if view.action is BondChangeAction.CREATE:
        target = view.target
        if target is None or view.price is None:
            raise ApplicationError("conflict")
        await session.execute(_ADVISORY, {"lock": f"finvest.bonds:code:{target.code}"})
        code_taken, isin_taken = await _taken(session, target, except_change=view.id)
        refusal = check_create(target, view.price, code_taken=code_taken, isin_taken=isin_taken)
    else:
        issue = await _locked_issue(session, view.code)
        current = _current(issue)
        if issue.version != view.base_version:
            raise invalid("issue", Refusal.STALE.value)
        if view.action is BondChangeAction.PRICE:
            refusal = check_price_change(current, view.price or 0)
        else:
            refusal = check_status_change(
                current, view.action, can_be_bought=_can_be_bought(issue, business_date(now))
            )
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {"id": view.id, "status": "approved", "decided_by": staff.id, "now": now, "note": None},
    )
    if view.action is BondChangeAction.CREATE and view.target is not None:
        issue_id = push_id()
        await session.execute(
            _INSERT_ISSUE,
            {**_values(view.target), "id": issue_id, "change_id": view.id, "now": now},
        )
        await _insert_price(session, issue_id, view, now)
    elif issue is not None and view.action is BondChangeAction.PRICE:
        await _insert_price(session, issue.id, view, now)
    elif issue is not None:
        updated = await session.execute(
            _UPDATE_STATUS,
            {
                "id": issue.id,
                "status": status_after(view.action, issue.issue.status).value,
                "change_id": view.id,
                "version": issue.version,
                "now": now,
            },
        )
        if updated.rowcount != 1:  # type: ignore[attr-defined]
            raise ApplicationError("conflict")
    decided = await _change(session, view.id)
    await _audit(
        session, staff, "approval", "bonds.change.approved", decided, clock, reason=decided.reason
    )
    return decided


async def send_back(
    session: AsyncSession, staff: Actor, change_id: str, note: str, clock: Clock
) -> ChangeView:
    if not valid_text(note):
        raise invalid("note", Refusal.NOTE_REQUIRED.value)
    view = await _for_decision(session, staff, change_id)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": "sent_back",
            "decided_by": staff.id,
            "now": clock.now(),
            "note": note,
        },
    )
    decided = await _change(session, view.id)
    await _audit(session, staff, "approval", "bonds.change.sent_back", decided, clock, reason=note)
    return decided


async def withdraw(session: AsyncSession, staff: Actor, change_id: str, clock: Clock) -> ChangeView:
    _staff_only(staff)
    await session.execute(_LOCK_CHANGE, {"id": change_id})
    view = await _change(session, change_id)
    refusal = check_withdrawal(status=view.status, proposed_by=view.proposed_by, by=_id(staff))
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
    decided = await _change(session, view.id)
    await _audit(session, staff, "change", "bonds.change.withdrawn", decided, clock)
    return decided


# Internals --------------------------------------------------------------------------------------

_ISSUE_REFUSALS = {BuyRefusal.ISSUE_NOT_OPEN, BuyRefusal.TOO_CLOSE_TO_MATURITY, BuyRefusal.NO_PRICE}


def invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    if actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


def _reason(reason: str) -> None:
    if not valid_text(reason):
        raise invalid("reason", Refusal.REASON_REQUIRED.value)


async def _issues(session: AsyncSession, statuses: tuple[BondStatus, ...]) -> list[IssueView]:
    rows = await session.execute(_ISSUES, {"statuses": [s.value for s in statuses]})
    return [_issue_view(row) for row in rows.all()]


def _issue_view(row: Row[Any]) -> IssueView:
    currency = Currency(row.currency)
    return IssueView(
        id=row.id,
        issue=BondIssue(
            code=row.code,
            isin=row.isin,
            name=row.name,
            currency=currency,
            coupon_bps=row.coupon_bps,
            tax_bps=row.tax_bps,
            issue_date=row.issue_date,
            maturity_date=row.maturity_date,
            minimum_face=Money(row.minimum_face_minor, currency),
            face_step=Money(row.face_step_minor, currency),
            status=BondStatus(row.status),
        ),
        version=row.version,
        price=row.price,
        priced_at=row.priced_at,
    )


def _can_be_bought(view: IssueView, today: date) -> bool:
    return len(remaining_coupons(view.issue, today)) >= 2


def _client(view: IssueView, today: date) -> ClientIssue:
    issue = view.issue
    left = remaining_coupons(issue, today)
    return ClientIssue(
        code=issue.code,
        isin=issue.isin,
        name=issue.name,
        currency=issue.currency,
        coupon_percent=coupon_label(issue.coupon_bps, issue.tax_bps),
        issue_date=issue.issue_date,
        maturity_date=issue.maturity_date,
        years=issue.years,
        next_coupon_date=left[0] if left else None,
        coupons_left=len(left),
        minimum_face=issue.minimum_face,
        face_step=issue.face_step,
        price=view.price,
        status=issue.status,
        can_buy=issue.status is BondStatus.LIVE
        and view.price is not None
        and _can_be_bought(view, today),
    )


def _current(view: IssueView) -> Current:
    return Current(issue=view.issue, version=view.version, price=view.price)


async def _locked_issue(session: AsyncSession, code: str) -> IssueView:
    await session.execute(_ADVISORY, {"lock": f"finvest.bonds:issue:{code}"})
    return await issue_view(session, code)


async def _taken(
    session: AsyncSession, issue: BondIssue, *, except_change: str | None = None
) -> tuple[bool, bool]:
    await session.execute(_ADVISORY, {"lock": f"finvest.bonds:code:{issue.code}"})
    await session.execute(_ADVISORY, {"lock": f"finvest.bonds:isin:{issue.isin}"})
    row = (
        await session.execute(
            _TAKEN, {"code": issue.code, "isin": issue.isin, "except": except_change}
        )
    ).one()
    return bool(row.code), bool(row.isin)


async def _propose_existing(
    session: AsyncSession,
    staff: Actor,
    view: IssueView,
    action: BondChangeAction,
    price: int | None,
    reason: str,
    clock: Clock,
) -> ChangeView:
    if (await session.execute(_WAITING, {"issue_id": view.id})).scalar_one():
        raise _refused(Refusal.ALREADY_WAITING)
    change_id = push_id()
    await session.execute(
        _INSERT_CHANGE,
        {
            **dict.fromkeys(_values(view.issue)),
            "type_id": None,
            "id": change_id,
            "action": action.value,
            "issue_id": view.id,
            "base_version": view.version,
            "price": price,
            "reason": reason,
            "proposed_by": staff.id,
            "now": clock.now(),
        },
    )
    return await _proposed(session, staff, change_id, reason, clock)


async def _proposed(
    session: AsyncSession, staff: Actor, change_id: str, reason: str, clock: Clock
) -> ChangeView:
    view = await _change(session, change_id)
    await _audit(session, staff, "change", "bonds.change.proposed", view, clock, reason=reason)
    return view


async def _for_decision(session: AsyncSession, staff: Actor, change_id: str) -> ChangeView:
    _staff_only(staff)
    await session.execute(_LOCK_CHANGE, {"id": change_id})
    view = await _change(session, change_id)
    refusal = check_decision(
        status=view.status, proposed_by=view.proposed_by, decided_by=_id(staff)
    )
    if refusal is not None:
        raise _refused(refusal)
    return view


async def _insert_price(
    session: AsyncSession, issue_id: str, view: ChangeView, now: datetime
) -> None:
    await session.execute(
        _INSERT_PRICE,
        {
            "id": push_id(),
            "issue_id": issue_id,
            "price": view.price,
            "change_id": view.id,
            "now": now,
        },
    )


async def _change(session: AsyncSession, change_id: str) -> ChangeView:
    row = (await session.execute(_CHANGE, {"id": change_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _change_view(row)


def _change_view(row: Row[Any]) -> ChangeView:
    target = None
    if row.action == BondChangeAction.CREATE.value:
        currency = Currency(row.currency)
        target = BondIssue(
            code=row.code,
            isin=row.isin,
            name=row.name,
            currency=currency,
            coupon_bps=row.coupon_bps,
            tax_bps=row.tax_bps,
            issue_date=row.issue_date,
            maturity_date=row.maturity_date,
            minimum_face=Money(row.minimum_face_minor, currency),
            face_step=Money(row.face_step_minor, currency),
        )
    return ChangeView(
        id=row.id,
        action=BondChangeAction(row.action),
        code=row.issue_code,
        target=target,
        price=row.price,
        base_version=row.base_version,
        reason=row.reason,
        status=BondChangeStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


def _values(issue: BondIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "isin": issue.isin,
        "name": issue.name,
        "currency": issue.currency.value,
        "coupon_bps": issue.coupon_bps,
        "tax_bps": issue.tax_bps,
        "issue_date": issue.issue_date,
        "maturity_date": issue.maturity_date,
        "minimum_face_minor": issue.minimum_face.minor,
        "face_step_minor": issue.face_step.minor,
    }


_FIELDS = {
    Refusal.REASON_REQUIRED: "reason",
    Refusal.NOTE_REQUIRED: "note",
    Refusal.CODE_TAKEN: "code",
    Refusal.ISIN_TAKEN: "isin",
    Refusal.NOT_A_DRAFT: "status",
    Refusal.ALREADY_LIVE: "to",
    Refusal.NOT_LIVE: "to",
    Refusal.NO_PRICE: "to",
    Refusal.SAME_PRICE: "price",
    Refusal.CURRENCY_NOT_OPEN: "currency",
    Refusal.MATURED: "to",
    Refusal.STALE: "issue",
}


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal in _FIELDS:
        return invalid(_FIELDS[refusal], refusal.value)
    if refusal in (Refusal.NOT_WAITING, Refusal.ALREADY_WAITING):
        return ApplicationError("conflict")
    return ApplicationError("forbidden")


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    view: ChangeView,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    # Bond issues and prices are business settings, not personal data.
    details: dict[str, object] = {"bond": view.code, "action": view.action.value}
    if view.price is not None:
        details["price"] = view.price
    if view.target is not None:
        details.update(
            isin=view.target.isin,
            coupon_bps=view.target.coupon_bps,
            tax_bps=view.target.tax_bps,
            maturity_date=view.target.maturity_date.isoformat(),
        )
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=CHANGE_RESOURCE,
            resource_id=view.id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details,
        ),
        clock,
    )
