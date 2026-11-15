"""GET /staff/money-movements: every payment in and every payout, newest first, for staff.

The Money movement screen shows exceptions (payouts held up, never answered, returned). This is
the plain list beside them, so staff can see every movement without downloading the deposits and
withdrawals report (QA finding F-010). It only reads, through the payments and payouts modules'
own read functions, and names each client through `console.clients.client_cards`.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.console.clients import client_cards
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.modules.payments.domain.deposits import DepositStatus
from app.modules.payments.service import newest_deposits
from app.modules.payouts.domain.payouts import PayoutStatus
from app.modules.payouts.payouts import newest_payouts
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.push_ids import PUSH_ID_PATTERN

router = APIRouter(
    prefix="/staff/money-movements",
    tags=["staff money movement"],
    responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
)

MoneyViewer = Annotated[StaffSession, Depends(require_permission("money_movement.view"))]

PAGE = 50
_PUSH_ID = re.compile(PUSH_ID_PATTERN)
Show = Literal["all", "deposits", "payouts", "failed", "unmatched"]

# What each filter shows. None: every status. A kind left out of a filter is not read at all.
# Unmatched: what is not yet tied to a confirmed outcome, a bank transfer not yet found on the
# statement, or a payment or payout whose outcome the provider has not told us.
_DEPOSITS: dict[Show, list[DepositStatus] | None] = {
    "all": None,
    "deposits": None,
    "failed": [DepositStatus.FAILED],
    "unmatched": [DepositStatus.IN_REVIEW, DepositStatus.UNCERTAIN],
}
_PAYOUTS: dict[Show, list[PayoutStatus] | None] = {
    "all": None,
    "payouts": None,
    "failed": [PayoutStatus.FAILED],
    "unmatched": [PayoutStatus.UNCERTAIN],
}


@dataclass(frozen=True, slots=True)
class Movement:
    kind: Literal["deposit", "payout"]
    id: str
    client_id: str
    # mtn, airtel or bank_transfer for a deposit; mobile_money or bank for a payout.
    channel: str
    amount_minor: int
    currency: str
    status: str
    failure_code: str | None
    created_at: datetime
    # When it ended: settled for a deposit, last changed for a final payout. None while open.
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class MovementPage:
    movements: list[Movement]
    next_after: tuple[datetime, str] | None


async def movements(
    session: AsyncSession, show: Show, *, after: tuple[datetime, str] | None, limit: int
) -> MovementPage:
    """A page of movements, newest first, from both modules.

    Each module is read for one more than a page before the same place, so the merged page is
    exactly what one list of both would hold there.
    """
    found: list[Movement] = []
    if show in _DEPOSITS:
        for deposit in await newest_deposits(
            session, statuses=_DEPOSITS[show], before=after, limit=limit + 1
        ):
            found.append(
                Movement(
                    kind="deposit",
                    id=deposit.id,
                    client_id=deposit.client_id,
                    channel=deposit.method.value,
                    amount_minor=deposit.amount.minor,
                    currency=deposit.amount.currency.value,
                    status=deposit.status.value,
                    failure_code=deposit.code if deposit.status is DepositStatus.FAILED else None,
                    created_at=deposit.created_at,
                    ended_at=deposit.settled_at,
                )
            )
    if show in _PAYOUTS:
        for record in await newest_payouts(
            session, statuses=_PAYOUTS[show], before=after, limit=limit + 1
        ):
            payout = record.payout
            final = payout.status in (
                PayoutStatus.PAID,
                PayoutStatus.FAILED,
                PayoutStatus.CANCELLED,
            )
            found.append(
                Movement(
                    kind="payout",
                    id=payout.id,
                    client_id=payout.client_id,
                    channel=payout.kind.value,
                    amount_minor=payout.amount.minor,
                    currency=payout.amount.currency.value,
                    status=payout.status.value,
                    failure_code=payout.failure_code,
                    created_at=payout.created_at,
                    ended_at=record.updated_at if final else None,
                )
            )
    found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    page = found[:limit]
    last = page[-1] if len(found) > limit else None
    return MovementPage(page, (last.created_at, last.id) if last else None)


class MovementAmountOut(Contract):
    minor: int
    currency: str


class MovementView(Contract):
    kind: Literal["deposit", "payout"]
    id: PushId
    client_id: PushId
    # The name on the client's approved identity document, or None until it is approved.
    client_name: str | None
    channel: str
    amount: MovementAmountOut
    status: str
    failure_code: str | None
    created_at: datetime
    ended_at: datetime | None


class MovementListView(Contract):
    movements: list[MovementView]
    # Pass as `after` for the next page. None on the last page.
    next_after: str | None


def _after(value: str | None) -> tuple[datetime, str] | None:
    """`next_after` as given: when the last movement was asked for and its ID, by a space."""
    if value is None:
        return None
    joined, _, record_id = value.partition(" ")
    try:
        when = datetime.fromisoformat(joined)
    except ValueError:
        when = None
    if when is None or when.tzinfo is None or _PUSH_ID.fullmatch(record_id) is None:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["query", "after"], code="bad_after")],
        )
    return when, record_id


@router.get("")
async def list_movements(
    request: Request,
    staff: MoneyViewer,
    show: Annotated[Show, Query()] = "all",
    after: Annotated[str | None, Query(max_length=80)] = None,
) -> MovementListView:
    """Every payment in and payout, newest first, 50 to a page. `show` narrows it to deposits,
    payouts, failed ones, or unmatched ones (in review or with an outcome not yet known)."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        page = await movements(session, show, after=_after(after), limit=PAGE)
        names = {
            card.account.id: card.verified_name
            for card in await client_cards(session, [item.client_id for item in page.movements])
        }
    return MovementListView(
        movements=[
            MovementView(
                kind=item.kind,
                id=item.id,
                client_id=item.client_id,
                client_name=names.get(item.client_id),
                channel=item.channel,
                amount=MovementAmountOut(minor=item.amount_minor, currency=item.currency),
                status=item.status,
                failure_code=item.failure_code,
                created_at=item.created_at,
                ended_at=item.ended_at,
            )
            for item in page.movements
        ],
        next_after=f"{page.next_after[0].isoformat()} {page.next_after[1]}"
        if page.next_after
        else None,
    )
