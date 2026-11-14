"""Staff routes for a paid payout that came back.

Seeing returns needs `money_movement.view`. Recording and deciding one needs
`bank_payouts.change`, and approving or declining also needs the second step in the last few
minutes. Every step needs a reason; proposing and approving take an `Idempotency-Key`. The rules
are in `returns.py` and migration 0044.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import returns
from app.modules.payouts.api import MoneyView
from app.modules.payouts.returns import ReturnRefused
from app.modules.reconciliation.statements import StatementLineView
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
Checker = Annotated[StaffSession, Depends(require_confirmed("bank_payouts.change"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class ReturnIn(Contract):
    """The statement line that shows the money coming back into the pot it was paid from."""

    statement_line_id: PushId
    reason: Reason


class ReasonIn(Contract):
    reason: Reason


class ReturnedPayoutOut(Contract):
    """The payout a return names and where it went. The account is masked."""

    kind: Literal["bank", "mobile_money"]
    amount: MoneyView
    bank_name: str | None
    network: str | None
    masked_account: str
    holder_name: str


class ReturnablePayoutOut(Contract):
    """A paid payout that could have come back. The account is masked."""

    id: PushId
    client_id: PushId
    kind: Literal["bank", "mobile_money"]
    amount: MoneyView
    paid_at: datetime
    bank_name: str | None
    network: str | None
    masked_account: str
    holder_name: str


class ReturnLineOut(Contract):
    """A statement line coming in that could be the payout coming back."""

    id: PushId
    value_date: date
    amount: MoneyView
    reference: str
    narrative: str


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
    payout: ReturnedPayoutOut
    line: ReturnLineOut | None = Field(
        description="The line that brought the money back. Null only if not on the payout's pot."
    )


def _money(amount: Money) -> MoneyView:
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def _line(line: StatementLineView) -> ReturnLineOut:
    return ReturnLineOut(
        id=line.id,
        value_date=line.value_date,
        amount=_money(line.amount),
        reference=line.reference,
        narrative=line.counterparty_hint,
    )


async def _out(session: AsyncSession, view: returns.ReturnView) -> ReturnOut:
    found = await returns.details(session, view)
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
        payout=ReturnedPayoutOut(
            kind=found.payout.kind.value,
            amount=_money(found.payout.amount),
            bank_name=found.bank_name,
            network=found.network,
            masked_account=found.masked_account,
            holder_name=found.holder_name,
        ),
        line=None if found.line is None else _line(found.line),
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
        return [await _out(session, each) for each in found]


@staff_router.get("/payout-returns/returnable")
async def returnable(
    staff: Viewer,
    database: Db,
    clock: Time,
    amount_minor: Annotated[int | None, Query(gt=0)] = None,
    currency: Literal["UGX", "USD"] = "UGX",
) -> list[ReturnablePayoutOut]:
    """Paid payouts from the last 120 days with no open or approved return, newest paid first,
    at most 100. With `amount_minor`, only payouts of that amount in `currency`."""
    amount = None if amount_minor is None else Money(amount_minor, Currency(currency))
    async with database.actor_transaction(staff.actor) as session:
        found = await returns.returnable(session, amount=amount, now=clock.now(), limit=100)
    return [
        ReturnablePayoutOut(
            id=each.payout.id,
            client_id=each.payout.client_id,
            kind=each.payout.kind.value,
            amount=MoneyView(
                minor=each.payout.amount.minor, currency=each.payout.amount.currency.value
            ),
            paid_at=each.paid_at,
            bank_name=each.bank_name,
            network=each.network,
            masked_account=each.masked_account,
            holder_name=each.holder_name,
        )
        for each in found
    ]


@staff_router.get("/payouts/{payout_id}/return-lines")
async def return_lines(payout_id: PushId, staff: Viewer, database: Db) -> list[ReturnLineOut]:
    """Statement lines that could be this paid payout coming back, named by no open or approved
    return. 409 `payout_not_paid`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            found = await returns.return_lines(session, payout_id)
    except ReturnRefused as error:
        raise _refused(error) from None
    return [_line(line) for line in found]


@staff_router.get("/payout-returns/{return_id}")
async def get_return(return_id: PushId, staff: Viewer, database: Db) -> ReturnOut:
    async with database.actor_transaction(staff.actor) as session:
        view = await returns.get_return(session, return_id)
        return await _out(session, view)


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
            out = await _out(session, view)
    except ReturnRefused as error:
        raise _refused(error) from None
    return out


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
            out = await _out(session, view)
    except ReturnRefused as error:
        raise _refused(error) from None
    return out


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
            out = await _out(session, view)
    except ReturnRefused as error:
        raise _refused(error) from None
    return out


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
            out = await _out(session, view)
    except ReturnRefused as error:
        raise _refused(error) from None
    return out
