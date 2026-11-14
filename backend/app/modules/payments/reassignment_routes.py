"""Staff routes for moving a bank transfer credited to the wrong client. See `reassignments.py`.

Seeing reassignments needs `reconciliation.view`, like the bank transfers they come from.
Proposing, approving, sending back and withdrawing need `deposit_reassignments.change`, and
approving, which moves the money, also needs the second step from the last few minutes. Every
`POST` needs an `Idempotency-Key`.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payments import reassignments as service
from app.modules.payments.domain.reassignments import MAX_TEXT_LENGTH, ReassignmentStatus
from app.modules.payments.routes import MoneyView, StatementLineOut
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

Viewers = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
Deciders = Annotated[StaffSession, Depends(require_permission("deposit_reassignments.change"))]
# Approving moves money between two clients, so it asks for a passkey or app code from the last
# few minutes. The console answers `confirm_it_is_you` with the confirm-it-is-you dialog.
Approvers = Annotated[StaffSession, Depends(require_confirmed("deposit_reassignments.change"))]

Status = Literal["proposed", "applied", "sent_back", "withdrawn"]
Refusal = Literal[
    "mobile_money_refund_instead",
    "not_confirmed",
    "not_for_an_investment",
    "not_matched_to_a_line",
    "money_moved_on",
    "same_client",
    "not_waiting_for_payment",
    "amount_differs_from_investment",
    "already_has_a_payment",
]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class ReassignmentOut(Contract):
    id: PushId
    status: Status
    # The confirmed bank transfer credited to the wrong client, and what it paid for.
    deposit_id: PushId
    from_client_id: PushId
    from_investment_id: PushId
    # The client whose money it was, and their investment that it pays for.
    to_client_id: PushId
    to_investment_id: PushId
    amount: MoneyView
    # The evidence: the bank statement line the original review matched.
    statement_line_id: PushId
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    # Why it was sent back.
    decision_note: str | None
    # Once applied: the ledger entry that moved the money and the right client's new payment.
    entry_id: PushId | None
    new_deposit_id: PushId | None


class PartyOut(Contract):
    client_id: PushId
    # None for a client without an approved account, which cannot happen for a paying client.
    name: str | None
    account_number: str | None


class ReassignmentDetailOut(Contract):
    reassignment: ReassignmentOut
    from_client: PartyOut
    to_client: PartyOut
    # The statement line, for the second person to check. None if it can no longer be read.
    line: StatementLineOut | None
    # The line's reference carries this client's Finvest account number. A hint, not proof:
    # banks cut and change references.
    reference_names_to_client: bool
    reference_names_from_client: bool
    # For an open proposal, why approving would be refused right now. None if it would go
    # through, and for a decided one.
    refusal: Refusal | None
    # The person reading may approve or send it back: they hold the permission and did not
    # propose it. The proposer may only withdraw it.
    can_decide: bool
    yours: bool


class ProposeIn(Contract):
    deposit_id: PushId
    to_investment_id: PushId
    reason: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


class SendBackIn(Contract):
    note: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


def _out(reassignment: service.Reassignment) -> ReassignmentOut:
    return ReassignmentOut(
        id=reassignment.id,
        status=reassignment.status.value,
        deposit_id=reassignment.deposit_id,
        from_client_id=reassignment.from_client_id,
        from_investment_id=reassignment.from_investment_id,
        to_client_id=reassignment.to_client_id,
        to_investment_id=reassignment.to_investment_id,
        amount=MoneyView(
            minor=reassignment.amount.minor, currency=reassignment.amount.currency.value
        ),
        statement_line_id=reassignment.statement_line_id,
        reason=reassignment.reason,
        proposed_by=reassignment.proposed_by,
        proposed_at=reassignment.proposed_at,
        decided_by=reassignment.decided_by,
        decided_at=reassignment.decided_at,
        decision_note=reassignment.decision_note,
        entry_id=reassignment.entry_id,
        new_deposit_id=reassignment.new_deposit_id,
    )


def _party(party: service.Party) -> PartyOut:
    return PartyOut(client_id=party.client_id, name=party.name, account_number=party.account_number)


def _detail(detail: service.ReassignmentDetail, staff: StaffSession) -> ReassignmentDetailOut:
    reassignment = detail.reassignment
    line = detail.line
    yours = reassignment.proposed_by == staff.staff_id
    return ReassignmentDetailOut(
        reassignment=_out(reassignment),
        from_client=_party(detail.from_client),
        to_client=_party(detail.to_client),
        line=None
        if line is None
        else StatementLineOut(
            id=line.id,
            account_reference=line.account_reference,
            value_date=line.value_date,
            amount=MoneyView(minor=line.amount.minor, currency=line.amount.currency.value),
            reference=line.reference,
            narrative=line.counterparty_hint,
        ),
        reference_names_to_client=detail.reference_names_to_client,
        reference_names_from_client=detail.reference_names_from_client,
        refusal=detail.refusal.value if detail.refusal else None,
        can_decide=reassignment.status.is_open
        and not yours
        and "deposit_reassignments.change" in staff.permissions,
        yours=yours,
    )


router = APIRouter(prefix="/staff", tags=["staff deposit reassignments"], responses=_REFUSALS)


@router.get("/payments/reassignments")
async def list_reassignments(
    staff: Viewers,
    database: Db,
    status: Annotated[Status | None, Query()] = None,
    deposit_id: Annotated[PushId | None, Query()] = None,
) -> list[ReassignmentOut]:
    """Reassignments, newest first, at most 200. `deposit_id` finds those from or to a payment."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.list_reassignments(
            session,
            staff.actor,
            status=ReassignmentStatus(status) if status else None,
            deposit_id=deposit_id,
            limit=200,
        )
    return [_out(reassignment) for reassignment in found]


@router.get("/payments/reassignments/{reassignment_id}")
async def get_reassignment(
    reassignment_id: PushId, staff: Viewers, database: Db, clock: Time
) -> ReassignmentDetailOut:
    """One reassignment, both clients, the statement line and whether approving would go through.

    Both clients are named and the bank's narrative can name the sender, so each look is written
    to the audit trail.
    """
    return await _read(database, staff, reassignment_id, clock)


async def _read(
    database: Database, staff: StaffSession, reassignment_id: str, clock: Clock
) -> ReassignmentDetailOut:
    async with database.actor_transaction(staff.actor) as session:
        detail = await service.get_reassignment(session, staff.actor, reassignment_id, clock)
    return _detail(detail, staff)


@router.post("/payments/reassignments", status_code=201)
async def propose_reassignment(
    body: ProposeIn, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> ReassignmentDetailOut:
    """Propose moving a bank transfer to another client's investment. Nothing moves until a
    different member of staff approves.

    The investment must be waiting for exactly the transfer's amount, with no payment of its
    own, and the money must still be waiting with the first client (`422` with a code if not).
    `409` if either already has an open proposal.
    """
    async with database.actor_transaction(staff.actor) as session:
        proposed = await service.propose_reassignment(
            session,
            staff.actor,
            deposit_id=body.deposit_id,
            to_investment_id=body.to_investment_id,
            reason=body.reason,
            idempotency_key=key,
            clock=clock,
        )
    return await _read(database, staff, proposed.id, clock)


@router.post("/payments/reassignments/{reassignment_id}/approve")
async def approve_reassignment(
    reassignment_id: PushId, staff: Approvers, key: IdempotencyKey, database: Db, clock: Time
) -> ReassignmentDetailOut:
    """Approve someone else's proposal. The money moves now, and the right client's investment
    starts moments later. The proposer gets `403`. `422` with a code if the money or the
    investment changed since it was proposed: the proposal stays open."""
    async with database.actor_transaction(staff.actor) as session:
        await service.approve_reassignment(
            session, staff.actor, reassignment_id, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, reassignment_id, clock)


@router.post("/payments/reassignments/{reassignment_id}/send-back")
async def send_back_reassignment(
    reassignment_id: PushId,
    body: SendBackIn,
    staff: Deciders,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
) -> ReassignmentDetailOut:
    """Decline someone else's proposal, with a note. Nothing moves. The proposer gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.send_back_reassignment(
            session, staff.actor, reassignment_id, body.note, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, reassignment_id, clock)


@router.post("/payments/reassignments/{reassignment_id}/withdraw")
async def withdraw_reassignment(
    reassignment_id: PushId, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> ReassignmentDetailOut:
    """Take back your own proposal before anyone decides it. Anyone else gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.withdraw_reassignment(
            session, staff.actor, reassignment_id, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, reassignment_id, clock)


routers: list[APIRouter] = [router]
