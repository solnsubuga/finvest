"""Coupons Bank of Uganda pays in for the dealing desk's bonds, recorded and approved by two
people. Works in the caller's transaction.

The rules are in `domain/bond_coupons.py`. Migration 0533 holds them whatever this code does:
the second person is never the first, what was recorded never changes, an issue's coupon on a
date and a bank reference are recorded once, and an approved coupon names exactly the ledger
entry it posted.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.bonds import catalogue
from app.modules.bonds.domain.issues import BondIssue, coupon_dates
from app.modules.dealing import holdings
from app.modules.dealing.domain.bond_coupons import (
    BANK_TRUST,
    COUPON_INCOME,
    CouponStatus,
    Received,
    Refusal,
    accounts,
    check_decision,
    check_record,
    expected_coupon,
    posting,
    record_date,
)
from app.modules.dealing.domain.orders import Instrument
from app.modules.ledger import closing as ledger_closing
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.errors import DayClosed
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

RESOURCE = "dealing_bond_coupon"
# How far back the list of coupons still to record looks. Older ones are long reconciled.
DUE_LOOKBACK = timedelta(days=366)

_COLUMNS = (
    "id, issue_code, currency, coupon_date, face_minor, expected_minor, amount_minor, "
    "value_date, bank_reference, note, status, recorded_by, recorded_at, decided_by, "
    "decided_at, decision_reason, ledger_entry_id"
)
_GET = text(f"SELECT {_COLUMNS} FROM dealing_bond_coupons WHERE id = :id")  # noqa: S608
_LOCK = text(f"SELECT {_COLUMNS} FROM dealing_bond_coupons WHERE id = :id FOR UPDATE")  # noqa: S608
_LIST = text(
    f"SELECT {_COLUMNS} FROM dealing_bond_coupons "  # noqa: S608
    "WHERE currency = :currency ORDER BY coupon_date DESC, recorded_at DESC, id DESC "
    "LIMIT :limit"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM dealing_bond_coupons "  # noqa: S608
    "WHERE status = 'recorded' ORDER BY recorded_at, id LIMIT :limit"
)
_TAKEN = text(
    "SELECT issue_code, coupon_date FROM dealing_bond_coupons "
    "WHERE status <> 'declined' AND currency = :currency AND coupon_date >= :since"
)
_INSERT = text(
    "INSERT INTO dealing_bond_coupons (id, issue_code, currency, coupon_date, face_minor, "
    "expected_minor, amount_minor, value_date, bank_reference, note, status, recorded_by, "
    "recorded_at) VALUES (:id, :code, :currency, :coupon_date, :face, :expected, :amount, "
    ":value_date, :reference, :note, 'recorded', :by, :now)"
)
_DECIDE = text(
    "UPDATE dealing_bond_coupons SET status = :status, decided_by = :by, decided_at = :now, "
    "decision_reason = :reason, ledger_entry_id = :entry WHERE id = :id"
)


@dataclass(frozen=True, slots=True)
class CouponView:
    id: str
    issue_code: str
    coupon_date: date
    # The face value held the day before the coupon date, and what it earns before any tax.
    face: Money
    expected: Money
    # What reached the trust account, on `value_date`.
    amount: Money
    value_date: date
    bank_reference: str
    note: str | None
    status: CouponStatus
    recorded_by: str
    recorded_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    ledger_entry_id: str | None


@dataclass(frozen=True, slots=True)
class CouponDue:
    """A coupon date that has come for bonds the desk held, with nothing recorded for it."""

    issue_code: str
    name: str
    coupon_date: date
    face: Money
    expected: Money


# Queries -----------------------------------------------------------------------------------------


async def get_coupon(session: AsyncSession, coupon_id: str) -> CouponView:
    row = (await session.execute(_GET, {"id": coupon_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def coupons(
    session: AsyncSession, currency: Currency, *, limit: int = 50
) -> list[CouponView]:
    """Coupons recorded in a currency, latest coupon date first."""
    rows = await session.execute(_LIST, {"currency": currency.value, "limit": limit})
    return [_view(row) for row in rows.all()]


async def waiting(session: AsyncSession, *, limit: int = 100) -> list[CouponView]:
    """Coupons waiting for a second person, oldest first."""
    rows = await session.execute(_WAITING, {"limit": limit})
    return [_view(row) for row in rows.all()]


async def due(session: AsyncSession, currency: Currency, clock: Clock) -> list[CouponDue]:
    """Coupon dates of the last year, up to today, on which the desk held bonds of an issue and
    no coupon is recorded (or only declined ones), oldest first. What the desk should see in
    the trust account and record."""
    today = business_date(clock.now())
    since = today - DUE_LOOKBACK
    taken = {
        (row.issue_code, row.coupon_date)
        for row in (
            await session.execute(_TAKEN, {"currency": currency.value, "since": since})
        ).all()
    }
    found: list[CouponDue] = []
    for view in await catalogue.every_issue(session):
        issue = view.issue
        if issue.currency is not currency:
            continue
        for coupon_date in coupon_dates(issue):
            if not since <= coupon_date <= today or (issue.code, coupon_date) in taken:
                continue
            face = await _face_on_record_date(session, issue, coupon_date)
            if face.is_positive:
                found.append(
                    CouponDue(
                        issue.code, issue.name, coupon_date, face, expected_coupon(issue, face)
                    )
                )
    return sorted(found, key=lambda each: (each.coupon_date, each.issue_code))


# Commands ----------------------------------------------------------------------------------------


async def record(
    session: AsyncSession,
    staff: Actor,
    *,
    issue_code: str,
    coupon_date: date,
    amount: Money,
    value_date: date,
    bank_reference: str,
    note: str | None,
    clock: Clock,
) -> CouponView:
    """Save a coupon from the custodian's note for a second member of staff to approve.
    Nothing is posted yet. The face value and what it earns are worked out from the desk's
    holdings, never typed in."""
    _staff_only(staff)
    issue = (await catalogue.issue_view(session, issue_code)).issue
    if amount.currency is not issue.currency:
        raise _invalid("amount", "currency_mismatch")
    face = await _face_on_record_date(session, issue, coupon_date)
    received = Received(
        coupon_date=coupon_date,
        face=face,
        expected=expected_coupon(issue, face) if face.is_positive else Money.zero(face.currency),
        amount=amount,
        value_date=value_date,
        bank_reference=bank_reference.strip(),
        note=(note or "").strip() or None,
    )
    refusal = check_record(issue, received, business_date(clock.now()))
    if refusal is not None:
        raise _refused(refusal)
    closed = await ledger_closing.closed_through(session)
    if closed is not None and value_date <= closed:
        raise _invalid("value_date", "value_date_in_a_closed_day")
    coupon_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": coupon_id,
                    "code": issue.code,
                    "currency": issue.currency.value,
                    "coupon_date": coupon_date,
                    "face": face.minor,
                    "expected": received.expected.minor,
                    "amount": amount.minor,
                    "value_date": value_date,
                    "reference": received.bank_reference,
                    # The table keeps a note only when what arrived differs.
                    "note": received.note if amount != received.expected else None,
                    "by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # The coupon, or the bank reference, is already recorded and not declined.
        field = "bank_reference" if "bank_reference" in str(error.orig) else "coupon_date"
        raise _invalid(field, "already_recorded") from error
    await _audit(session, staff, "change", "dealing.bond_coupon.recorded", coupon_id, clock)
    return await get_coupon(session, coupon_id)


async def approve(session: AsyncSession, staff: Actor, coupon_id: str, clock: Clock) -> CouponView:
    """The second person, having checked the statement. Posts the coupon to the ledger, once."""
    view = await _for_decision(session, staff, coupon_id)
    opened = {
        code: (await ledger.open_account(session, spec, clock)).id
        for code, spec in accounts(view.amount.currency).items()
    }
    draft = posting(
        view.id,
        view.amount,
        view.value_date,
        bank_account_id=opened[BANK_TRUST],
        income_account_id=opened[COUPON_INCOME],
    )
    async with session.begin_nested():
        try:
            posted = await ledger.post_entry(session, draft, clock)
        except DayClosed:
            # The day the money arrived closed after it was recorded: decline this one and
            # record it again, dated on an open day.
            raise _invalid("value_date", "day_closed_decline_and_record_again") from None
        await session.execute(
            _DECIDE,
            {
                "id": view.id,
                "status": CouponStatus.APPROVED.value,
                "by": staff.id,
                "now": clock.now(),
                "reason": None,
                "entry": posted.id,
            },
        )
    await _audit(
        session,
        staff,
        "approval",
        "dealing.bond_coupon.approved",
        view.id,
        clock,
        reason="Checked against the bank statement as the second person",
    )
    return await get_coupon(session, view.id)


async def decline(
    session: AsyncSession, staff: Actor, coupon_id: str, reason: str, clock: Clock
) -> CouponView:
    """The second person refuses it, with a reason. Nothing is posted, and it can be recorded
    again."""
    if not reason.strip():
        raise _refused(Refusal.REASON_REQUIRED)
    view = await _for_decision(session, staff, coupon_id)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": CouponStatus.DECLINED.value,
            "by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
            "entry": None,
        },
    )
    await _audit(
        session,
        staff,
        "approval",
        "dealing.bond_coupon.declined",
        view.id,
        clock,
        reason=reason.strip(),
    )
    return await get_coupon(session, view.id)


# Internals ---------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


async def _face_on_record_date(session: AsyncSession, issue: BondIssue, coupon_date: date) -> Money:
    return await holdings.face_held(
        session,
        issue.code,
        issue.currency,
        on=record_date(coupon_date),
        instrument=Instrument.BOND,
    )


async def _for_decision(session: AsyncSession, staff: Actor, coupon_id: str) -> CouponView:
    _staff_only(staff)
    assert staff.id is not None
    row = (await session.execute(_LOCK, {"id": coupon_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    view = _view(row)
    refusal = check_decision(view.status, view.recorded_by, staff.id)
    if refusal is not None:
        raise _refused(refusal)
    return view


def _view(row: Row[Any]) -> CouponView:
    currency = Currency(row.currency)
    return CouponView(
        id=row.id,
        issue_code=row.issue_code,
        coupon_date=row.coupon_date,
        face=Money(row.face_minor, currency),
        expected=Money(row.expected_minor, currency),
        amount=Money(row.amount_minor, currency),
        value_date=row.value_date,
        bank_reference=row.bank_reference,
        note=row.note,
        status=CouponStatus(row.status),
        recorded_by=row.recorded_by,
        recorded_at=row.recorded_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        ledger_entry_id=row.ledger_entry_id,
    )


_FIELDS = {
    Refusal.NOT_A_COUPON_DATE: "coupon_date",
    Refusal.COUPON_DATE_IN_FUTURE: "coupon_date",
    Refusal.NOTHING_HELD: "coupon_date",
    Refusal.AMOUNT_NOT_POSITIVE: "amount",
    Refusal.REFERENCE_REQUIRED: "bank_reference",
    Refusal.VALUE_DATE_BEFORE_COUPON: "value_date",
    Refusal.VALUE_DATE_IN_FUTURE: "value_date",
    Refusal.NOTE_REQUIRED: "note",
    Refusal.REASON_REQUIRED: "reason",
}


def _refused(refusal: Refusal) -> ApplicationError:
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
    coupon_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=RESOURCE,
            resource_id=coupon_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
        ),
        clock,
    )
