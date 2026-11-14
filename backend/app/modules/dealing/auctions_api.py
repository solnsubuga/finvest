"""The Treasury bill auction calendar over HTTP.

`GET /staff/dealing/auctions` lists the dates from a month ago on, with `dealing_desk.view`.
Entering and cancelling a date need `dealing_orders.change` and an `Idempotency-Key`, so a retry
from a weak connection never enters a date twice. Every command answers with the calendar.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.modules.dealing import auctions
from app.modules.identity.api import require_permission
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


class AuctionOut(Contract):
    id: str
    product_code: str
    auction_date: date
    added_by: str
    added_at: datetime
    cancelled_by: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None


class CalendarOut(Contract):
    # From a month ago on, cancelled dates included, earliest first.
    auctions: list[AuctionOut]
    # False for a reader who may not enter or cancel a date.
    may_change: bool


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
DeskStaff = Annotated[StaffSession, Depends(require_permission("dealing_desk.view"))]
Changer = Annotated[StaffSession, Depends(require_permission("dealing_orders.change"))]


async def _calendar(session: AsyncSession, staff: StaffSession, clock: Clock) -> CalendarOut:
    return CalendarOut(
        auctions=[
            AuctionOut(
                id=a.id,
                product_code=a.product_code,
                auction_date=a.auction_date,
                added_by=a.added_by,
                added_at=a.added_at,
                cancelled_by=a.cancelled_by,
                cancelled_at=a.cancelled_at,
                cancel_reason=a.cancel_reason,
            )
            for a in await auctions.calendar(session, clock)
        ],
        may_change="dealing_orders.change" in staff.permissions,
    )


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
async def add_auction(
    request: Request, staff: Changer, clock: AppClock, key: IdempotencyKey, body: AuctionIn
) -> CalendarOut:
    async def command(session: AsyncSession) -> None:
        await auctions.add(session, staff.actor, body.product_code, body.auction_date, clock)

    return await _command(
        request,
        staff,
        clock,
        operation="dealing.auction.add",
        key=key,
        body=body.model_dump(mode="json"),
        command=command,
    )


@router.post("/{auction_id}/cancel")
async def cancel_auction(
    request: Request,
    staff: Changer,
    clock: AppClock,
    key: IdempotencyKey,
    auction_id: str,
    body: CancelIn,
) -> CalendarOut:
    async def command(session: AsyncSession) -> None:
        await auctions.cancel(session, staff.actor, auction_id, body.reason, clock)

    return await _command(
        request,
        staff,
        clock,
        operation="dealing.auction.cancel",
        key=key,
        body={"auction_id": auction_id, **body.model_dump(mode="json")},
        command=command,
    )
