"""The Treasury bill auction calendar, kept by staff with `dealing_orders.change`.

One person enters a date and can cancel it with a reason. A date moves no money and no order
depends on it, so it takes one person (decided for now, see docs/decisions.md). Every entry and
cancellation is audited. The table's guard (migration 0088) holds that nothing is removed and a
date never changes, only its cancellation, once.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.dealing.domain.auctions import (
    AuctionDate,
    Refusal,
    check_cancel,
    check_new,
    next_auctions,
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
    "id, product_code, auction_date, added_by, added_at, cancelled_by, cancelled_at, cancel_reason"
)
_FROM = text(
    f"SELECT {_COLUMNS} FROM dealing_auctions "  # noqa: S608
    "WHERE auction_date >= :since ORDER BY auction_date, product_code, added_at, id"
)
_STANDING_ON = text(
    "SELECT product_code, min(auction_date) AS auction_date FROM dealing_auctions "
    "WHERE cancelled_by IS NULL AND auction_date >= :on GROUP BY product_code"
)
_FOR_PRODUCT_DAY = text(
    "SELECT product_code, auction_date, cancelled_by FROM dealing_auctions "
    "WHERE product_code = :product_code AND auction_date = :auction_date"
)
_LOCKED = text(f"SELECT {_COLUMNS} FROM dealing_auctions WHERE id = :id FOR UPDATE")  # noqa: S608
_INSERT = text(
    "INSERT INTO dealing_auctions (id, product_code, auction_date, added_by, added_at) "
    "VALUES (:id, :product_code, :auction_date, :by, :now)"
)
_CANCEL = text(
    "UPDATE dealing_auctions SET cancelled_by = :by, cancelled_at = :now, cancel_reason = :reason "
    "WHERE id = :id"
)


@dataclass(frozen=True, slots=True)
class AuctionView:
    id: str
    product_code: str
    auction_date: date
    added_by: str
    added_at: datetime
    cancelled_by: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None


async def calendar(session: AsyncSession, clock: Clock) -> list[AuctionView]:
    """Dates from a month ago on, cancelled ones included, earliest first."""
    since = business_date(clock.now()) - SHOWN_BACK
    return [_view(row) for row in (await session.execute(_FROM, {"since": since})).all()]


async def next_by_product(session: AsyncSession, on: date) -> dict[str, date]:
    """Each product's earliest standing auction on or after `on`."""
    rows = (await session.execute(_STANDING_ON, {"on": on})).all()
    return next_auctions((AuctionDate(r.product_code, r.auction_date, False) for r in rows), on)


async def add(
    session: AsyncSession, staff: Actor, product_code: str, auction_date: date, clock: Clock
) -> str:
    _staff_only(staff)
    products = {
        view.product.code: view.product for view in await investments.list_products(session, clock)
    }
    product = products.get(product_code)
    if product is None:
        raise _invalid("product_code", "unknown_product")
    existing = [
        AuctionDate(r.product_code, r.auction_date, r.cancelled_by is not None)
        for r in (
            await session.execute(
                _FOR_PRODUCT_DAY, {"product_code": product_code, "auction_date": auction_date}
            )
        ).all()
    ]
    refusal = check_new(
        product_code,
        auction_date,
        treasury_bill=product.kind is ProductKind.TREASURY_BILL,
        today=business_date(clock.now()),
        existing=existing,
    )
    if refusal is not None:
        raise _refused(refusal)
    auction_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": auction_id,
                    "product_code": product_code,
                    "auction_date": auction_date,
                    "by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # Someone entered the same product and day at the same moment.
        raise _refused(Refusal.ALREADY_ENTERED) from error
    await _audit(session, staff, "dealing.auction.added", auction_id, clock)
    return auction_id


async def cancel(
    session: AsyncSession, staff: Actor, auction_id: str, reason: str, clock: Clock
) -> None:
    _staff_only(staff)
    row = (await session.execute(_LOCKED, {"id": auction_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    found = _view(row)
    refusal = check_cancel(
        AuctionDate(found.product_code, found.auction_date, found.cancelled_by is not None), reason
    )
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _CANCEL, {"id": auction_id, "by": staff.id, "now": clock.now(), "reason": reason.strip()}
    )
    await _audit(session, staff, "dealing.auction.cancelled", auction_id, clock, reason=reason)


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal is Refusal.ALREADY_CANCELLED:
        return ApplicationError("conflict")
    field = {
        Refusal.NOT_A_TREASURY_BILL: "product_code",
        Refusal.DATE_PASSED: "auction_date",
        Refusal.ALREADY_ENTERED: "auction_date",
        Refusal.REASON_REQUIRED: "reason",
    }[refusal]
    return _invalid(field, refusal.value)


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    auction_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=action,
            resource_type=RESOURCE,
            resource_id=auction_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
        ),
        clock,
    )


def _view(row: Row[Any]) -> AuctionView:
    return AuctionView(
        id=row.id,
        product_code=row.product_code,
        auction_date=row.auction_date,
        added_by=row.added_by,
        added_at=row.added_at,
        cancelled_by=row.cancelled_by,
        cancelled_at=row.cancelled_at,
        cancel_reason=row.cancel_reason,
    )
