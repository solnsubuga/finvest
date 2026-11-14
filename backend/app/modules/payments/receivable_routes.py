"""Staff routes for what a client owes for a deposit credited by mistake once the money has
left: opening the receivable, and recovering or writing it off. See `receivables.py`.

Seeing them needs `reconciliation.view`. Proposing, approving, sending back and withdrawing need
`deposit_reassignments.change`, the permission for putting a wrongly credited payment right, and
approving, which moves money, also needs the second step from the last few minutes. Every
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
from app.modules.payments import receivables as service
from app.modules.payments.domain.reassignments import MAX_TEXT_LENGTH
from app.modules.payments.domain.receivables import StepKind, StepStatus
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
# Approving posts an entry against the client and Finvest's cash, so it asks for a passkey or
# app code from the last few minutes. The console answers `confirm_it_is_you` with its dialog.
Approvers = Annotated[StaffSession, Depends(require_confirmed(PERMISSION))]

Status = Literal["proposed", "applied", "sent_back", "withdrawn"]
Kind = Literal["open", "recover", "write_off"]
Refusal = Literal[
    "not_confirmed",
    "not_for_an_investment",
    "already_reversed",
    "not_a_payment",
    "unwind_first",
    "reverse_instead",
    "already_open",
    "not_open",
    "already_cleared",
]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class StepOut(Contract):
    id: PushId
    # `open` records what the client owes, `recover` clears it when the money arrives late,
    # `write_off` clears it as a loss.
    kind: Kind
    status: Status
    deposit_id: PushId
    client_id: PushId
    amount: MoneyView
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    # Why it was sent back.
    decision_note: str | None
    # Once applied, the entry it posted.
    entry_id: PushId | None


class StepDetailOut(Contract):
    step: StepOut
    # The deposit's way of paying: mtn, airtel or bank_transfer.
    method: str
    # What the client owes for this deposit now.
    owed: MoneyView
    # For an open proposal, why approving would be refused right now. None if it would go
    # through, and for a decided one.
    refusal: Refusal | None
    # The person reading may approve or send it back: they hold the permission and did not
    # propose it. The proposer may only withdraw it.
    can_decide: bool
    yours: bool


class StepProposeIn(Contract):
    kind: Kind
    deposit_id: PushId
    reason: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


class StepSendBackIn(Contract):
    note: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


def _out(step: service.Step) -> StepOut:
    return StepOut(
        id=step.id,
        kind=step.kind.value,
        status=step.status.value,
        deposit_id=step.deposit_id,
        client_id=step.client_id,
        amount=MoneyView(minor=step.amount.minor, currency=step.amount.currency.value),
        reason=step.reason,
        proposed_by=step.proposed_by,
        proposed_at=step.proposed_at,
        decided_by=step.decided_by,
        decided_at=step.decided_at,
        decision_note=step.decision_note,
        entry_id=step.entry_id,
    )


def _detail(detail: service.StepDetail, staff: StaffSession) -> StepDetailOut:
    step = detail.step
    yours = step.proposed_by == staff.staff_id
    return StepDetailOut(
        step=_out(step),
        method=detail.deposit.method.value,
        owed=MoneyView(minor=detail.owed.minor, currency=detail.owed.currency.value),
        refusal=detail.refusal.value if detail.refusal else None,
        can_decide=step.status.is_open and not yours and PERMISSION in staff.permissions,
        yours=yours,
    )


router = APIRouter(prefix="/staff", tags=["staff deposit receivables"], responses=_REFUSALS)


@router.get("/payments/receivables")
async def list_steps(
    staff: Viewers,
    database: Db,
    status: Annotated[Status | None, Query()] = None,
    deposit_id: Annotated[PushId | None, Query()] = None,
) -> list[StepOut]:
    """Receivable steps, newest first, at most 200."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.list_steps(
            session,
            staff.actor,
            status=StepStatus(status) if status else None,
            deposit_id=deposit_id,
            limit=200,
        )
    return [_out(step) for step in found]


@router.get("/payments/receivables/outstanding")
async def outstanding(staff: Viewers, database: Db) -> list[StepOut]:
    """The opening step of every receivable not yet recovered or written off, oldest first."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.outstanding(session)
    return [_out(step) for step in found]


@router.get("/payments/receivables/{step_id}")
async def get_step(step_id: PushId, staff: Viewers, database: Db, clock: Time) -> StepDetailOut:
    """One step, what the client owes now and whether approving would go through."""
    return await _read(database, staff, step_id, clock)


async def _read(
    database: Database, staff: StaffSession, step_id: str, clock: Clock
) -> StepDetailOut:
    async with database.actor_transaction(staff.actor) as session:
        detail = await service.get_step(session, staff.actor, step_id, clock)
    return _detail(detail, staff)


@router.post("/payments/receivables", status_code=201)
async def propose_step(
    body: StepProposeIn, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> StepDetailOut:
    """Propose opening a deposit's receivable, or recovering or writing it off. Nothing moves
    until a different member of staff approves.

    Opening is refused with `unwind_first` while the investment runs and `reverse_instead`
    while the money still waits for it. `409` if another step for the deposit is waiting.
    """
    async with database.actor_transaction(staff.actor) as session:
        proposed = await service.propose_step(
            session,
            staff.actor,
            kind=StepKind(body.kind),
            deposit_id=body.deposit_id,
            reason=body.reason,
            idempotency_key=key,
            clock=clock,
        )
    return await _read(database, staff, proposed.id, clock)


@router.post("/payments/receivables/{step_id}/approve")
async def approve_step(
    step_id: PushId, staff: Approvers, key: IdempotencyKey, database: Db, clock: Time
) -> StepDetailOut:
    """Approve someone else's step. Its entry posts now. The proposer gets `403`. `422` with a
    code if things changed since it was proposed: the proposal stays open."""
    async with database.actor_transaction(staff.actor) as session:
        await service.approve_step(session, staff.actor, step_id, idempotency_key=key, clock=clock)
    return await _read(database, staff, step_id, clock)


@router.post("/payments/receivables/{step_id}/send-back")
async def send_back_step(
    step_id: PushId,
    body: StepSendBackIn,
    staff: Deciders,
    key: IdempotencyKey,
    database: Db,
    clock: Time,
) -> StepDetailOut:
    """Decline someone else's step, with a note. Nothing moves. The proposer gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.send_back_step(
            session, staff.actor, step_id, body.note, idempotency_key=key, clock=clock
        )
    return await _read(database, staff, step_id, clock)


@router.post("/payments/receivables/{step_id}/withdraw")
async def withdraw_step(
    step_id: PushId, staff: Deciders, key: IdempotencyKey, database: Db, clock: Time
) -> StepDetailOut:
    """Take back your own step before anyone decides it. Anyone else gets `403`."""
    async with database.actor_transaction(staff.actor) as session:
        await service.withdraw_step(session, staff.actor, step_id, idempotency_key=key, clock=clock)
    return await _read(database, staff, step_id, clock)


routers: list[APIRouter] = [router]
