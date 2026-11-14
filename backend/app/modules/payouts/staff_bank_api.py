"""Staff routes for paying bank payouts from the trust account.

Seeing bank payouts, batches and settlements needs `money_movement.view`. Every step needs
`bank_payouts.change`. Approving a batch or a settlement moves money, so it needs someone other
than who prepared or proposed it, and the second step in the last few minutes. Every step needs
a reason; preparing, approving and declining a batch, and proposing and approving a settlement,
take an `Idempotency-Key`. The rules are in `bank_payouts.py` and migration 0039.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import bank_payouts
from app.modules.payouts.api import MoneyView
from app.modules.payouts.bank_payouts import BankPayoutRefused, BatchStatus
from app.modules.payouts.payouts import PayoutView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

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
# Approving moves money, and declining decides someone else's work: a recent second step.
Checker = Annotated[StaffSession, Depends(require_confirmed("bank_payouts.change"))]

CurrencyCode = Literal["UGX", "USD"]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class BankPayoutOut(Contract):
    """A bank payout as staff see it. Never the account number: that is only in the file."""

    id: PushId
    client_id: PushId
    destination_id: PushId
    amount: MoneyView
    status: Literal["queued", "sent", "paid", "failed"]
    business_date: date
    created_at: datetime


class BatchIn(Contract):
    currency: CurrencyCode
    payout_ids: Annotated[list[PushId], Field(min_length=1, max_length=bank_payouts.MAX_BATCH)]
    reason: Reason


class ReasonIn(Contract):
    reason: Reason


class BatchOut(Contract):
    id: PushId
    currency: CurrencyCode
    reason: str
    status: Literal["prepared", "approved", "declined", "withdrawn"]
    prepared_by: PushId
    prepared_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None
    payout_ids: list[PushId]
    total: MoneyView


class SettlementIn(Contract):
    """`paid` names the trust statement line that took the money; `failed` names a trust
    statement at least two days after the batch was approved."""

    outcome: Literal["paid", "failed"]
    statement_line_id: PushId | None = None
    statement_id: PushId | None = None
    reason: Reason


class SettlementOut(Contract):
    id: PushId
    payout_id: PushId
    batch_id: PushId
    outcome: Literal["paid", "failed"]
    statement_line_id: PushId | None
    statement_id: PushId | None
    reason: str
    status: Literal["proposed", "approved", "declined", "withdrawn"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None


class SentBankPayoutOut(Contract):
    """A bank payout that left in an approved batch and waits to be settled."""

    payout: BankPayoutOut
    batch_id: PushId
    sent_at: datetime
    settlement: SettlementOut | None


class SettledPayoutOut(Contract):
    id: PushId
    status: Literal["paid", "failed"]


def _money(value: Money) -> MoneyView:
    return MoneyView(minor=value.minor, currency=value.currency.value)


def _payout(payout: PayoutView) -> BankPayoutOut:
    status = payout.status.value
    assert status in ("queued", "sent", "paid", "failed"), "a bank payout on these routes"
    return BankPayoutOut(
        id=payout.id,
        client_id=payout.client_id,
        destination_id=payout.destination_id,
        amount=_money(payout.amount),
        status=status,  # type: ignore[arg-type]
        business_date=payout.business_date,
        created_at=payout.created_at,
    )


def _batch(view: bank_payouts.BatchView) -> BatchOut:
    return BatchOut(
        id=view.id,
        currency=view.currency.value,
        reason=view.reason,
        status=view.status.value,
        prepared_by=view.prepared_by,
        prepared_at=view.prepared_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
        payout_ids=list(view.payout_ids),
        total=_money(view.total),
    )


def _settlement(view: bank_payouts.SettlementView) -> SettlementOut:
    return SettlementOut(
        id=view.id,
        payout_id=view.payout_id,
        batch_id=view.batch_id,
        outcome=view.outcome,
        statement_line_id=view.statement_line_id,
        statement_id=view.statement_id,
        reason=view.reason,
        status=view.status.value,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_reason=view.decision_reason,
    )


def _refused(error: BankPayoutRefused) -> ApplicationError:
    return ApplicationError(
        error.status, fields=[FieldProblem(field_path=["body"], code=error.code)]
    )


@staff_router.get("/payouts/bank/waiting")
async def waiting(
    staff: Viewer, database: Db, currency: CurrencyCode = "UGX"
) -> list[BankPayoutOut]:
    """Queued bank payouts in no prepared or approved batch, oldest first, at most 200."""
    async with database.actor_transaction(staff.actor) as session:
        found = await bank_payouts.waiting_payouts(session, Currency(currency))
    return [_payout(each) for each in found]


@staff_router.get("/payouts/bank/sent")
async def sent(staff: Viewer, database: Db) -> list[SentBankPayoutOut]:
    """Bank payouts sent and waiting to be settled, oldest first, at most 100, each with its
    open settlement."""
    async with database.actor_transaction(staff.actor) as session:
        found = await bank_payouts.sent_payouts(session)
    return [
        SentBankPayoutOut(
            payout=_payout(each.payout),
            batch_id=each.batch_id,
            sent_at=each.sent_at,
            settlement=None if each.settlement is None else _settlement(each.settlement),
        )
        for each in found
    ]


@staff_router.get("/payout-bank-batches")
async def list_batches(
    staff: Viewer,
    database: Db,
    status: Annotated[list[Literal["prepared", "approved", "declined", "withdrawn"]], Query()] = [  # noqa: B006
        "prepared",
        "approved",
    ],
) -> list[BatchOut]:
    """Batches in these statuses, newest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await bank_payouts.batches(
            session, [BatchStatus(each) for each in status], limit=100
        )
    return [_batch(each) for each in found]


@staff_router.get("/payout-bank-batches/{batch_id}")
async def get_batch(batch_id: PushId, staff: Viewer, database: Db) -> BatchOut:
    async with database.actor_transaction(staff.actor) as session:
        view = await bank_payouts.get_batch(session, batch_id)
    return _batch(view)


@staff_router.post("/payout-bank-batches", status_code=201)
async def prepare(
    body: BatchIn, staff: Maker, database: Db, clock: Time, idempotency_key: IdempotencyKey
) -> BatchOut:
    """Put the chosen queued bank payouts into a batch for someone else to approve. 422
    `batch_payouts_invalid` for a repeated payout; 409 `payout_not_bank`, `payout_not_queued`
    or `payout_in_another_batch`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bank_payouts.prepare_batch(
                session,
                staff.actor,
                currency=Currency(body.currency),
                payout_ids=body.payout_ids,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _batch(view)


@staff_router.post("/payout-bank-batches/{batch_id}/approve")
async def approve(
    batch_id: PushId,
    body: ReasonIn,
    staff: Checker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> BatchOut:
    """The second signature. Every payout in the batch is sent, and the file can be downloaded.
    403 `prepared_by_you`; 409 `batch_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bank_payouts.approve_batch(
                session,
                staff.actor,
                batch_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _batch(view)


@staff_router.post("/payout-bank-batches/{batch_id}/decline")
async def decline(
    batch_id: PushId,
    body: ReasonIn,
    staff: Checker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> BatchOut:
    """Someone other than the preparer refuses the batch. Its payouts stay queued. 403
    `prepared_by_you`; 409 `batch_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bank_payouts.decline_batch(
                session,
                staff.actor,
                batch_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _batch(view)


@staff_router.post("/payout-bank-batches/{batch_id}/withdraw")
async def withdraw(
    batch_id: PushId, body: ReasonIn, staff: Maker, database: Db, clock: Time
) -> BatchOut:
    """The preparer takes their batch back. 403 `not_prepared_by_you`; 409
    `batch_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bank_payouts.withdraw_batch(
                session, staff.actor, batch_id, reason=body.reason, clock=clock
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _batch(view)


@staff_router.get(
    "/payout-bank-batches/{batch_id}/file",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def transfer_file(batch_id: PushId, staff: Maker, database: Db, clock: Time) -> Response:
    """The approved batch as a CSV file to upload in the bank's portal, with whole account
    numbers. Writes a `payouts.bank_batch_file_downloaded` access event. 409
    `batch_not_approved`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            content = await bank_payouts.transfer_file(session, staff.actor, batch_id, clock)
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="finvest-bank-payouts-{batch_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


@staff_router.post("/payouts/{payout_id}/bank-settlements", status_code=201)
async def propose_settlement(
    payout_id: PushId,
    body: SettlementIn,
    staff: Maker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> SettlementOut:
    """The first person's word on how a sent bank payout ended. 422 `statement_line_required` or
    `statement_required`; 409 `payout_not_bank`, `payout_not_sent`,
    `settlement_already_proposed`, `statement_line_in_use`, `statement_line_not_this_payout`
    or `statement_not_enough`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bank_payouts.propose_settlement(
                session,
                staff.actor,
                payout_id,
                outcome=body.outcome,
                statement_line_id=body.statement_line_id,
                statement_id=body.statement_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _settlement(view)


@staff_router.post("/payout-bank-settlements/{settlement_id}/approve")
async def approve_settlement(
    settlement_id: PushId,
    body: ReasonIn,
    staff: Checker,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> SettledPayoutOut:
    """A different person approves, and the payout is paid or failed, with its money settled
    from the trust account or parked. 403 `proposed_by_you`; 409 `settlement_already_decided`
    or `payout_not_sent`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            moved = await bank_payouts.approve_settlement(
                session,
                staff.actor,
                settlement_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    status = moved.status.value
    assert status in ("paid", "failed"), "an approved settlement finishes its payout"
    return SettledPayoutOut(id=moved.id, status=status)  # type: ignore[arg-type]


@staff_router.post("/payout-bank-settlements/{settlement_id}/decline")
async def decline_settlement(
    settlement_id: PushId, body: ReasonIn, staff: Checker, database: Db, clock: Time
) -> SettlementOut:
    """Someone other than the proposer refuses it. The payout stays sent. 403 `proposed_by_you`;
    409 `settlement_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bank_payouts.decline_settlement(
                session, staff.actor, settlement_id, reason=body.reason, clock=clock
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _settlement(view)


@staff_router.post("/payout-bank-settlements/{settlement_id}/withdraw")
async def withdraw_settlement(
    settlement_id: PushId, body: ReasonIn, staff: Maker, database: Db, clock: Time
) -> SettlementOut:
    """The proposer takes it back. 403 `not_proposed_by_you`; 409 `settlement_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await bank_payouts.withdraw_settlement(
                session, staff.actor, settlement_id, reason=body.reason, clock=clock
            )
    except BankPayoutRefused as error:
        raise _refused(error) from None
    return _settlement(view)
