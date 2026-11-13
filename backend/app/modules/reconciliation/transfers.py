"""Transfers between Finvest's own pots. The rules are in `domain/transfers.py`; migration 0042
repeats them.

The money has already moved when a transfer is recorded: today a sweep is made in the ioTec
portal or the bank. Recording it changes nothing in the books. Approval by a second person
posts the entry. The provider's reference is kept, so the same sweep cannot be recorded twice,
and the transfer's ID is the ledger reference that reconciliation matches on both statements.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.ledger import closing as ledger_closing
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.errors import InsufficientFunds
from app.modules.reconciliation.domain import transfers as rules
from app.modules.reconciliation.domain.statements import StatementSource
from app.modules.reconciliation.domain.transfers import (
    TransferError,
    TransferStatus,
    TransferTerms,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id


@dataclass(frozen=True, slots=True)
class TransferView:
    id: str
    terms: TransferTerms
    provider_reference: str
    status: TransferStatus
    proposed_by: str
    decided_by: str | None
    ledger_entry_id: str | None
    proposed_at: datetime


_COLUMNS = (
    "id, from_source, from_reference, to_source, to_reference, currency, sent_minor, "
    "received_minor, value_date, provider_reference, status, proposed_by, decided_by, "
    "ledger_entry_id, proposed_at"
)
_INSERT = text(
    "INSERT INTO reconciliation_pot_transfers (id, from_source, from_reference, to_source, "
    "to_reference, currency, sent_minor, received_minor, value_date, provider_reference, "
    "status, proposed_by, proposed_at, reason) VALUES (:id, :from_source, :from_reference, "
    ":to_source, :to_reference, :currency, :sent_minor, :received_minor, :value_date, "
    ":provider_reference, 'proposed', :proposed_by, :now, :reason)"
)
_GET = text(f"SELECT {_COLUMNS} FROM reconciliation_pot_transfers WHERE id = :id")  # noqa: S608
_LOCK = text(
    f"SELECT {_COLUMNS} FROM reconciliation_pot_transfers WHERE id = :id FOR UPDATE"  # noqa: S608
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM reconciliation_pot_transfers "  # noqa: S608
    "WHERE status = 'proposed' ORDER BY proposed_at, id LIMIT :limit"
)
_DECIDE = text(
    "UPDATE reconciliation_pot_transfers SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_reason = :reason, ledger_entry_id = :ledger_entry_id "
    "WHERE id = :id"
)


async def propose(
    session: AsyncSession,
    staff: Actor,
    *,
    from_pot: tuple[StatementSource, str],
    to_pot: tuple[StatementSource, str],
    sent: Money,
    received: Money,
    value_date: date,
    provider_reference: str,
    reason: str,
    clock: Clock,
) -> TransferView:
    """Record a sweep that has been made. Nothing is posted until someone else approves it."""
    _staff_only(staff)
    if not reason.strip() or not provider_reference.strip():
        raise ApplicationError("validation_failed")
    if value_date > business_date(clock.now()):
        raise TransferError("transfer_in_the_future")
    closed = await ledger_closing.closed_through(session)
    if closed is not None and value_date <= closed:
        raise TransferError("transfer_in_a_closed_day")
    transfer_id = push_id()
    terms = TransferTerms(
        transfer_id,
        rules.find_pot(*from_pot),
        rules.find_pot(*to_pot),
        sent,
        received,
        value_date,
    )
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": transfer_id,
                    "from_source": terms.from_pot.source,
                    "from_reference": terms.from_pot.account_reference,
                    "to_source": terms.to_pot.source,
                    "to_reference": terms.to_pot.account_reference,
                    "currency": sent.currency.value,
                    "sent_minor": sent.minor,
                    "received_minor": received.minor,
                    "value_date": value_date,
                    "provider_reference": provider_reference.strip(),
                    "proposed_by": staff.id,
                    "now": clock.now(),
                    "reason": reason.strip(),
                },
            )
    except IntegrityError as error:
        name = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        if name == "uq_reconciliation_pot_transfers_provider_reference":
            raise TransferError("transfer_already_recorded") from None
        raise
    await _audit(session, staff, "reconciliation.transfer_proposed", transfer_id, clock, reason)
    return await get_transfer(session, transfer_id)


async def approve(
    session: AsyncSession, staff: Actor, transfer_id: str, clock: Clock
) -> TransferView:
    """The second person. Posts the transfer to the ledger, once."""
    _staff_only(staff)
    row = (await session.execute(_LOCK, {"id": transfer_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    view = _view(row)
    _may_decide(view, staff)
    terms = view.terms
    from_account = await ledger.open_account(session, terms.from_pot.ledger_account, clock)
    to_account = await ledger.open_account(session, terms.to_pot.ledger_account, clock)
    fee_account = await ledger.open_account(session, rules.fee_account(terms.sent.currency), clock)
    async with session.begin_nested():
        try:
            posted = await ledger.post_entry(
                session,
                rules.posting(
                    terms,
                    from_account_id=from_account.id,
                    to_account_id=to_account.id,
                    fee_account_id=fee_account.id,
                ),
                clock,
            )
        except InsufficientFunds:
            # The books say the pot never held that much: a person must look before posting.
            raise TransferError("transfer_more_than_the_pot_holds") from None
        await session.execute(
            _DECIDE,
            {
                "id": transfer_id,
                "status": TransferStatus.APPROVED,
                "decided_by": staff.id,
                "now": clock.now(),
                "reason": None,
                "ledger_entry_id": posted.id,
            },
        )
    await _audit(
        session, staff, "reconciliation.transfer_approved", transfer_id, clock,
        "Second signature on a transfer between pots", category="approval",
    )  # fmt: skip
    return await get_transfer(session, transfer_id)


async def decline(
    session: AsyncSession, staff: Actor, transfer_id: str, reason: str, clock: Clock
) -> TransferView:
    """The second person refuses it. Nothing is posted. A reason is required."""
    _staff_only(staff)
    if not reason.strip():
        raise ApplicationError("validation_failed")
    row = (await session.execute(_LOCK, {"id": transfer_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    _may_decide(_view(row), staff)
    await session.execute(
        _DECIDE,
        {
            "id": transfer_id,
            "status": TransferStatus.DECLINED,
            "decided_by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
            "ledger_entry_id": None,
        },
    )
    await _audit(
        session, staff, "reconciliation.transfer_declined", transfer_id, clock, reason.strip(),
        category="approval",
    )  # fmt: skip
    return await get_transfer(session, transfer_id)


async def get_transfer(session: AsyncSession, transfer_id: str) -> TransferView:
    row = (await session.execute(_GET, {"id": transfer_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def waiting(session: AsyncSession, *, limit: int = 50) -> list[TransferView]:
    """Transfers waiting for a second person, oldest first. Empty outside a staff transaction."""
    rows = await session.execute(_WAITING, {"limit": max(1, min(limit, 100))})
    return [_view(row) for row in rows.all()]


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _may_decide(view: TransferView, staff: Actor) -> None:
    if view.status is not TransferStatus.PROPOSED:
        raise TransferError("transfer_already_decided")
    if view.proposed_by == staff.id:
        raise TransferError("transfer_needs_second_person")


def _view(row: Row[tuple[object, ...]]) -> TransferView:
    currency = Currency(row.currency)
    return TransferView(
        id=row.id,
        terms=TransferTerms(
            row.id,
            rules.find_pot(StatementSource(row.from_source), row.from_reference),
            rules.find_pot(StatementSource(row.to_source), row.to_reference),
            Money(int(row.sent_minor), currency),
            Money(int(row.received_minor), currency),
            row.value_date,
        ),
        provider_reference=row.provider_reference,
        status=TransferStatus(row.status),
        proposed_by=row.proposed_by,
        decided_by=row.decided_by,
        ledger_entry_id=row.ledger_entry_id,
        proposed_at=row.proposed_at,
    )


async def _audit(
    session: AsyncSession,
    staff: Actor,
    action: str,
    transfer_id: str,
    clock: Clock,
    reason: str,
    *,
    category: str = "change",
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if category == "approval" else "change",
            action=action,
            resource_type="reconciliation_pot_transfer",
            resource_id=transfer_id,
            actor=AuditActor(staff.kind, staff.id),
            reason=reason,
        ),
        clock,
    )
