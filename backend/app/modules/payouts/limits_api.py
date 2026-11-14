"""Staff routes for the withdrawal limits and the review threshold, on the Settings screen.

Seeing them is `settings.view`. Proposing or deciding a new version is
`withdrawal_limits.change`, and a different member of staff always approves.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import limit_changes
from app.modules.payouts.domain.limits import MOBILE_MONEY_PER_PAYOUT_UGX, WithdrawalLimits
from app.modules.payouts.payouts import current_limits
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope
from app.shared.money import Currency, Money

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

staff_router = APIRouter(prefix="/staff", tags=["staff withdrawal limits"], responses=_REFUSALS)
routers = [staff_router]

Viewer = Annotated[StaffSession, Depends(require_permission("settings.view"))]
Changer = Annotated[StaffSession, Depends(require_permission("withdrawal_limits.change"))]
# Deciding needs a passkey or app code from the last few minutes.
Decider = Annotated[StaffSession, Depends(require_confirmed("withdrawal_limits.change"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
# Shillings. A mobile money wallet takes at most 5,000,000 in one transfer.
MobileMinor = Annotated[int, Field(ge=1, le=MOBILE_MONEY_PER_PAYOUT_UGX.minor)]
AmountMinor = Annotated[int, Field(ge=1, le=10_000_000_000)]
DateIn = Annotated[date, Field(strict=False)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class LimitsView(Contract):
    """One version, in shillings. A limit that is None is not set."""

    effective_from: date
    currency: Literal["UGX"]
    mobile_money_per_payout_minor: int
    mobile_money_per_day_minor: int | None
    bank_per_payout_minor: int | None
    bank_per_day_minor: int | None
    # A payout, or a transfer to another client, above this waits for two members of staff.
    review_above_minor: int | None


class LimitsChangeView(Contract):
    id: PushId
    limits: LimitsView
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    # The proposer can withdraw it. Anyone else who may change the limits can decide it.
    yours: bool


class LimitsScreen(Contract):
    in_force: LimitsView
    # Versions approved to start on a later day, soonest first.
    upcoming: list[LimitsView]
    change: LimitsChangeView | None
    can_change: bool


class ProposeLimits(Contract):
    # A later day than today, Kampala time.
    effective_from: DateIn
    mobile_money_per_payout_minor: MobileMinor
    mobile_money_per_day_minor: AmountMinor | None = None
    bank_per_payout_minor: AmountMinor | None = None
    bank_per_day_minor: AmountMinor | None = None
    review_above_minor: AmountMinor | None = None
    reason: Reason


class DecideLimits(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


def _minor(amount: Money | None) -> int | None:
    return None if amount is None else amount.minor


def _view(limits: WithdrawalLimits) -> LimitsView:
    return LimitsView(
        effective_from=limits.effective_from,
        currency="UGX",
        mobile_money_per_payout_minor=limits.mobile_money_per_payout.minor,
        mobile_money_per_day_minor=_minor(limits.mobile_money_per_day),
        bank_per_payout_minor=_minor(limits.bank_per_payout),
        bank_per_day_minor=_minor(limits.bank_per_day),
        review_above_minor=_minor(limits.review_above),
    )


def _change(change: limit_changes.LimitsChange, staff: StaffSession) -> LimitsChangeView:
    return LimitsChangeView(
        id=change.id,
        limits=_view(change.limits),
        reason=change.reason,
        proposed_by=change.proposed_by,
        proposed_at=change.proposed_at,
        yours=change.proposed_by == staff.staff_id,
    )


_UPCOMING = text(
    "SELECT effective_from FROM withdrawal_limits WHERE effective_from > :today "
    "ORDER BY effective_from"
)


@staff_router.get("/withdrawal-limits")
async def limits_screen(staff: Viewer, database: Db, clock: Time) -> LimitsScreen:
    """The limits in force today, versions approved for later days, and any change waiting."""
    today = business_date(clock.now())
    async with database.actor_transaction(staff.actor) as session:
        in_force = await current_limits(session, today)
        days = (await session.execute(_UPCOMING, {"today": today})).scalars().all()
        upcoming = [await current_limits(session, day) for day in days]
        change = await limit_changes.waiting(session)
    return LimitsScreen(
        in_force=_view(in_force),
        upcoming=[_view(limits) for limits in upcoming],
        change=_change(change, staff) if change else None,
        can_change="withdrawal_limits.change" in staff.permissions,
    )


@staff_router.post("/withdrawal-limits/changes", status_code=201)
async def propose_limits(
    body: ProposeLimits, staff: Changer, database: Db, clock: Time
) -> LimitsChangeView:
    """Put a whole new version of the limits forward, starting on a later day.

    422 `limits_invalid` for limits that contradict themselves (a daily limit below the one for
    one payout), `not_a_later_day` for a start of today or before. 409 if a change is already
    waiting, or `day_taken` if a version already starts that day.
    """

    def ugx(minor: int | None) -> Money | None:
        return None if minor is None else Money(minor, Currency.UGX)

    async with database.actor_transaction(staff.actor) as session:
        change = await limit_changes.propose(
            session,
            staff.actor,
            effective_from=body.effective_from,
            mobile_money_per_payout=Money(body.mobile_money_per_payout_minor, Currency.UGX),
            mobile_money_per_day=ugx(body.mobile_money_per_day_minor),
            bank_per_payout=ugx(body.bank_per_payout_minor),
            bank_per_day=ugx(body.bank_per_day_minor),
            review_above=ugx(body.review_above_minor),
            reason=body.reason,
            clock=clock,
        )
    return _change(change, staff)


@staff_router.post("/withdrawal-limits/changes/{change_id}/decision")
async def decide_limits(
    change_id: PushId, body: DecideLimits, staff: Decider, database: Db, clock: Time
) -> LimitsChangeView:
    """Approve or reject another person's change, or withdraw your own.

    Approving makes the version, which applies from its start day. 409 `not_a_later_day` once
    that day has come, `day_taken` if another version took it.
    """
    async with database.actor_transaction(staff.actor) as session:
        change = await limit_changes.decide(
            session, staff.actor, change_id, body.decision, body.reason, clock
        )
    return _change(change, staff)
