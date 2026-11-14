"""Staff routes for settling many bank payouts of one batch from a single bulk debit.

Seeing them needs `money_movement.view`. Proposing and withdrawing need `bank_payouts.change`;
approving and declining also need the second step in the last few minutes, and someone other
than the proposer. Every step needs a reason; proposing and approving take an
`Idempotency-Key`. The rules are in `bulk_settlements.py` and migration 0280.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import bank_payouts, bulk_settlements
from app.modules.payouts.api import MoneyView
from app.modules.payouts.bank_payouts import BankPayoutRefused
from app.modules.payouts.staff_bank_api import BankPayoutOut, BatchOut, _batch, _money, _payout
from app.modules.reconciliation.statements import StatementLineView
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


class BulkDebitLineOut(Contract):
    """A trust statement line going out, as the first person picks the bulk debit."""

    id: PushId
    value_date: date
    amount: MoneyView
    reference: str
    narrative: str


class BulkDebitOut(Contract):
    """An approved batch's payouts still to settle, and the lines that could be their debit."""

    batch: BatchOut
    payouts: list[BankPayoutOut]
    lines: list[BulkDebitLineOut]


class BulkIn(Contract):
    statement_line_id: PushId
    payout_ids: Annotated[list[PushId], Field(min_length=1, max_length=bank_payouts.MAX_BATCH)]
    reason: Reason


class ReasonIn(Contract):
    reason: Reason


class BulkOut(Contract):
    id: PushId
    batch_id: PushId
    statement_line_id: PushId
    reason: str
    status: Literal["proposed", "approved", "declined", "withdrawn"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None
    payout_ids: list[PushId]
    total: MoneyView


def _line(line: StatementLineView) -> BulkDebitLineOut:
    return BulkDebitLineOut(
        id=line.id,
        value_date=line.value_date,
        amount=_money(line.amount),
        reference=line.reference,
        narrative=line.counterparty_hint,
    )


def _out(view: bulk_settlements.BulkView) -> BulkOut:
    return BulkOut(
        id=view.id,
        batch_id=view.batch_id,
        statement_line_id=view.statement_line_id,
        reason=view.reason,
        status=view.status.value,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
        payout_ids=list(view.payout_ids),
        total=_money(view.total),
    )


def _refused(error: BankPayoutRefused) -> ApplicationError:
    return ApplicationError(
        error.status, fields=[FieldProblem(field_path=["body"], code=error.code)]
    )


@staff_router.get("/payout-bank-batches/{batch_id}/bulk-debit")
async def bulk_debit(batch_id: PushId, staff: Viewer, database: Db) -> BulkDebitOut:
    """The batch's sent payouts that no open settlement names, and the trust statement lines
    going out since it was approved that no settlement names and that are no more than those
    payouts together. 409 `batch_not_approved`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            debit = await bulk_settlements.bulk_debit(session, batch_id)
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return BulkDebitOut(
        batch=_batch(debit.batch),
        payouts=[_payout(each) for each in debit.payouts],
        lines=[_line(each) for each in debit.lines],
    )


@staff_router.get("/payout-bank-bulk-settlements")
async def waiting(staff: Viewer, database: Db) -> list[BulkOut]:
    """Bulk settlements waiting for a second person, oldest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await bulk_settlements.waiting(session, limit=100)
    return [_out(each) for each in found]


@staff_router.get("/payout-bank-bulk-settlements/{bulk_id}")
async def get_bulk(bulk_id: PushId, staff: Viewer, database: Db) -> BulkOut:
    async with database.actor_transaction(staff.actor) as session:
        view = await bulk_settlements.get_bulk(session, bulk_id)
    return _out(view)


@staff_router.post("/payout-bank-batches/{batch_id}/bulk-settlements", status_code=201)
async def propose(
    batch_id: PushId,
    body: BulkIn,
    staff: Maker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> BulkOut:
    """Say one bulk debit paid these payouts of the batch. 422 `bulk_payouts_invalid`; 409
    `batch_not_approved`, `payout_not_in_batch`, `payout_not_to_settle`,
    `statement_line_in_use`, `statement_line_not_this_batch` or `amounts_do_not_add_up`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bulk_settlements.propose(
                session,
                staff.actor,
                batch_id,
                statement_line_id=body.statement_line_id,
                payout_ids=body.payout_ids,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-bank-bulk-settlements/{bulk_id}/approve")
async def approve(
    bulk_id: PushId,
    body: ReasonIn,
    staff: Checker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> BulkOut:
    """The second signature. Every payout in it becomes paid and each client is told. 403
    `proposed_by_you`; 409 `settlement_already_decided` or `payout_not_sent`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bulk_settlements.approve(
                session,
                staff.actor,
                bulk_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-bank-bulk-settlements/{bulk_id}/decline")
async def decline(
    bulk_id: PushId, body: ReasonIn, staff: Checker, database: Db, clock: Time
) -> BulkOut:
    """Someone other than the proposer refuses it. Its payouts stay sent. 403
    `proposed_by_you`; 409 `settlement_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bulk_settlements.decline(
                session, staff.actor, bulk_id, reason=body.reason, clock=clock
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/payout-bank-bulk-settlements/{bulk_id}/withdraw")
async def withdraw(
    bulk_id: PushId, body: ReasonIn, staff: Maker, database: Db, clock: Time
) -> BulkOut:
    """The proposer takes it back. 403 `not_proposed_by_you`; 409 `settlement_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bulk_settlements.withdraw(
                session, staff.actor, bulk_id, reason=body.reason, clock=clock
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _out(view)
