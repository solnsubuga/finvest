"""Paying URA, recorded by one member of staff and approved by another. Works in the caller's
transaction.

The rules are in `domain/remittances.py`. Migration 0170 holds them whatever this code does:
the second person is never the first, what was recorded never changes, and an approved
remittance names exactly the ledger entry it posted.

A lock per month and currency is taken before reading what a month still owes, so two people
recording at once can never pay URA more than the month withheld.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.ledger import closing as ledger_closing
from app.modules.ledger import service as ledger
from app.modules.ledger import statements
from app.modules.ledger.domain.errors import DayClosed, InsufficientFunds
from app.modules.tax import withholding
from app.modules.tax.domain.periods import month_end, month_label, month_of, next_month
from app.modules.tax.domain.remittances import (
    BANK_TRUST,
    Proposal,
    Refusal,
    RemittanceStatus,
    accounts,
    check_decision,
    check_outstanding,
    check_proposal,
    posting,
)
from app.modules.tax.domain.withholding import TAX_PAYABLE, MonthSummary, summaries
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

RESOURCE = "tax_remittance"

_COLUMNS = (
    "id, month, currency, amount_minor, ura_reference, bank_reference, paid_on, note, status, "
    "proposed_by, proposed_at, decided_by, decided_at, decision_reason, ledger_entry_id"
)
_GET = text(f"SELECT {_COLUMNS} FROM tax_remittances WHERE id = :id")  # noqa: S608
_LOCK = text(f"SELECT {_COLUMNS} FROM tax_remittances WHERE id = :id FOR UPDATE")  # noqa: S608
_LIST = text(
    f"SELECT {_COLUMNS} FROM tax_remittances "  # noqa: S608
    "WHERE currency = :currency ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM tax_remittances "  # noqa: S608
    "WHERE status = 'proposed' ORDER BY proposed_at, id LIMIT :limit"
)
_BY_MONTH = text(
    "SELECT month, status, coalesce(sum(amount_minor), 0)::bigint AS amount_minor "
    "FROM tax_remittances WHERE currency = :currency AND month BETWEEN :date_from AND :date_to "
    "AND status <> 'declined' GROUP BY month, status"
)
_INSERT = text(
    "INSERT INTO tax_remittances (id, month, currency, amount_minor, ura_reference, "
    "bank_reference, paid_on, note, status, proposed_by, proposed_at) VALUES (:id, :month, "
    ":currency, :amount, :ura, :bank, :paid_on, :note, 'proposed', :by, :now)"
)
_DECIDE = text(
    "UPDATE tax_remittances SET status = :status, decided_by = :by, decided_at = :now, "
    "decision_reason = :reason, ledger_entry_id = :entry WHERE id = :id"
)
_PAID_OUT = text(
    "SELECT ledger_entry_id, bank_reference, currency, amount_minor, paid_on "
    "FROM tax_remittances WHERE status = 'approved' AND paid_on BETWEEN :date_from AND :date_to "
    "ORDER BY paid_on, id"
)
_LOCK_MONTH = text(
    "SELECT pg_advisory_xact_lock(hashtext('tax_remittance'), hashtext(:currency || :month))"
)


@dataclass(frozen=True, slots=True)
class RemittanceView:
    id: str
    month: date
    amount: Money
    ura_reference: str
    bank_reference: str
    paid_on: date
    note: str
    status: RemittanceStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    ledger_entry_id: str | None


@dataclass(frozen=True, slots=True)
class BankPayment:
    """An approved payment to URA, as the trust bank account's statement should show it."""

    ledger_entry_id: str
    bank_reference: str
    amount: Money
    paid_on: date


# Queries -----------------------------------------------------------------------------------------


async def expected_bank_movements(
    session: AsyncSession, *, date_from: date, date_to: date
) -> list[BankPayment]:
    """Approved payments to URA paid between two days, for reconciliation to pair with the
    trust bank account's statement by the bank's reference. Empty outside a staff or worker
    transaction."""
    rows = await session.execute(_PAID_OUT, {"date_from": date_from, "date_to": date_to})
    return [
        BankPayment(
            ledger_entry_id=row.ledger_entry_id,
            bank_reference=row.bank_reference,
            amount=Money(int(row.amount_minor), Currency(row.currency)),
            paid_on=row.paid_on,
        )
        for row in rows.all()
    ]


async def get_remittance(session: AsyncSession, remittance_id: str) -> RemittanceView:
    row = (await session.execute(_GET, {"id": remittance_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def remittances(
    session: AsyncSession, currency: Currency, *, limit: int = 50
) -> list[RemittanceView]:
    """Remittances in a currency, newest first."""
    rows = await session.execute(_LIST, {"currency": currency.value, "limit": limit})
    return [_view(row) for row in rows.all()]


async def waiting(session: AsyncSession, *, limit: int = 100) -> list[RemittanceView]:
    """Remittances waiting for a second person, oldest first."""
    rows = await session.execute(_WAITING, {"limit": limit})
    return [_view(row) for row in rows.all()]


async def month_summary(session: AsyncSession, currency: Currency, month: date) -> MonthSummary:
    found = await month_summaries(session, currency, date_from=month, date_to=month)
    return found[0]


async def month_summaries(
    session: AsyncSession, currency: Currency, *, date_from: date, date_to: date
) -> list[MonthSummary]:
    """Each month from the one `date_from` is in to the one `date_to` is in, newest first:
    what was withheld, what was paid to URA for it, and what is still to pay."""
    first, last = month_of(date_from), month_of(date_to)
    months: list[date] = []
    cursor = first
    while cursor <= last:
        months.append(cursor)
        cursor = next_month(cursor)
    withheld = await withholding.withheld_by_month(
        session, currency, date_from=first, date_to=month_end(last)
    )
    rows = await session.execute(
        _BY_MONTH, {"currency": currency.value, "date_from": first, "date_to": last}
    )
    remitted: dict[date, Money] = {}
    pending: dict[date, Money] = {}
    for row in rows.all():
        into = remitted if row.status == RemittanceStatus.APPROVED.value else pending
        into[row.month] = Money(int(row.amount_minor), currency)
    return summaries(months, withheld, remitted, pending, currency)


async def held_for_ura(session: AsyncSession, currency: Currency) -> Money:
    """Tax withheld and not yet paid to URA, across every month."""
    return await statements.total_balance(session, TAX_PAYABLE, currency)


# Commands ----------------------------------------------------------------------------------------


async def propose(
    session: AsyncSession,
    staff: Actor,
    *,
    month: date,
    amount: Money,
    ura_reference: str,
    bank_reference: str,
    paid_on: date,
    note: str,
    clock: Clock,
) -> RemittanceView:
    """Record a payment to URA that has left the trust account, for a second member of staff to
    approve. Nothing is posted yet."""
    _staff_only(staff)
    proposal = Proposal(month, amount, ura_reference, bank_reference, paid_on, note)
    refusal = check_proposal(proposal, business_date(clock.now()))
    if refusal is not None:
        raise _refused(refusal)
    closed = await ledger_closing.closed_through(session)
    if closed is not None and paid_on <= closed:
        raise _invalid("paid_on", "paid_on_in_a_closed_day")
    await _check_outstanding(session, month, amount)
    remittance_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": remittance_id,
                    "month": month,
                    "currency": amount.currency.value,
                    "amount": amount.minor,
                    "ura": ura_reference.strip(),
                    "bank": bank_reference.strip(),
                    "paid_on": paid_on,
                    "note": note.strip(),
                    "by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        field = "ura_reference" if "ura_reference" in str(error.orig) else "bank_reference"
        raise _invalid(field, "already_recorded") from error
    await _audit(session, staff, "change", "tax.remittance.proposed", remittance_id, clock,
                 reason=note.strip())  # fmt: skip
    return await get_remittance(session, remittance_id)


async def approve(
    session: AsyncSession, staff: Actor, remittance_id: str, clock: Clock
) -> RemittanceView:
    """The second person, having checked URA's receipt and the bank statement. Posts it, once."""
    view = await _for_decision(session, staff, remittance_id)
    # Counted again, less this one, in case the month's figures moved since it was recorded.
    await _check_outstanding(session, view.month, view.amount, waiting_already=view.amount)
    currency = view.amount.currency
    opened = {
        code: (await ledger.open_account(session, spec, clock)).id
        for code, spec in accounts(currency).items()
    }
    draft = posting(
        view.id,
        view.amount,
        view.paid_on,
        payable_account_id=opened[TAX_PAYABLE],
        bank_account_id=opened[BANK_TRUST],
    )
    async with session.begin_nested():
        try:
            posted = await ledger.post_entry(session, draft, clock)
        except InsufficientFunds:
            raise _refused(Refusal.NOT_ENOUGH_HELD) from None
        except DayClosed:
            raise _invalid("paid_on", "day_closed_decline_and_record_again") from None
        await session.execute(
            _DECIDE,
            {
                "id": view.id,
                "status": RemittanceStatus.APPROVED.value,
                "by": staff.id,
                "now": clock.now(),
                "reason": None,
                "entry": posted.id,
            },
        )
    await _audit(session, staff, "approval", "tax.remittance.approved", view.id, clock,
                 reason="Second signature on a payment to URA")  # fmt: skip
    return await get_remittance(session, view.id)


async def decline(
    session: AsyncSession, staff: Actor, remittance_id: str, reason: str, clock: Clock
) -> RemittanceView:
    """The second person refuses it, with a reason. Nothing is posted."""
    if not reason.strip():
        raise _refused(Refusal.REASON_REQUIRED)
    view = await _for_decision(session, staff, remittance_id)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": RemittanceStatus.DECLINED.value,
            "by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
            "entry": None,
        },
    )
    await _audit(session, staff, "approval", "tax.remittance.declined", view.id, clock,
                 reason=reason.strip())  # fmt: skip
    return await get_remittance(session, view.id)


# Internals ---------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


async def _for_decision(session: AsyncSession, staff: Actor, remittance_id: str) -> RemittanceView:
    _staff_only(staff)
    assert staff.id is not None
    row = (await session.execute(_GET, {"id": remittance_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    # The month's lock first, then the row's, the same order as recording takes them.
    await _lock_month(session, row.currency, row.month)
    row = (await session.execute(_LOCK, {"id": remittance_id})).one()
    view = _view(row)
    refusal = check_decision(view.status, view.proposed_by, staff.id)
    if refusal is not None:
        raise _refused(refusal)
    return view


async def _lock_month(session: AsyncSession, currency: str, month: date) -> None:
    await session.execute(_LOCK_MONTH, {"currency": currency, "month": month_label(month)})


async def _check_outstanding(
    session: AsyncSession, month: date, amount: Money, *, waiting_already: Money | None = None
) -> None:
    await _lock_month(session, amount.currency.value, month)
    summary = await month_summary(session, amount.currency, month)
    outstanding = summary.outstanding + (waiting_already or Money.zero(amount.currency))
    refusal = check_outstanding(amount, outstanding)
    if refusal is not None:
        raise _refused(refusal)


def _view(row: Row[Any]) -> RemittanceView:
    return RemittanceView(
        id=row.id,
        month=row.month,
        amount=Money(row.amount_minor, Currency(row.currency)),
        ura_reference=row.ura_reference,
        bank_reference=row.bank_reference,
        paid_on=row.paid_on,
        note=row.note,
        status=RemittanceStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        ledger_entry_id=row.ledger_entry_id,
    )


_FIELDS = {
    Refusal.NOTE_REQUIRED: "note",
    Refusal.AMOUNT_NOT_POSITIVE: "amount",
    Refusal.URA_REFERENCE_REQUIRED: "ura_reference",
    Refusal.BANK_REFERENCE_REQUIRED: "bank_reference",
    Refusal.MONTH_NOT_ENDED: "month",
    Refusal.PAID_IN_THE_FUTURE: "paid_on",
    Refusal.PAID_BEFORE_THE_MONTH: "paid_on",
    Refusal.MORE_THAN_OUTSTANDING: "amount",
    Refusal.NOT_ENOUGH_HELD: "amount",
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
    remittance_id: str,
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
            resource_id=remittance_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
        ),
        clock,
    )
