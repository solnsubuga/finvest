"""Staff routes for taking back a deposit credited by mistake. See `reversals.py`.

Seeing reversals needs `reconciliation.view`, like the payments they come from. Proposing,
approving, sending back and withdrawing need `deposit_reassignments.change`, the permission for
putting a wrongly credited payment right, and approving, which moves money, also needs the
second step from the last few minutes. Every `POST` needs an `Idempotency-Key`.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payments import reversals as service
from app.modules.payments.domain.reassignments import MAX_TEXT_LENGTH
from app.modules.payments.domain.reversals import ReversalStatus
from app.modules.payments.routes import MoneyView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

PERMISSION = "deposit_reassignments.change"
_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

Viewers = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
Deciders = Annotated[StaffSession, Depends(require_permission(PERMISSION))]
# Approving takes money back from a client's books, so it asks for a passkey or app code from
# the last few minutes. The console answers `confirm_it_is_you` with its dialog.
Approvers = Annotated[StaffSession, Depends(require_confirmed(PERMISSION))]

Status = Literal["proposed", "approved", "sent_back", "withdrawn", "done", "failed"]
Refusal = Literal[
    "not_confirmed", "not_for_an_investment", "already_reversed", "not_a_payment", "money_moved_on"
]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class ReversalOut(Contract):
    id: PushId
    status: Status
    # The confirmed deposit taken back, and its client and investment.
    deposit_id: PushId
    client_id: PushId
    investment_id: PushId
    amount: MoneyView
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    # Why it was sent back.
    decision_note: str | None
    # When the worker finished it, the entry that reversed the deposit's, or why it failed.
    done_at: datetime | None
    entry_id: PushId | None
    failure_code: str | None


class ReversalDetailOut(Contract):
    reversal: ReversalOut
    # The deposit's way of paying: mtn, airtel or bank_transfer.
    method: str
    # What waits for the investment now. Reversing needs at least the deposit's amount.
    waiting: MoneyView
    # For an open proposal, why approving would be refused right now. None if it would go
    # through, and for a decided one.
    refusal: Refusal | None
    # The person reading may approve or send it back: they hold the permission and did not
    # propose it. The proposer may only withdraw it.
    can_decide: bool
    yours: bool


class ReversalProposeIn(Contract):
    deposit_id: PushId
    reason: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


class ReversalSendBackIn(Contract):
    note: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


def _out(reversal: service.Reversal) -> ReversalOut:
    return ReversalOut(
        id=reversal.id,
        status=reversal.status.value,
        deposit_id=reversal.deposit_id,
        client_id=reversal.client_id,
        investment_id=reversal.investment_id,
        amount=MoneyView(minor=reversal.amount.minor, currency=reversal.amount.currency.value),
        reason=reversal.reason,
        proposed_by=reversal.proposed_by,
        proposed_at=reversal.proposed_at,
        decided_by=reversal.decided_by,
        decided_at=reversal.decided_at,
        decision_note=reversal.decision_note,
        done_at=reversal.done_at,
        entry_id=reversal.entry_id,
        failure_code=reversal.failure_code,
    )


def _detail(detail: service.ReversalDetail, staff: StaffSession) -> ReversalDetailOut:
    reversal = detail.reversal
    yours = reversal.proposed_by == staff.staff_id
    return ReversalDetailOut(
        reversal=_out(reversal),
        method=detail.deposit.method.value,
        waiting=MoneyView(minor=detail.waiting.minor, currency=detail.waiting.currency.value),
        refusal=detail.refusal.value if detail.refusal else None,
        can_decide=reversal.status.is_open and not yours and PERMISSION in staff.permissions,
        yours=yours,
    )


router = APIRouter(prefix="/staff", tags=["staff deposit reversals"], responses=_REFUSALS)


@router.get("/payments/reversals")
async def list_reversals(
    staff: Viewers,
    database: Db,
    status: Annotated[Status | None, Query()] = None,
    deposit_id: Annotated[PushId | None, Query()] = None,
) -> list[ReversalOut]:
    """Reversals, newest first, at most 200."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.list_reversals(
            session,
            staff.actor,
            status=ReversalStatus(status) if status else None,
            deposit_id=deposit_id,
            limit=200,
        )
    return [_out(reversal) for reversal in found]


@router.get("/payments/reversals/{reversal_id}")
async def get_reversal(
    reversal_id: PushId, staff: Viewers, database: Db, clock: Time
) -> ReversalDetailOut:
    """One reversal, the money waiting now and whether approving would go through."""
    return await _read(database, staff, reversal_id, clock)


async def _read(
    database: Database, staff: StaffSession, reversal_id: str, clock: Clock
) -> ReversalDetailOut:
    async with database.actor_transaction(staff.actor) as session:
        detail = await service.get_reversal(session, staff.actor, reversal_id, clock)
    return _detail(detail, staff)


@router.post("/payments/reversals", status_code=201)
async def propose_reversal(
    body: ReversalProposeIn, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> ReversalDetailOut:
    """Propose taking back a deposit credited by mistake. Nothing moves until a different
    member of staff approves.

    The whole amount must still wait for the investment (`422` `money_moved_on` if not: unwind
    a started investment first). `409` if the deposit already has a reversal.
    """
    async with database.actor_transaction(staff.actor) as session:
        proposed = await service.propose_reversal(
            session,
            staff.actor,
            deposit_id=body.deposit_id,
            reason=body.reason,
            idempotency_key=key,
            clock=clock,
        )
    return await _read(database, staff, proposed.id, clock)


@router.post("/payments/reversals/{reversal_id}/approve")
async def approve_reversal(
    reversal_id: PushId, staff: Approvers, key: IdempotencyKey, database: Db, clock: Time
) -> ReversalDetailOut:
    """Approve someone else's proposal. The worker takes the money back moments later. The
    proposer gets `403`. `422` with a code if the money moved on since it was proposed: the
    proposal stays open."""
    async with database.actor_transaction(staff.actor) as session:
        await service.approve_reversal(
            session, staff.actor, reversal_id, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, reversal_id, clock)


@router.post("/payments/reversals/{reversal_id}/send-back")
async def send_back_reversal(
    reversal_id: PushId,
    body: ReversalSendBackIn,
    staff: Deciders,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
) -> ReversalDetailOut:
    """Decline someone else's proposal, with a note. Nothing moves. The proposer gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.send_back_reversal(
            session, staff.actor, reversal_id, body.note, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, reversal_id, clock)


@router.post("/payments/reversals/{reversal_id}/withdraw")
async def withdraw_reversal(
    reversal_id: PushId, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> ReversalDetailOut:
    """Take back your own proposal before anyone decides it. Anyone else gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.withdraw_reversal(
            session, staff.actor, reversal_id, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, reversal_id, clock)


routers: list[APIRouter] = [router]
