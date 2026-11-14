"""The Treasury bill auction calendar over HTTP.

`GET /staff/dealing/auctions` lists the dates from a month ago on, and any change still waiting,
with `dealing_desk.view`. Proposing a date or a cancellation, and approving, declining or
withdrawing a change, need `dealing_orders.change` and an `Idempotency-Key`, so a retry from a
weak connection never applies a step twice. Every command answers with the calendar.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.modules.dealing import auctions
from app.modules.dealing.domain.auctions import Change, State, Status, Step
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422)
}

ProductCode = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9-]+$")]
DateIn = Annotated[date, Field(strict=False)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class AuctionIn(Contract):
    product_code: ProductCode
    auction_date: DateIn


class CancelIn(Contract):
    reason: Reason


class DeclineIn(Contract):
    note: Reason


class CancellationOut(Contract):
    id: str
    reason: str
    status: Status
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    # The steps the reader may take on the cancellation.
    allowed: list[Step]


class AuctionOut(Contract):
    id: str
    product_code: str
    auction_date: date
    # waiting: for a second person; standing: counts on the desk; cancelling: still counts, a
    # cancellation waits; cancelled; declined or withdrawn: never counted.
    state: State
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    # The cancellation waiting or approved, else the latest one declined or withdrawn.
    cancellation: CancellationOut | None
    # The steps the reader may take on the date: approve, decline, withdraw or cancel.
    allowed: list[Step]


class CalendarOut(Contract):
    # From a month ago on, and any date still waiting, earliest first.
    auctions: list[AuctionOut]
    # False for a reader who may not propose or decide a change.
    may_change: bool


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
DeskStaff = Annotated[StaffSession, Depends(require_permission("dealing_desk.view"))]
Changer = Annotated[StaffSession, Depends(require_permission("dealing_orders.change"))]
# Approving asks for a recent passkey or app code, as every other approval does.
Approver = Annotated[StaffSession, Depends(require_confirmed("dealing_orders.change"))]


def _cancellation(change: Change, allowed: list[Step]) -> CancellationOut:
    return CancellationOut(
        id=change.id,
        reason=change.reason or "",
        status=change.status,
        proposed_by=change.proposed_by,
        proposed_at=change.proposed_at,
        decided_by=change.decided_by,
        decided_at=change.decided_at,
        decision_note=change.decision_note,
        allowed=allowed,
    )


async def _calendar(session: AsyncSession, staff: StaffSession, clock: Clock) -> CalendarOut:
    may_change = "dealing_orders.change" in staff.permissions
    shown = []
    for view in await auctions.calendar(session, staff.actor, clock):
        entry = view.entry
        shown.append(
            AuctionOut(
                id=entry.id,
                product_code=entry.product_code,
                auction_date=entry.auction_date,
                state=view.state,
                proposed_by=entry.proposed_by,
                proposed_at=entry.proposed_at,
                decided_by=entry.decided_by,
                decided_at=entry.decided_at,
                decision_note=entry.decision_note,
                cancellation=(
                    _cancellation(
                        view.cancellation, view.cancellation_allowed if may_change else []
                    )
                    if view.cancellation
                    else None
                ),
                allowed=view.allowed if may_change else [],
            )
        )
    return CalendarOut(auctions=shown, may_change=may_change)


async def _command(
    request: Request,
    staff: StaffSession,
    clock: Clock,
    *,
    operation: str,
    key: str,
    body: dict[str, object],
    command: Callable[[AsyncSession], Awaitable[None]],
) -> CalendarOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:

        async def run() -> Receipt:
            await command(session)
            return Receipt(200, {})

        await run_once(
            session,
            actor=staff.actor,
            operation=operation,
            key=key,
            request=body,
            now=clock.now(),
            command=run,
        )
        return await _calendar(session, staff, clock)


router = APIRouter(prefix="/staff/dealing/auctions", tags=["dealing desk"], responses=_REFUSALS)


@router.get("")
async def get_calendar(request: Request, staff: DeskStaff, clock: AppClock) -> CalendarOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        return await _calendar(session, staff, clock)


@router.post("")
async def propose_auction(
    request: Request, staff: Changer, clock: AppClock, key: IdempotencyKey, body: AuctionIn
) -> CalendarOut:
    async def command(session: AsyncSession) -> None:
        await auctions.propose(session, staff.actor, body.product_code, body.auction_date, clock)

    return await _command(
        request,
        staff,
        clock,
        operation="dealing.auction.propose",
        key=key,
        body=body.model_dump(mode="json"),
        command=command,
    )


@router.post("/{auction_id}/cancel")
async def propose_cancellation(
    request: Request,
    staff: Changer,
    clock: AppClock,
    key: IdempotencyKey,
    auction_id: str,
    body: CancelIn,
) -> CalendarOut:
    async def command(session: AsyncSession) -> None:
        await auctions.propose_cancel(session, staff.actor, auction_id, body.reason, clock)

    return await _command(
        request,
        staff,
        clock,
        operation="dealing.auction.cancel",
        key=key,
        body={"auction_id": auction_id, **body.model_dump(mode="json")},
        command=command,
    )


async def _decide(
    request: Request,
    staff: StaffSession,
    clock: Clock,
    key: str,
    change_id: str,
    step: Literal["approve", "decline", "withdraw"],
    note: str = "",
) -> CalendarOut:
    async def command(session: AsyncSession) -> None:
        await auctions.decide(session, staff.actor, change_id, step, note, clock)

    body: dict[str, object] = {"change_id": change_id}
    if step == "decline":
        body["note"] = note
    return await _command(
        request,
        staff,
        clock,
        operation=f"dealing.auction.{step}",
        key=key,
        body=body,
        command=command,
    )


@router.post("/changes/{change_id}/approve")
async def approve_change(
    request: Request, staff: Approver, clock: AppClock, key: IdempotencyKey, change_id: str
) -> CalendarOut:
    return await _decide(request, staff, clock, key, change_id, "approve")


@router.post("/changes/{change_id}/withdraw")
async def withdraw_change(
    request: Request, staff: Changer, clock: AppClock, key: IdempotencyKey, change_id: str
) -> CalendarOut:
    return await _decide(request, staff, clock, key, change_id, "withdraw")


@router.post("/changes/{change_id}/decline")
async def decline_change(
    request: Request,
    staff: Changer,
    clock: AppClock,
    key: IdempotencyKey,
    change_id: str,
    body: DeclineIn,
) -> CalendarOut:
    return await _decide(request, staff, clock, key, change_id, "decline", body.note)
