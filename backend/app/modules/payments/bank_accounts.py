"""The bank account clients pay into by transfer, and the two-person change to it.

Every function works inside the caller's transaction. A proposal, its decision and their audit
events commit together or not at all. Migration 0260 holds the rules PostgreSQL enforces: the
person who decides is never the person who proposed, and a decided row never changes.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.payments.domain.bank_accounts import (
    MAX_REASON,
    BankAccount,
    InvalidBankAccount,
    checked,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency
from app.shared.push_ids import push_id

RESOURCE = "payment_bank_account"
Decision = Literal["approved", "rejected", "withdrawn"]


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    currency: Currency
    account: BankAccount
    reason: str
    proposed_by: str
    proposed_at: datetime


@dataclass(frozen=True, slots=True)
class CurrencyAccount:
    """One currency as the console's Settings screen lists it."""

    currency: Currency
    # None until a first account is approved: clients cannot pay this currency by transfer.
    in_use: BankAccount | None
    changed_at: datetime | None
    proposal: Proposal | None


_COLUMNS = "bank_name, account_name, account_number, branch"
_IN_USE = text(
    f"SELECT {_COLUMNS} FROM payment_bank_accounts "  # noqa: S608 - constant column list
    "WHERE currency = :currency AND status = 'approved' "
    "ORDER BY decided_at DESC, id DESC LIMIT 1"
)
_ALL_IN_USE = text(
    f"SELECT DISTINCT ON (currency) currency, {_COLUMNS}, decided_at "  # noqa: S608
    "FROM payment_bank_accounts WHERE status = 'approved' "
    "ORDER BY currency, decided_at DESC, id DESC"
)
_PROPOSAL_COLUMNS = f"id, currency, {_COLUMNS}, reason, proposed_by, proposed_at"
_OPEN = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM payment_bank_accounts "  # noqa: S608
    "WHERE status = 'proposed'"
)
_OPEN_BY_ID = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM payment_bank_accounts "  # noqa: S608
    "WHERE id = :id AND status = 'proposed' FOR UPDATE"
)
_INSERT = text(
    "INSERT INTO payment_bank_accounts (id, currency, bank_name, account_name, account_number, "
    "branch, reason, proposed_by, proposed_at) VALUES (:id, :currency, :bank_name, "
    ":account_name, :account_number, :branch, :reason, :staff, :now)"
)
_DECIDE = text(
    "UPDATE payment_bank_accounts SET status = :status, decided_by = :staff, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)


async def account_in_use(session: AsyncSession, currency: Currency) -> BankAccount | None:
    """The account clients are told to pay `currency` into now: the last one approved."""
    row = (await session.execute(_IN_USE, {"currency": currency.value})).one_or_none()
    return None if row is None else _account(row)


async def list_accounts(session: AsyncSession) -> list[CurrencyAccount]:
    """Every currency, its account in use and any change waiting for a decision."""
    in_use = {row.currency: row for row in (await session.execute(_ALL_IN_USE)).all()}
    proposals = {row.currency: _proposal(row) for row in (await session.execute(_OPEN)).all()}
    listed = []
    for currency in Currency:
        row = in_use.get(currency.value)
        listed.append(
            CurrencyAccount(
                currency=currency,
                in_use=_account(row) if row else None,
                changed_at=row.decided_at if row else None,
                proposal=proposals.get(currency.value),
            )
        )
    return listed


async def propose(
    session: AsyncSession,
    staff: Actor,
    currency: Currency,
    *,
    bank_name: str,
    account_name: str,
    account_number: str,
    branch: str | None,
    reason: str,
    clock: Clock,
) -> Proposal:
    """Put new account details forward. Clients are shown nothing new until they are approved."""
    reason = _reason(reason)
    try:
        account = checked(
            bank_name=bank_name,
            account_name=account_name,
            account_number=account_number,
            branch=branch,
        )
    except InvalidBankAccount as error:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=[field], code=code) for field, code in error.problems],
        ) from None
    proposal = Proposal(push_id(), currency, account, reason, _staff_id(staff), clock.now())
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": proposal.id,
                    "currency": currency.value,
                    "bank_name": account.bank_name,
                    "account_name": account.account_name,
                    "account_number": account.account_number,
                    "branch": account.branch,
                    "reason": reason,
                    "staff": proposal.proposed_by,
                    "now": proposal.proposed_at,
                },
            )
    except IntegrityError:
        # A change for this currency is already waiting. It is decided or withdrawn first.
        raise ApplicationError("conflict") from None
    await _audit(session, staff, "payments.bank_account_proposed", proposal, reason, clock)
    return proposal


async def decide(
    session: AsyncSession,
    staff: Actor,
    proposal_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
) -> Proposal:
    """Approve or reject someone else's proposal, or withdraw your own."""
    reason = _reason(reason)
    row = (await session.execute(_OPEN_BY_ID, {"id": proposal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    proposal = _proposal(row)
    own = proposal.proposed_by == _staff_id(staff)
    if own != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    await session.execute(
        _DECIDE,
        {"status": decision, "staff": staff.id, "now": clock.now(), "reason": reason, "id": row.id},
    )
    await _audit(session, staff, f"payments.bank_account_{decision}", proposal, reason, clock)
    return proposal


async def _audit(
    session: AsyncSession, staff: Actor, action: str, proposal: Proposal, reason: str, clock: Clock
) -> None:
    # The details themselves are on the row, which never changes. The event says who and when.
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "rejected")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=proposal.id,
            actor=AuditActor.staff(_staff_id(staff)),
            reason=reason,
            details={"currency": proposal.currency.value},
        ),
        clock,
    )


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["reason"], code="required")]
        )
    return cleaned


def _account(row: Row[Any]) -> BankAccount:
    return BankAccount(row.bank_name, row.account_name, row.account_number, row.branch)


def _proposal(row: Row[Any]) -> Proposal:
    return Proposal(
        id=row.id,
        currency=Currency(row.currency.strip()),
        account=_account(row),
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
    )
