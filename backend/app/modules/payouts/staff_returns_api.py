"""Staff routes for a paid payout that came back.

Seeing returns needs `money_movement.view`. Recording and deciding one needs
`bank_payouts.change`, and approving or declining also needs the second step in the last few
minutes. Every step needs a reason; proposing and approving take an `Idempotency-Key`. The rules
are in `returns.py` and migration 0044.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import returns
from app.modules.payouts.returns import ReturnRefused
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

staff_router = APIRouter(prefix="/staff", tags=["staff payouts"], responses=_REFUSALS)
routers = [staff_router]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
Viewer = Annotated[StaffSession, Depends(require_permission("money_movement.view"))]
Maker = Annotated[StaffSession, Depends(require_permission("bank_payouts.change"))]
Checker = Annotated[StaffSession, Depends(require_confirmed("bank_payouts.change"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class ReturnIn(Contract):
    """The statement line that shows the money coming back into the pot it was paid from."""

    statement_line_id: PushId
    reason: Reason


class ReasonIn(Contract):
    reason: Reason


class ReturnOut(Contract):
    id: PushId
    payout_id: PushId
    client_id: PushId
    statement_line_id: PushId
    reason: str
    status: Literal["proposed", "approved", "declined", "withdrawn"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None
    # The `payout.returned` entry the approval posted.
    ledger_entry_id: PushId | None


def _out(view: returns.ReturnView) -> ReturnOut:
    return ReturnOut(
        id=view.id,
        payout_id=view.payout_id,
        client_id=view.client_id,
        statement_line_id=view.statement_line_id,
        reason=view.reason,
        status=view.status.value,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
        ledger_entry_id=view.ledger_entry_id,
    )


def _refused(error: ReturnRefused) -> ApplicationError:
    return ApplicationError(
        error.status, fields=[FieldProblem(field_path=["body"], code=error.code)]
    )


@staff_router.get("/payout-returns")
async def waiting(staff: Viewer, database: Db) -> list[ReturnOut]:
    """Returns waiting for a second person, oldest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await returns.waiting(session, limit=100)
    return [_out(each) for each in found]


@staff_router.get("/payout-returns/{return_id}")
async def get_return(return_id: PushId, staff: Viewer, database: Db) -> ReturnOut:
    async with database.actor_transaction(staff.actor) as session:
        view = await returns.get_return(session, return_id)
    return _out(view)


@staff_router.post("/payouts/{payout_id}/returns", status_code=201)
async def propose(
    payout_id: PushId,
    body: ReturnIn,
    staff: Maker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ReturnOut:
    """Say a paid payout came back. 409 `payout_not_paid`, `return_already_recorded`,
    `statement_line_in_use` or `statement_line_not_this_payout`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await returns.propose(
                session,
                staff.actor,
                payout_id,
                statement_line_id=body.statement_line_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except ReturnRefused as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-returns/{return_id}/approve")
async def approve(
    return_id: PushId,
    body: ReasonIn,
    staff: Checker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> ReturnOut:
    """The second signature. The money is owed to the client again and they are told. 403
    `proposed_by_you`; 409 `return_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await returns.approve(
                session,
                staff.actor,
                return_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except ReturnRefused as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-returns/{return_id}/decline")
async def decline(
    return_id: PushId, body: ReasonIn, staff: Checker, database: Db, clock: Time
) -> ReturnOut:
    """Someone other than the proposer refuses it. 403 `proposed_by_you`; 409
    `return_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await returns.decline(
                session, staff.actor, return_id, reason=body.reason, clock=clock
            )
    except ReturnRefused as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-returns/{return_id}/withdraw")
async def withdraw(
    return_id: PushId, body: ReasonIn, staff: Maker, database: Db, clock: Time
) -> ReturnOut:
    """The proposer takes it back. 403 `not_proposed_by_you`; 409 `return_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await returns.withdraw(
                session, staff.actor, return_id, reason=body.reason, clock=clock
            )
    except ReturnRefused as error:
        raise _refused(error) from None
    return _out(view)
