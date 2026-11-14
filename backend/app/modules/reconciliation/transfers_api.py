"""Staff routes for transfers between Finvest's own pots, such as a sweep from an ioTec
wallet to the trust bank account.

Seeing transfers needs `reconciliation.view`; recording and deciding one needs
`reconciliation.change`. One member of staff records a transfer that has already been made. A
different one approves it, which posts it to the ledger, or declines it. Approving and declining
need a passkey or app code from the last few minutes. Recording, approving and declining take an
`Idempotency-Key`: a retried request returns what the first one did, and a key reused for a
different request is refused with `idempotency_key_reused`. The rules are in `transfers.py` and
migration 0042.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.reconciliation import transfers
from app.modules.reconciliation.domain.statements import StatementSource
from app.modules.reconciliation.domain.transfers import TransferError
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


staff_router = APIRouter(
    prefix="/staff/reconciliation/pot-transfers",
    tags=["staff reconciliation"],
    responses=_REFUSALS,
)
routers = [staff_router]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
Staff = Annotated[StaffSession, Depends(require_permission("reconciliation.view"))]
Changer = Annotated[StaffSession, Depends(require_permission("reconciliation.change"))]
# The second signature moves money, so it needs a passkey or app code from the last few minutes.
Approver = Annotated[StaffSession, Depends(require_confirmed("reconciliation.change"))]

Source = Literal["iotec", "bank"]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
# A day as JSON sends it, "2026-11-13". Strict contracts would refuse the string.
Day = Annotated[date, Field(strict=False)]

# Refusals that mean the request itself is wrong, as opposed to the state of the transfer.
_INVALID = frozenset(
    {
        "pot_unknown",
        "transfer_same_pot",
        "currency_not_supported",
        "amount_not_positive",
        "transfer_received_more_than_sent",
        "transfer_fee_not_allowed",
        "transfer_in_the_future",
    }
)


class MoneyIn(Contract):
    minor: Annotated[int, Field(gt=0)]
    currency: Literal["UGX", "USD"]


class MoneyOut(Contract):
    minor: int
    currency: Literal["UGX", "USD"]


class PotIn(Contract):
    source: Source
    account_reference: Annotated[str, Field(min_length=1, max_length=40)]


class PotOut(Contract):
    source: Source
    account_reference: str
    name: str


class TransferIn(Contract):
    """A transfer that has already been made, as the provider or bank shows it."""

    from_pot: PotIn
    to_pot: PotIn
    # What left the sending pot, and what arrived. The difference is the provider's fee.
    sent: MoneyIn
    received: MoneyIn
    value_date: Day
    provider_reference: Annotated[str, Field(min_length=1, max_length=100)]
    reason: Reason


class ReasonIn(Contract):
    reason: Reason


class TransferOut(Contract):
    id: PushId
    from_pot: PotOut
    to_pot: PotOut
    sent: MoneyOut
    received: MoneyOut
    fee: MoneyOut
    value_date: date
    provider_reference: str
    status: Literal["proposed", "approved", "declined"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    # The entry the approval posted, which reconciliation pairs with both statements.
    ledger_entry_id: PushId | None


def _money(value: Money) -> MoneyOut:
    return MoneyOut(minor=value.minor, currency=value.currency.value)


def _pot_out(view: transfers.TransferView, side: Literal["from", "to"]) -> PotOut:
    pot = view.terms.from_pot if side == "from" else view.terms.to_pot
    source: Source = "iotec" if pot.source is StatementSource.IOTEC else "bank"
    return PotOut(source=source, account_reference=pot.account_reference, name=pot.name)


def _out(view: transfers.TransferView) -> TransferOut:
    terms = view.terms
    return TransferOut(
        id=view.id,
        from_pot=_pot_out(view, "from"),
        to_pot=_pot_out(view, "to"),
        sent=_money(terms.sent),
        received=_money(terms.received),
        fee=_money(terms.fee),
        value_date=terms.value_date,
        provider_reference=view.provider_reference,
        status=view.status.value,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        ledger_entry_id=view.ledger_entry_id,
    )


def _refused(error: TransferError) -> ApplicationError:
    if error.code == "transfer_needs_second_person":
        return ApplicationError("forbidden", fields=[_problem(error.code)])
    status: Literal["conflict", "validation_failed"] = (
        "validation_failed" if error.code in _INVALID else "conflict"
    )
    return ApplicationError(status, fields=[_problem(error.code)])


def _problem(code: str) -> FieldProblem:
    return FieldProblem(field_path=["body"], code=code)


def _money_in(value: MoneyIn) -> Money:
    return Money(value.minor, Currency(value.currency))


@staff_router.get("")
async def waiting(
    staff: Staff, database: Db, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[TransferOut]:
    """Transfers waiting for a second person, oldest first."""
    async with database.actor_transaction(staff.actor) as session:
        found = await transfers.waiting(session, limit=limit)
    return [_out(each) for each in found]


@staff_router.get("/{transfer_id}")
async def get_transfer(transfer_id: PushId, staff: Staff, database: Db) -> TransferOut:
    """One transfer, in any status."""
    async with database.actor_transaction(staff.actor) as session:
        view = await transfers.get_transfer(session, transfer_id)
    return _out(view)


@staff_router.post("", status_code=201)
async def record(
    body: TransferIn,
    staff: Changer,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> TransferOut:
    """Record a transfer that has been made. Nothing is posted until someone else approves it.

    422 `pot_unknown`, `transfer_same_pot`, `currency_not_supported`,
    `transfer_received_more_than_sent`, `transfer_fee_not_allowed` (a transfer out of the bank
    cannot carry a fee yet) or `transfer_in_the_future`; 409 `transfer_in_a_closed_day` or
    `transfer_already_recorded` for a provider reference already recorded from that pot.
    """
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await transfers.propose(
                session,
                staff.actor,
                from_pot=(StatementSource(body.from_pot.source), body.from_pot.account_reference),
                to_pot=(StatementSource(body.to_pot.source), body.to_pot.account_reference),
                sent=_money_in(body.sent),
                received=_money_in(body.received),
                value_date=body.value_date,
                provider_reference=body.provider_reference,
                reason=body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except TransferError as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/{transfer_id}/approve")
async def approve(
    transfer_id: PushId,
    staff: Approver,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> TransferOut:
    """The second signature, which posts the transfer. 403 `transfer_needs_second_person` for
    whoever recorded it; 409 `transfer_already_decided` or `transfer_more_than_the_pot_holds`
    when the books say the sending pot never held that much."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await transfers.approve(
                session, staff.actor, transfer_id, idempotency_key=idempotency_key, clock=clock
            )
    except TransferError as error:
        raise _refused(error) from None
    return _out(view)


@staff_router.post("/{transfer_id}/decline")
async def decline(
    transfer_id: PushId,
    body: ReasonIn,
    staff: Approver,
    database: Db,
    clock: Time,
    idempotency_key: IdempotencyKey,
) -> TransferOut:
    """A second person refuses it, with a reason. Nothing is posted. 403
    `transfer_needs_second_person`; 409 `transfer_already_decided`."""
    try:
        async with database.actor_transaction(staff.actor) as session:
            view = await transfers.decline(
                session,
                staff.actor,
                transfer_id,
                body.reason,
                idempotency_key=idempotency_key,
                clock=clock,
            )
    except TransferError as error:
        raise _refused(error) from None
    return _out(view)
