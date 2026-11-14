"""Staff booking an investment for a client, with a second person. The client pays as usual.

When a client asks Finvest to set up an investment for them, by phone, in the help chat or at
the office, one member of staff books it with a reason: the product, the amount and what
happens at maturity. A different one approves it, which asks the worker to save it, or sends it
back with a note. Only the one who booked it withdraws it. The database holds them to that
(migration 0334).

The worker saves the investment exactly as a client's own request in the app: on the product's
terms of that day, unpaid, with the usual payment window, and under every rule a client's
request meets. The client is told, and pays for it as for any other. Nothing here moves money.
If it cannot be saved, for example because the product closed or the client already has the
most unpaid investments allowed, the booking says why. See docs/investing.md, "Booking for a
client".
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments import holdings
from app.modules.investments.catalogue import invalid, product_from_row, product_row
from app.modules.investments.domain.investments import AtMaturity
from app.modules.investments.domain.products import ProductStatus, check_new_investment
from app.modules.kyc import service as kyc
from app.modules.notifications import contract as notifications
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

CARRY_OUT_KIND = "investments.carry_out_booking"
RESOURCE = "investment_booking"
MAX_REASON_LENGTH = 500
MAX_BOOKINGS = 200
logger = structlog.get_logger()

BookingStatus = Literal["proposed", "approved", "sent_back", "withdrawn", "done", "failed"]


@dataclass(frozen=True, slots=True)
class Booking:
    id: str
    client_id: str
    product_code: str
    product_name: str
    amount: Money
    at_maturity: AtMaturity | None
    reason: str
    status: BookingStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    investment_id: str | None
    failure_code: str | None
    finished_at: datetime | None


_COLUMNS = (
    "b.id, b.client_id, p.code AS product_code, p.name AS product_name, b.currency, "
    "b.principal_minor, b.at_maturity, b.reason, b.status, b.proposed_by, b.proposed_at, "
    "b.decided_by, b.decided_at, b.decision_note, b.job_id, b.investment_id, b.failure_code, "
    "b.finished_at"
)
_FROM = "FROM investment_bookings b JOIN investment_products p ON p.id = b.product_id"
_BY_ID = text(f"SELECT {_COLUMNS} {_FROM} WHERE b.id = :id")
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} {_FROM} WHERE b.id = :id FOR UPDATE OF b")
_LIST = text(
    f"SELECT {_COLUMNS} {_FROM} "
    "WHERE (CAST(:status AS text) IS NULL OR b.status = :status) "
    "AND (CAST(:client_id AS text) IS NULL OR b.client_id = :client_id) "
    "ORDER BY b.proposed_at DESC, b.id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO investment_bookings (id, client_id, product_id, currency, principal_minor, "
    "at_maturity, reason, proposed_by, proposed_at) VALUES (:id, :client_id, :product_id, "
    ":currency, :principal_minor, :at_maturity, :reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE investment_bookings SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note, job_id = :job_id WHERE id = :id"
)
_FINISH = text(
    "UPDATE investment_bookings SET status = :status, investment_id = :investment_id, "
    "failure_code = :failure_code, finished_at = :now WHERE id = :id"
)


async def propose_booking(
    session: AsyncSession,
    staff: Actor,
    *,
    client_id: str,
    product_code: str,
    amount: Money,
    at_maturity: AtMaturity | None,
    reason: str,
    clock: Clock,
) -> Booking:
    """Book an investment for a client, for a second member of staff to approve.

    The same checks as a client's own request are made now, so a booking that could never be
    saved is refused at once: 404 for an unknown or unapproved client or product, and 422 on
    `amount` or `at_maturity` as for the client. They are made again when it is saved.
    """
    _staff_only(staff)
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise invalid("reason", "required")
    if await kyc.verified_name(session, client_id) is None:
        raise ApplicationError("not_found")
    row = await product_row(session, product_code)
    product = product_from_row(row)
    if product.status is not ProductStatus.LIVE:
        raise ApplicationError("not_found")
    refusal = check_new_investment(product, amount)
    if refusal is not None:
        raise invalid("amount", refusal.value)
    if product.has_fixed_term:
        at_maturity = at_maturity or AtMaturity.ROLL_OVER
    elif at_maturity is not None:
        raise invalid("at_maturity", "open_term_has_no_maturity")
    booking_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": booking_id,
            "client_id": client_id,
            "product_id": row.id,
            "currency": amount.currency.value,
            "principal_minor": amount.minor,
            "at_maturity": at_maturity.value if at_maturity else None,
            "reason": cleaned,
            "proposed_by": staff.id,
            "now": clock.now(),
        },
    )
    booking = await _booking(session, booking_id)
    await _audit(session, AuditActor.staff(_id(staff)), "change", "proposed", booking, clock)
    return booking


async def approve_booking(
    session: AsyncSession, staff: Actor, booking_id: str, clock: Clock
) -> Booking:
    """Approve as the second person, and ask the worker to save the investment."""
    booking = await _for_decision(session, staff, booking_id)
    if booking.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    now = clock.now()
    job_id = await enqueue(
        session,
        JobRequest(
            kind=CARRY_OUT_KIND,
            idempotency_key=f"booking:{booking.id}",
            payload={"booking_id": booking.id},
        ),
        now=now,
    )
    if job_id is None:
        raise ApplicationError("conflict")
    await _decide(session, staff, booking, "approved", now, job_id=job_id)
    approved = await _booking(session, booking.id)
    await _audit(session, AuditActor.staff(_id(staff)), "approval", "approved", approved, clock)
    logger.info("investment_booking_approved", booking_id=booking.id, job_id=job_id)
    return approved


async def send_back_booking(
    session: AsyncSession, staff: Actor, booking_id: str, note: str, clock: Clock
) -> Booking:
    """Send it back as the second person, with a note for the one who booked it. Nothing is
    saved."""
    booking = await _for_decision(session, staff, booking_id)
    if booking.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    cleaned = note.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise invalid("note", "required")
    await _decide(session, staff, booking, "sent_back", clock.now(), note=cleaned)
    sent_back = await _booking(session, booking.id)
    await _audit(
        session, AuditActor.staff(_id(staff)), "approval", "sent_back", sent_back, clock, cleaned
    )
    return sent_back


async def withdraw_booking(
    session: AsyncSession, staff: Actor, booking_id: str, clock: Clock
) -> Booking:
    """Take back a booking nobody has decided. Only the one who booked it can."""
    booking = await _for_decision(session, staff, booking_id)
    if booking.proposed_by != staff.id:
        raise ApplicationError("forbidden")
    await _decide(session, staff, booking, "withdrawn", clock.now())
    withdrawn = await _booking(session, booking.id)
    await _audit(session, AuditActor.staff(_id(staff)), "change", "withdrawn", withdrawn, clock)
    return withdrawn


async def list_bookings(
    session: AsyncSession,
    staff: Actor,
    *,
    status: BookingStatus | None = None,
    client_id: str | None = None,
    limit: int = 50,
) -> list[Booking]:
    """Bookings, newest first. For the console and the approvals inbox."""
    _staff_only(staff)
    rows = await session.execute(
        _LIST,
        {"status": status, "client_id": client_id, "limit": max(1, min(limit, MAX_BOOKINGS))},
    )
    return [_from_row(row) for row in rows.all()]


async def get_booking(session: AsyncSession, staff: Actor, booking_id: str) -> Booking:
    _staff_only(staff)
    return await _booking(session, booking_id)


# What the worker calls --------------------------------------------------------------------------


async def carry_out_booking(session: AsyncSession, booking_id: str, clock: Clock) -> Booking:
    """Save the investment of an approved booking. Worker only. Safe to repeat.

    A booking already finished is left as it is. A refusal a client's own request would get
    (`forbidden`, `not_found`, `conflict` or a 422) is recorded on the booking as
    `failure_code` and nothing is saved.
    """
    row = (await session.execute(_BY_ID_LOCKED, {"id": booking_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    booking = _from_row(row)
    if booking.status != "approved":
        return booking
    now = clock.now()
    try:
        async with session.begin_nested():
            investment_id = await holdings.create_booked(
                session,
                booking.id,
                booking.client_id,
                booking.product_code,
                booking.amount,
                booking.at_maturity,
                clock,
            )
    except ApplicationError as error:
        code = _failure(error)
        await session.execute(
            _FINISH,
            {
                "id": booking.id,
                "status": "failed",
                "investment_id": None,
                "failure_code": code,
                "now": now,
            },
        )
        failed = await _booking(session, booking.id)
        await _audit(session, AuditActor.system(), "change", "failed", failed, clock)
        logger.info("investment_booking_failed", booking_id=booking.id, error_code=code)
        return failed
    await session.execute(
        _FINISH,
        {
            "id": booking.id,
            "status": "done",
            "investment_id": investment_id,
            "failure_code": None,
            "now": now,
        },
    )
    await notifications.notify(
        session,
        event_name="investment_booked",
        client_id=booking.client_id,
        idempotency_key=booking.id,
        variables={"product": booking.product_name},
        link=notifications.Link("investment", investment_id),
        now=now,
    )
    done = await _booking(session, booking.id)
    await _audit(session, AuditActor.system(), "change", "done", done, clock)
    logger.info("investment_booking_done", booking_id=booking.id, investment_id=investment_id)
    return done


# Internals --------------------------------------------------------------------------------------


def _failure(error: ApplicationError) -> str:
    """Why a booking could not be saved, in the words of a client's own refusal."""
    if error.fields:
        return error.fields[0].code
    if error.code == "conflict":
        return "too_many_unpaid"
    if error.code == "forbidden":
        return "client_not_able"
    return error.code


async def _decide(
    session: AsyncSession,
    staff: Actor,
    booking: Booking,
    status: BookingStatus,
    now: datetime,
    *,
    note: str | None = None,
    job_id: str | None = None,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": booking.id,
            "status": status,
            "decided_by": staff.id,
            "now": now,
            "note": note,
            "job_id": job_id,
        },
    )


async def _booking(session: AsyncSession, booking_id: str) -> Booking:
    row = (await session.execute(_BY_ID, {"id": booking_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _for_decision(session: AsyncSession, staff: Actor, booking_id: str) -> Booking:
    _staff_only(staff)
    row = (await session.execute(_BY_ID_LOCKED, {"id": booking_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    booking = _from_row(row)
    if booking.status != "proposed":
        raise ApplicationError("conflict")
    return booking


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def _from_row(row: Row[Any]) -> Booking:
    return Booking(
        id=row.id,
        client_id=row.client_id,
        product_code=row.product_code,
        product_name=row.product_name,
        amount=Money(row.principal_minor, Currency(row.currency.strip())),
        at_maturity=AtMaturity(row.at_maturity) if row.at_maturity else None,
        reason=row.reason,
        status=row.status,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        investment_id=row.investment_id,
        failure_code=row.failure_code,
        finished_at=row.finished_at,
    )


async def _audit(
    session: AsyncSession,
    actor: AuditActor,
    category: AuditCategory,
    step: str,
    booking: Booking,
    clock: Clock,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"investments.booking.{step}",
            resource_type=RESOURCE,
            resource_id=booking.id,
            actor=actor,
            reason=reason if reason is not None else booking.reason,
            details={
                "client_id": booking.client_id,
                "product": booking.product_code,
                "investment_id": booking.investment_id,
                "failure_code": booking.failure_code,
            },
        ),
        clock,
    )
