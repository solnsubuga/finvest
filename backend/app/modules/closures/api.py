"""Routes to close a client's account: the client's own, and the staff queue.

A client sees what still stops their account closing, asks to close it once nothing does, and
may cancel while staff have not decided. Staff with `clients.close` prepare a request, and a
different member of staff approves it, which closes the account. Either may decline it.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.closures import service
from app.modules.closures.domain.blockers import Blocker, Standing, blockers
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.passkeys import RECENT_SIGN_IN
from app.modules.identity.service import StaffSession
from app.modules.kyc.service import identities
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409)
}
client_router = APIRouter(
    prefix="/client/account-closure", tags=["account closure"], responses=_REFUSALS
)
staff_router = APIRouter(prefix="/staff/closures", tags=["account closure"], responses=_REFUSALS)
routers = [client_router, staff_router]

Closer = Annotated[StaffSession, Depends(require_permission("clients.close"))]
# Approving closes the account for good, so it needs a passkey or app code from just now.
Approver = Annotated[StaffSession, Depends(require_confirmed("clients.close"))]
Note = Annotated[str, Field(min_length=1, max_length=service.MAX_NOTE)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class AmountView(Contract):
    # Whole units of the smallest denomination: shillings for UGX, cents for USD.
    amount_minor: int
    currency: str


class StandingView(Contract):
    running_investments: int
    owed: list[AmountView]
    open_deposits: int
    open_payouts: int
    open_goals: int
    live_plans: int
    # Every reason the account cannot close yet. Empty when it can.
    blockers: list[Blocker]


class ClosureView(Contract):
    id: PushId
    status: service.Status
    reason: str | None
    requested_at: datetime
    # What staff said when they declined it. The client sees it.
    decision_note: str | None
    decided_at: datetime | None
    cancelled_at: datetime | None


class ClientClosureView(Contract):
    standing: StandingView
    # The latest request, open or ended. None if the client never asked.
    request: ClosureView | None


class AskBody(Contract):
    reason: str | None = Field(default=None, max_length=service.MAX_NOTE)


class StaffClosureView(ClosureView):
    client_id: PushId
    # The name on the approved identity document, and the account number.
    verified_name: str | None
    account_number: str | None
    prepared_by: PushId | None
    prepared_by_name: str | None
    prepared_at: datetime | None
    prepared_note: str | None
    decided_by: PushId | None
    decided_by_name: str | None
    standing: StandingView
    # What this member of staff may do with it now.
    can_prepare: bool
    can_approve: bool
    can_decline: bool


class StaffClosureList(Contract):
    items: list[StaffClosureView]


class NoteBody(Contract):
    note: Note


def _standing(found: Standing) -> StandingView:
    return StandingView(
        running_investments=found.running_investments,
        owed=[AmountView(amount_minor=m.minor, currency=m.currency.value) for m in found.owed],
        open_deposits=found.open_deposits,
        open_payouts=found.open_payouts,
        open_goals=found.open_goals,
        live_plans=found.live_plans,
        blockers=blockers(found),
    )


def _closure(found: service.ClosureRequest) -> ClosureView:
    return ClosureView(
        id=found.id,
        status=found.status,
        reason=found.reason,
        requested_at=found.requested_at,
        decision_note=found.decision_note if found.status == "declined" else None,
        decided_at=found.decided_at,
        cancelled_at=found.cancelled_at,
    )


async def _client_view(session: AsyncSession, client_id: str) -> ClientClosureView:
    found = await service.latest(session, client_id)
    return ClientClosureView(
        standing=_standing(await service.standing(session, client_id)),
        request=_closure(found) if found else None,
    )


@client_router.get("")
async def my_closure(client: CurrentClient, database: Db) -> ClientClosureView:
    """What still stops the account closing, and the latest request to close it."""
    async with database.actor_transaction(client.actor) as session:
        return await _client_view(session, client.client_id)


@client_router.post("", status_code=201)
async def ask_to_close(
    body: AskBody, client: CurrentClient, database: Db, clock: Time
) -> ClientClosureView:
    """Ask to close the account. Needs a sign-in from the last few minutes, and nothing blocking.

    A refusal lists what blocks it as `blockers` field problems.
    """
    if clock.now() - client.signed_in_at > RECENT_SIGN_IN:
        raise ApplicationError("sign_in_again")
    async with database.actor_transaction(client.actor) as session:
        await service.ask(session, client.actor, body.reason, clock)
        return await _client_view(session, client.client_id)


@client_router.post("/{request_id}/cancellation")
async def cancel_closure(
    request_id: PushId, client: CurrentClient, database: Db, clock: Time
) -> ClientClosureView:
    """Keep the account open after all, while staff have not decided."""
    async with database.actor_transaction(client.actor) as session:
        await service.cancel(session, client.actor, request_id, clock)
        return await _client_view(session, client.client_id)


async def _staff_view(
    session: AsyncSession, staff: StaffSession, found: service.ClosureRequest
) -> StaffClosureView:
    check = (await identities(session, [found.client_id])).get(found.client_id)
    may = "clients.close" in staff.permissions
    return StaffClosureView(
        **_closure(found).model_dump(),
        client_id=found.client_id,
        verified_name=check.verified_name if check else None,
        account_number=check.account_number if check else None,
        prepared_by=found.prepared_by,
        prepared_by_name=found.prepared_by_name,
        prepared_at=found.prepared_at,
        prepared_note=found.prepared_note,
        decided_by=found.decided_by,
        decided_by_name=found.decided_by_name,
        standing=_standing(await service.standing(session, found.client_id)),
        can_prepare=may and found.status == "requested",
        can_approve=may and found.status == "prepared" and found.prepared_by != staff.staff_id,
        can_decline=may and found.is_open,
    )


@staff_router.get("")
async def open_closures(staff: Closer, database: Db) -> StaffClosureList:
    """Every request to close an account that waits for staff, oldest first."""
    async with database.actor_transaction(staff.actor) as session:
        return StaffClosureList(
            items=[
                await _staff_view(session, staff, item)
                for item in await service.open_requests(session)
            ]
        )


@staff_router.get("/{request_id}")
async def closure(request_id: PushId, staff: Closer, database: Db) -> StaffClosureView:
    async with database.actor_transaction(staff.actor) as session:
        return await _staff_view(session, staff, await service.get(session, request_id))


@staff_router.post("/{request_id}/preparation")
async def prepare_closure(
    request_id: PushId, body: NoteBody, staff: Closer, database: Db, clock: Time
) -> StaffClosureView:
    """Check the request and pass it to a second member of staff to close."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.prepare(session, staff.actor, request_id, body.note, clock)
        return await _staff_view(session, staff, found)


@staff_router.post("/{request_id}/approval")
async def approve_closure(
    request_id: PushId, body: NoteBody, staff: Approver, database: Db, clock: Time
) -> StaffClosureView:
    """Close the account of someone else's prepared request. It cannot be undone."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.approve(session, staff.actor, request_id, body.note, clock)
        return await _staff_view(session, staff, found)


@staff_router.post("/{request_id}/decline")
async def decline_closure(
    request_id: PushId, body: NoteBody, staff: Closer, database: Db, clock: Time
) -> StaffClosureView:
    """Turn the request down. The client sees the note and the account stays open."""
    async with database.actor_transaction(staff.actor) as session:
        found = await service.decline(session, staff.actor, request_id, body.note, clock)
        return await _staff_view(session, staff, found)
