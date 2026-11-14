"""The Treasury bill auction calendar, kept by two members of staff with `dealing_orders.change`.

One person proposes a date for a product, or the cancellation of a standing date with a reason,
and a different person approves or declines it. The proposer may withdraw it while it waits.
Every step is audited. The table's guard (migration 0088) holds the same rules, keeps every row
and lets a change be decided only once. Rules in `domain/auctions.py`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.dealing.domain.auctions import (
    Change,
    ChangeAction,
    Refusal,
    State,
    Status,
    Step,
    allowed,
    cancellation_of,
    check_cancel,
    check_decision,
    check_new,
    next_auctions,
    state_of,
)
from app.modules.investments import service as investments
from app.modules.investments.domain.products import ProductKind
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "dealing_auction"
# How far back the calendar shows dates that have passed.
SHOWN_BACK = timedelta(days=31)

_COLUMNS = (
    "id, action, product_code, auction_date, cancels, reason, status, proposed_by, proposed_at, "
    "decided_by, decided_at, decision_note"
)
# The dates from `since` on, the changes to them, and every change still waiting.
_FROM = text(
    f"SELECT {_COLUMNS} FROM dealing_auctions "  # noqa: S608
    "WHERE auction_date >= :since OR status = 'proposed' "
    "ORDER BY auction_date, product_code, proposed_at, id"
)
_FOR_PRODUCT_DAY = text(
    f"SELECT {_COLUMNS} FROM dealing_auctions "  # noqa: S608
    "WHERE product_code = :product_code AND auction_date = :auction_date"
)
_STANDING_FROM = text(
    f"SELECT {_COLUMNS} FROM dealing_auctions "  # noqa: S608
    "WHERE auction_date >= :on AND status = 'approved'"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM dealing_auctions "  # noqa: S608
    "WHERE status = 'proposed' ORDER BY proposed_at, id"
)
_LOCKED = text(f"SELECT {_COLUMNS} FROM dealing_auctions WHERE id = :id FOR UPDATE")  # noqa: S608
# One change at a time for a product and day, so the checks below read a settled calendar.
_LOCK_DAY = text("SELECT pg_advisory_xact_lock(hashtext('dealing_auction:' || :day))")
_INSERT = text(
    "INSERT INTO dealing_auctions (id, action, product_code, auction_date, cancels, reason, "
    "proposed_by, proposed_at) "
    "VALUES (:id, :action, :product_code, :auction_date, :cancels, :reason, :by, :now)"
)
_DECIDE = text(
    "UPDATE dealing_auctions SET status = :status, decided_by = :by, decided_at = :now, "
    "decision_note = :note WHERE id = :id"
)

_DONE: dict[Step, Status] = {"approve": "approved", "decline": "declined", "withdraw": "withdrawn"}


@dataclass(frozen=True, slots=True)
class DateView:
    """A date in the calendar, its state, any cancellation of it, and the reader's steps."""

    entry: Change
    state: State
    cancellation: Change | None
    allowed: list[Step]
    cancellation_allowed: list[Step]


async def calendar(session: AsyncSession, staff: Actor, clock: Clock) -> list[DateView]:
    """Dates from a month ago on, and any still waiting, earliest first."""
    today = business_date(clock.now())
    changes = await _changes(session, _FROM, {"since": today - SHOWN_BACK})
    me = _staff_id(staff)
    views = []
    for entry in changes:
        if entry.action != "add":
            continue
        cancellation = cancellation_of(entry.id, changes)
        views.append(
            DateView(
                entry=entry,
                state=state_of(entry, changes),
                cancellation=cancellation,
                allowed=allowed(entry, changes, me, today),
                cancellation_allowed=(
                    allowed(cancellation, changes, me, today) if cancellation else []
                ),
            )
        )
    return views


async def next_by_product(session: AsyncSession, on: date) -> dict[str, date]:
    """Each product's earliest standing auction on or after `on`."""
    approved = await _changes(session, _STANDING_FROM, {"on": on})
    return next_auctions(approved, on)


async def waiting(session: AsyncSession) -> list[Change]:
    """Every change waiting for a second person, for the approvals inbox."""
    return await _changes(session, _WAITING, {})


async def propose(
    session: AsyncSession, staff: Actor, product_code: str, auction_date: date, clock: Clock
) -> str:
    """Put a date forward. The desk counts it once a different person approves it."""
    me = _staff_id(staff)
    products = {
        view.product.code: view.product for view in await investments.list_products(session, clock)
    }
    product = products.get(product_code)
    if product is None:
        raise _invalid("product_code", "unknown_product")
    await _lock_day(session, product_code, auction_date)
    refusal = check_new(
        product_code,
        auction_date,
        treasury_bill=product.kind is ProductKind.TREASURY_BILL,
        today=business_date(clock.now()),
        changes=await _day(session, product_code, auction_date),
    )
    if refusal is not None:
        raise _refused(refusal)
    change_id = await _insert(session, "add", product_code, auction_date, None, None, me, clock)
    await _audit(session, me, "add_proposed", change_id, product_code, auction_date, None, clock)
    return change_id


async def propose_cancel(
    session: AsyncSession, staff: Actor, date_id: str, reason: str, clock: Clock
) -> str:
    """Put forward cancelling a standing date. It counts until a different person approves."""
    me = _staff_id(staff)
    found = await _one(session, date_id)
    await _lock_day(session, found.product_code, found.auction_date)
    day = await _day(session, found.product_code, found.auction_date)
    refusal = check_cancel(found, reason, day)
    if refusal is not None:
        raise _refused(refusal)
    reason = reason.strip()
    change_id = await _insert(
        session, "cancel", found.product_code, found.auction_date, found.id, reason, me, clock
    )
    await _audit(
        session,
        me,
        "cancel_proposed",
        change_id,
        found.product_code,
        found.auction_date,
        reason,
        clock,
    )
    return change_id


async def decide(
    session: AsyncSession, staff: Actor, change_id: str, step: Step, note: str, clock: Clock
) -> None:
    """Approve or decline someone else's change, or withdraw your own."""
    if step == "cancel":  # pragma: no cover - the routes never send it
        raise ApplicationError("validation_failed")
    me = _staff_id(staff)
    found = await _one(session, change_id)
    await _lock_day(session, found.product_code, found.auction_date)
    refusal = check_decision(found, step, me, business_date(clock.now()), note=note)
    if refusal is not None:
        raise _refused(refusal)
    note = note.strip()
    try:
        async with session.begin_nested():
            await session.execute(
                _DECIDE,
                {
                    "id": found.id,
                    "status": _DONE[step],
                    "by": me,
                    "now": clock.now(),
                    "note": note or None,
                },
            )
    except IntegrityError as error:
        # The guard found the same product and day already standing.
        raise _refused(Refusal.ALREADY_ENTERED) from error
    await _audit(
        session,
        me,
        f"{found.action}_{_DONE[step]}",
        found.id,
        found.product_code,
        found.auction_date,
        note or None,
        clock,
    )


# Helpers --------------------------------------------------------------------------------------


async def _changes(session: AsyncSession, query: Any, params: dict[str, Any]) -> list[Change]:
    return [_change(row) for row in (await session.execute(query, params)).all()]


async def _day(session: AsyncSession, product_code: str, auction_date: date) -> list[Change]:
    return await _changes(
        session, _FOR_PRODUCT_DAY, {"product_code": product_code, "auction_date": auction_date}
    )


async def _one(session: AsyncSession, change_id: str) -> Change:
    row = (await session.execute(_LOCKED, {"id": change_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _change(row)


async def _lock_day(session: AsyncSession, product_code: str, auction_date: date) -> None:
    await session.execute(_LOCK_DAY, {"day": f"{product_code}:{auction_date.isoformat()}"})


async def _insert(
    session: AsyncSession,
    action: ChangeAction,
    product_code: str,
    auction_date: date,
    cancels: str | None,
    reason: str | None,
    me: str,
    clock: Clock,
) -> str:
    change_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": change_id,
                    "action": action,
                    "product_code": product_code,
                    "auction_date": auction_date,
                    "cancels": cancels,
                    "reason": reason,
                    "by": me,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # The day lock makes this unlikely: the same change was put forward a moment ago.
        raise _refused(
            Refusal.ALREADY_ENTERED if action == "add" else Refusal.CANCELLATION_WAITING
        ) from error
    return change_id


def _staff_id(actor: Actor) -> str:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


_FIELDS: dict[Refusal, str] = {
    Refusal.NOT_A_TREASURY_BILL: "product_code",
    Refusal.DATE_PASSED: "auction_date",
    Refusal.ALREADY_ENTERED: "auction_date",
    Refusal.REASON_REQUIRED: "reason",
    Refusal.NOTE_REQUIRED: "note",
}


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal in (Refusal.OWN_CHANGE, Refusal.NOT_YOURS):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        return ApplicationError("forbidden")
    if refusal in (Refusal.NOT_WAITING, Refusal.NOT_STANDING, Refusal.CANCELLATION_WAITING):
        return ApplicationError("conflict")
    return _invalid(_FIELDS[refusal], refusal.value)


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


async def _audit(
    session: AsyncSession,
    me: str,
    step: str,
    change_id: str,
    product_code: str,
    auction_date: date,
    reason: str | None,
    clock: Clock,
) -> None:
    decided = step.endswith(("_approved", "_declined"))
    await audit.record(
        session,
        AuditEvent(
            category="approval" if decided else "change",
            action=f"dealing.auction.{step}",
            resource_type=RESOURCE,
            resource_id=change_id,
            actor=AuditActor.staff(me),
            # An approval's reason is required by the audit trail; the note is optional here.
            reason=reason or (f"{step.replace('_', ' ')} by a second person" if decided else None),
            details={"product_code": product_code, "auction_date": auction_date.isoformat()},
        ),
        clock,
    )


def _change(row: Row[Any]) -> Change:
    return Change(
        id=row.id,
        action=cast(ChangeAction, row.action),
        product_code=row.product_code,
        auction_date=row.auction_date,
        cancels=row.cancels,
        reason=row.reason,
        status=cast(Status, row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )
