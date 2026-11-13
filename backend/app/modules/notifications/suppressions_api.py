"""Routes for email suppressions in the console.

Seeing the list is `clients.view`, so whoever helps a client can see why their email stopped.
Proposing or deciding a lift is `email_suppressions.lift`. Every lift needs two people.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession, staff_names
from app.modules.notifications import suppression_lifts as lifts
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}
router = APIRouter(
    prefix="/staff/email-suppressions", tags=["staff email suppressions"], responses=_REFUSALS
)
routers = [router]

Viewer = Annotated[StaffSession, Depends(require_permission("clients.view"))]
Lifter = Annotated[StaffSession, Depends(require_permission("email_suppressions.lift"))]
Reason = Annotated[str, Field(min_length=1, max_length=lifts.MAX_REASON)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


def _hash_key(request: Request) -> bytes | None:
    key: bytes | None = request.app.state.email_hash_key
    return key


HashKey = Annotated[bytes | None, Depends(_hash_key)]


class LiftView(Contract):
    id: PushId
    address_hash: str
    reason: str
    proposed_by: PushId
    # The member of staff's name, so the second person sees who asked.
    proposed_by_name: str | None
    proposed_at: datetime
    status: Literal["proposed", "approved", "rejected", "withdrawn"]
    decided_by: PushId | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    yours: bool


class SuppressionView(Contract):
    address_hash: str
    reason: Literal["bounced", "complained"]
    created_at: datetime
    last_event_at: datetime | None
    suppressed: bool
    lifted_at: datetime | None
    # Never lifted: the address sent a spam complaint.
    complained: bool
    open_lift: LiftView | None


class SuppressionList(Contract):
    suppressions: list[SuppressionView]
    can_lift: bool


class Lookup(Contract):
    email: Annotated[str, Field(min_length=3, max_length=254)]


class LookupResult(Contract):
    # None when the address was never suppressed.
    suppression: SuppressionView | None


class ProposeLift(Contract):
    reason: Reason


class DecideLift(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


@router.get("")
async def list_suppressions(staff: Viewer, database: Db) -> SuppressionList:
    """The newest 200 suppressions, whether each still applies, and any lift waiting."""
    async with database.actor_transaction(staff.actor) as session:
        found = await lifts.list_suppressions(session)
        names = await staff_names(session, _staff_ids([i.open_lift for i in found if i.open_lift]))
    return SuppressionList(
        suppressions=[_view(item, staff, names) for item in found],
        can_lift="email_suppressions.lift" in staff.permissions,
    )


@router.post("/lookup")
async def lookup(body: Lookup, staff: Viewer, database: Db) -> LookupResult:
    """Whether one address is suppressed. The address is hashed and not kept. A POST, so the
    address never appears in a URL or a log."""
    async with database.actor_transaction(staff.actor) as session:
        found = await lifts.suppression_for_address(session, body.email)
        names = await staff_names(
            session, _staff_ids([found.open_lift] if found and found.open_lift else [])
        )
    return LookupResult(suppression=_view(found, staff, names) if found else None)


@router.post("/{address_hash}/lifts", status_code=201)
async def propose_lift(
    address_hash: Hash,
    body: ProposeLift,
    staff: Lifter,
    database: Db,
    clock: Time,
    hash_key: HashKey,
) -> LiftView:
    """Ask to email the address again. A different member of staff decides."""
    async with database.actor_transaction(staff.actor) as session:
        lift = await lifts.propose(session, staff.actor, address_hash, body.reason, clock, hash_key)
        names = await staff_names(session, _staff_ids([lift]))
    return _lift(lift, staff, names)


@router.post("/lifts/{lift_id}/decision")
async def decide_lift(
    lift_id: PushId, body: DecideLift, staff: Lifter, database: Db, clock: Time
) -> LiftView:
    """Approve or reject someone else's lift, or withdraw your own."""
    async with database.actor_transaction(staff.actor) as session:
        lift = await lifts.decide(session, staff.actor, lift_id, body.decision, body.reason, clock)
        names = await staff_names(session, _staff_ids([lift]))
    return _lift(lift, staff, names)


def _staff_ids(found: list[lifts.Lift]) -> set[str]:
    return {lift.proposed_by for lift in found} | {
        lift.decided_by for lift in found if lift.decided_by
    }


def _view(item: lifts.Suppression, staff: StaffSession, names: dict[str, str]) -> SuppressionView:
    if item.reason not in ("bounced", "complained"):
        raise ApplicationError("internal_error")
    return SuppressionView(
        address_hash=item.address_hash,
        reason=item.reason,  # type: ignore[arg-type]
        created_at=item.created_at,
        last_event_at=item.last_event_at,
        suppressed=item.suppressed,
        lifted_at=item.lifted_at,
        complained=item.complained,
        open_lift=_lift(item.open_lift, staff, names) if item.open_lift else None,
    )


def _lift(lift: lifts.Lift, staff: StaffSession, names: dict[str, str]) -> LiftView:
    return LiftView(
        id=lift.id,
        address_hash=lift.address_hash,
        reason=lift.reason,
        proposed_by=lift.proposed_by,
        proposed_by_name=names.get(lift.proposed_by),
        proposed_at=lift.proposed_at,
        status=lift.status,  # type: ignore[arg-type]
        decided_by=lift.decided_by,
        decided_by_name=names.get(lift.decided_by) if lift.decided_by else None,
        decided_at=lift.decided_at,
        decision_reason=lift.decision_reason,
        yours=lift.proposed_by == staff.staff_id,
    )
