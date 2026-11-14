"""Sending an investment to another client, over HTTP.

Client routes:

* `GET /client/transfer-recipients?to=` finds another approved client by Finvest account number
  or mobile number, and shows them as first given name and surname initial. Throttled to 20
  look-ups in 15 minutes per client.
* `POST /client/investments/{id}/send` asks to send part or all of a running investment, with
  an `Idempotency-Key`. A large one is held for staff.
* `GET /client/transfers` and `GET /client/transfers/{id}` list what the client sent, and what
  they received once it is done.
* `POST /client/transfers/{id}/cancel` takes back a transfer still held for staff.

Staff routes, in the review queue (`review_queue.view`):

* `GET /staff/investment-transfers/held` and `GET /staff/investment-transfers`.
* Letting a held transfer through takes two people with `investment_transfers.change`: one
  proposes (`/release`), a different one approves (`/approve`) after a second step in the last
  few minutes, and the proposer may withdraw (`/withdraw`). Declining (`/decline`) is one
  person's step, also behind the second step. Every step but withdrawing needs a reason and an
  `Idempotency-Key`.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.throttling import reserve_attempts, throttle_key
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.investments import transfers
from app.modules.investments.domain.transfers import MAX_MESSAGE_LENGTH, HeldFor, TransferStatus
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope
from app.shared.money import Currency, Money

MAX_AMOUNT_MINOR = 10**15
LOOKUPS_PER_WINDOW = 20

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
    429: {"model": ErrorEnvelope},
}

client_router = APIRouter(prefix="/client", tags=["client transfers"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff", tags=["staff transfers"], responses=_REFUSALS)
routers = (client_router, staff_router)


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
Viewer = Annotated[StaffSession, Depends(require_permission("review_queue.view"))]
Proposer = Annotated[StaffSession, Depends(require_permission("investment_transfers.change"))]
# Approving and declining need a passkey or app code from the last few minutes.
Reviewer = Annotated[StaffSession, Depends(require_confirmed("investment_transfers.change"))]

AmountMinor = Annotated[int, Field(ge=1, le=MAX_AMOUNT_MINOR)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class RecipientOut(Contract):
    """Who the client is about to send to: never their full name."""

    account_number: Annotated[str, Field(pattern=r"^\d{10}$")]
    name: str


class SendRequest(Contract):
    # The recipient's ten-digit Finvest account number, as `transfer-recipients` returned it.
    to_account_number: Annotated[str, Field(min_length=10, max_length=16)]
    amount_minor: AmountMinor
    currency: Currency
    message: Annotated[str, Field(max_length=MAX_MESSAGE_LENGTH)] | None = None
    # Send everything: the whole value when the request is saved, and the interest added before
    # it moves. `amount_minor` is then the value the client was shown.
    whole: bool = False


class ClientTransferOut(Contract):
    id: PushId
    direction: str
    other_name: str
    status: TransferStatus
    amount_minor: int
    currency: Currency
    # Everything was sent, including interest earned after the request.
    whole: bool
    message: str | None
    requested_at: datetime
    finished_at: datetime | None
    # The sender's: the investment sent from. The recipient's: the one they were given.
    investment_id: PushId | None
    # The sender's new investment with what they kept, if they kept any.
    kept_investment_id: PushId | None
    # Why it could not be made, for the sender.
    failure_code: str | None


class StaffTransferOut(Contract):
    id: PushId
    investment_id: PushId
    sender_client_id: PushId
    recipient_client_id: PushId
    sender_name: str
    recipient_name: str
    amount_minor: int
    currency: Currency
    whole: bool
    message: str | None
    status: TransferStatus
    held: bool
    # Why it was held: `size` or `screening`. Never shown to either client.
    held_for: HeldFor | None
    requested_at: datetime
    release_proposed_by: PushId | None
    release_proposed_at: datetime | None
    release_reason: str | None
    released_by: PushId | None
    released_at: datetime | None
    declined_by: PushId | None
    declined_at: datetime | None
    decision_reason: str | None
    cancelled_at: datetime | None
    finished_at: datetime | None
    sent_minor: int | None
    received_investment_id: PushId | None
    kept_investment_id: PushId | None
    failure_code: str | None


class ReviewIn(Contract):
    reason: Reason


def _client_out(view: transfers.ClientTransfer) -> ClientTransferOut:
    return ClientTransferOut(
        id=view.id,
        direction=view.direction,
        other_name=view.other_name,
        status=view.status,
        amount_minor=view.amount.minor,
        currency=view.amount.currency,
        whole=view.whole,
        message=view.message,
        requested_at=view.requested_at,
        finished_at=view.finished_at,
        investment_id=view.investment_id,
        kept_investment_id=view.kept_investment_id,
        failure_code=view.failure_code,
    )


def _staff_out(transfer: transfers.Transfer) -> StaffTransferOut:
    return StaffTransferOut(
        id=transfer.id,
        investment_id=transfer.investment_id,
        sender_client_id=transfer.sender_client_id,
        recipient_client_id=transfer.recipient_client_id,
        sender_name=transfer.sender_name,
        recipient_name=transfer.recipient_name,
        amount_minor=transfer.amount.minor,
        currency=transfer.amount.currency,
        whole=transfer.whole,
        message=transfer.message,
        status=transfer.status,
        held=transfer.held,
        held_for=transfer.held_for,
        requested_at=transfer.requested_at,
        release_proposed_by=transfer.release_proposed_by,
        release_proposed_at=transfer.release_proposed_at,
        release_reason=transfer.release_reason,
        released_by=transfer.released_by,
        released_at=transfer.released_at,
        declined_by=transfer.declined_by,
        declined_at=transfer.declined_at,
        decision_reason=transfer.decision_reason,
        cancelled_at=transfer.cancelled_at,
        finished_at=transfer.finished_at,
        sent_minor=None if transfer.sent is None else transfer.sent.minor,
        received_investment_id=transfer.received_investment_id,
        kept_investment_id=transfer.kept_investment_id,
        failure_code=transfer.failure_code,
    )


# Client -----------------------------------------------------------------------------------------


@client_router.get("/transfer-recipients")
async def find_recipient(
    client: CurrentClient,
    database: Db,
    clock: Time,
    to: Annotated[str, Query(min_length=6, max_length=24)],
) -> RecipientOut:
    """Another approved client, by Finvest account number or mobile number. 404 when there is
    nobody approved with it, 422 `to_yourself`, 429 after 20 look-ups in 15 minutes."""
    await reserve_attempts(
        database,
        clock,
        [(throttle_key("transfer_recipient", client.client_id), LOOKUPS_PER_WINDOW)],
    )
    async with database.actor_transaction(client.actor) as session:
        found = await transfers.find_recipient(session, client.actor, to)
    return RecipientOut(account_number=found.account_number, name=found.name)


@client_router.post("/investments/{investment_id}/send", status_code=201)
async def send_investment(
    client: CurrentClient,
    database: Db,
    clock: Time,
    key: IdempotencyKey,
    investment_id: PushId,
    body: SendRequest,
) -> ClientTransferOut:
    """Send part or all of a running investment to another client. Nothing is cashed out.

    422 on `amount_minor` with `below_product_minimum`, `more_than_investment_value`,
    `remainder_below_product_minimum`, `currency_not_the_investments` or `matures_today`; on
    `to` with `no_such_client` or `to_yourself`. 409 when the investment is not running, an
    early withdrawal was asked for, or a transfer of it is already open. 403 with
    `restricted_after_code_sign_in` for a day after signing in with a code or a password reset.
    """
    async with database.actor_transaction(client.actor) as session:
        transfer = await transfers.send_investment(
            session,
            client.actor,
            investment_id,
            to_account_number=body.to_account_number,
            amount=Money(body.amount_minor, body.currency),
            message=body.message,
            restricted_until=client.restricted_until,
            idempotency_key=key,
            clock=clock,
            whole=body.whole,
        )
        view = await transfers.client_transfer(session, client.actor, transfer.id)
    return _client_out(view)


@client_router.get("/transfers")
async def client_transfers(client: CurrentClient, database: Db) -> list[ClientTransferOut]:
    async with database.actor_transaction(client.actor) as session:
        found = await transfers.client_transfers(session, client.actor)
    return [_client_out(view) for view in found]


@client_router.get("/transfers/{transfer_id}")
async def client_transfer(
    client: CurrentClient, database: Db, transfer_id: PushId
) -> ClientTransferOut:
    async with database.actor_transaction(client.actor) as session:
        view = await transfers.client_transfer(session, client.actor, transfer_id)
    return _client_out(view)


@client_router.post("/transfers/{transfer_id}/cancel")
async def cancel_transfer(
    client: CurrentClient, database: Db, clock: Time, transfer_id: PushId
) -> ClientTransferOut:
    """Take back a transfer that is still held for staff. 409 once it has gone through."""
    async with database.actor_transaction(client.actor) as session:
        await transfers.cancel_transfer(session, client.actor, transfer_id, clock)
        view = await transfers.client_transfer(session, client.actor, transfer_id)
    return _client_out(view)


# Staff ------------------------------------------------------------------------------------------


@staff_router.get("/investment-transfers/held")
async def held_transfers(staff: Viewer, database: Db) -> list[StaffTransferOut]:
    """Transfers waiting for staff, oldest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await transfers.held_transfers(session, staff.actor)
    return [_staff_out(transfer) for transfer in found]


@staff_router.get("/investment-transfers")
async def list_transfers(
    staff: Viewer,
    database: Db,
    status: TransferStatus | None = None,
    client_id: PushId | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[StaffTransferOut]:
    async with database.actor_transaction(staff.actor) as session:
        found = await transfers.list_transfers(
            session, staff.actor, status=status, client_id=client_id, limit=limit
        )
    return [_staff_out(transfer) for transfer in found]


async def _command(
    database: Database,
    staff: StaffSession,
    clock: Clock,
    *,
    operation: str,
    key: str,
    body: dict[str, object],
    command: Callable[[AsyncSession], Awaitable[transfers.Transfer]],
) -> StaffTransferOut:
    """Run one staff command once per key, and answer with the transfer as it is now."""
    async with database.actor_transaction(staff.actor) as session:

        async def run() -> Receipt:
            transfer = await command(session)
            return Receipt(200, {"transfer_id": transfer.id})

        outcome = await run_once(
            session,
            actor=staff.actor,
            operation=operation,
            key=key,
            request=body,
            now=clock.now(),
            command=run,
        )
        transfer_id = outcome.receipt.response.get("transfer_id")
        assert isinstance(transfer_id, str)
        return _staff_out(await transfers.get_transfer(session, staff.actor, transfer_id))


@staff_router.post("/investment-transfers/{transfer_id}/release")
async def propose_release(
    transfer_id: PushId,
    body: ReviewIn,
    staff: Proposer,
    database: Db,
    clock: Time,
    key: IdempotencyKey,
) -> StaffTransferOut:
    """The first person proposes letting a held transfer through. 409 if it is not held."""
    return await _command(
        database,
        staff,
        clock,
        operation="investments.propose_transfer_release",
        key=key,
        body={"transfer_id": transfer_id, "reason": body.reason},
        command=lambda session: transfers.propose_release(
            session, staff.actor, transfer_id, body.reason, clock
        ),
    )


@staff_router.post("/investment-transfers/{transfer_id}/approve")
async def approve_release(
    transfer_id: PushId,
    body: ReviewIn,
    staff: Reviewer,
    database: Db,
    clock: Time,
    key: IdempotencyKey,
) -> StaffTransferOut:
    """A different person approves, and the worker moves the money. 403 for the proposer."""
    return await _command(
        database,
        staff,
        clock,
        operation="investments.approve_transfer_release",
        key=key,
        body={"transfer_id": transfer_id, "reason": body.reason},
        command=lambda session: transfers.approve_release(
            session, staff.actor, transfer_id, body.reason, clock
        ),
    )


@staff_router.post("/investment-transfers/{transfer_id}/withdraw")
async def withdraw_release(
    transfer_id: PushId, staff: Proposer, database: Db, clock: Time
) -> StaffTransferOut:
    """The proposer takes their proposal back. 403 for anyone else."""
    async with database.actor_transaction(staff.actor) as session:
        transfer = await transfers.withdraw_release(session, staff.actor, transfer_id, clock)
    return _staff_out(transfer)


@staff_router.post("/investment-transfers/{transfer_id}/decline")
async def decline_transfer(
    transfer_id: PushId,
    body: ReviewIn,
    staff: Reviewer,
    database: Db,
    clock: Time,
    key: IdempotencyKey,
) -> StaffTransferOut:
    """Decline a held transfer with a reason. Nothing had moved. The sender is told."""
    return await _command(
        database,
        staff,
        clock,
        operation="investments.decline_transfer",
        key=key,
        body={"transfer_id": transfer_id, "reason": body.reason},
        command=lambda session: transfers.decline_transfer(
            session, staff.actor, transfer_id, body.reason, clock
        ),
    )
