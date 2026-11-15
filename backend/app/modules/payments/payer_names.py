"""The name the network holds for a number that paid a client's mobile money payment.

The owner accepts money from any payer and keeps the payer's number and name on record for
screening and audit (2026-11-15). Neither Pesapal nor ioTec says who paid by name, so when a
mobile money payment is confirmed from a number other than the client's own, a lookup is asked
for in the same transaction, and the worker asks ioTec Verify for the name, as for a payout
number (`payouts/domain/name_lookups.py`). The worker's side is in `payer_name_jobs.py`.

Nothing here changes the payment: it has already counted. A lookup is asked for once, and its
answer is final. Nothing here logs or audits the number or the name.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.identity.service import client_contact
from app.modules.payouts.domain.name_lookups import READ_WITHIN, Found, LookupStatus, NoName
from app.shared.clock import Clock

SEND_KIND = "payments.payer_name.send"
READ_KIND = "payments.payer_name.read"
# A send that certainly never arrived is tried again by the job, this many times in all.
SEND_ATTEMPTS = 5
READ_ATTEMPTS = 12


@dataclass(frozen=True, slots=True)
class PayerName:
    deposit_id: str
    client_id: str
    phone: str
    status: LookupStatus
    request_id: str | None
    registered_name: str | None
    created_at: datetime
    answered_at: datetime | None


_COLUMNS = (
    "deposit_id, client_id, phone, status, request_id, registered_name, created_at, answered_at"
)
_GET = text(f"SELECT {_COLUMNS} FROM payment_payer_names WHERE deposit_id = :id")  # noqa: S608
_INSERT = text(
    "INSERT INTO payment_payer_names (deposit_id, client_id, phone, status, created_at, "
    "updated_at) VALUES (:id, :client_id, :phone, 'queued', :now, :now) "
    "ON CONFLICT (deposit_id) DO NOTHING"
)
_SENT = text(
    "UPDATE payment_payer_names SET status = 'sent', request_id = CAST(:request_id AS uuid), "
    "updated_at = :now WHERE deposit_id = :id AND status = 'queued'"
)
_ANSWERED = text(
    "UPDATE payment_payer_names SET status = :status, registered_name = :registered_name, "
    "error_code = :error_code, answered_at = :now, updated_at = :now "
    "WHERE deposit_id = :id AND status IN ('queued', 'sent')"
)


async def ask_if_another(
    session: AsyncSession, deposit_id: str, client_id: str, paid_from: str | None, clock: Clock
) -> bool:
    """Ask for the name behind `paid_from` when it is not the client's own number.

    The worker's transaction that confirms a mobile money payment. Nothing is asked when the
    provider named no number, or the number is the one on the client's account, whose name the
    identity check already looked up. True if a lookup was asked for.
    """
    if paid_from is None:
        return False
    contact = await client_contact(session, client_id)
    if contact is not None and contact.phone == paid_from:
        return False
    now = clock.now()
    saved = await session.execute(
        _INSERT, {"id": deposit_id, "client_id": client_id, "phone": paid_from, "now": now}
    )
    if saved.rowcount != 1:  # type: ignore[attr-defined]
        return False
    await enqueue(
        session,
        JobRequest(
            kind=SEND_KIND,
            idempotency_key=deposit_id,
            payload={"deposit_id": deposit_id},
            max_attempts=SEND_ATTEMPTS,
        ),
        now=now,
    )
    return True


async def payer_name(session: AsyncSession, deposit_id: str) -> PayerName | None:
    """The lookup for a payment, if one was asked for and this transaction may see it."""
    row = (await session.execute(_GET, {"id": deposit_id})).one_or_none()
    return None if row is None else _payer_name(row)


# The worker's steps. -----------------------------------------------------------------------


async def number_to_look_up(session: AsyncSession, deposit_id: str) -> str | None:
    """The number for a queued lookup as ioTec takes it (`256...`), or None if it moved on."""
    found = await payer_name(session, deposit_id)
    if found is None or found.status is not LookupStatus.QUEUED:
        return None
    return found.phone.removeprefix("+")


async def note_sent(session: AsyncSession, deposit_id: str, request_id: str, clock: Clock) -> bool:
    """ioTec accepted the lookup. Queues the read. False if the lookup had moved on."""
    noted = await session.execute(
        _SENT, {"id": deposit_id, "request_id": request_id, "now": clock.now()}
    )
    if noted.rowcount != 1:  # type: ignore[attr-defined]
        return False
    await enqueue(
        session,
        JobRequest(
            kind=READ_KIND,
            idempotency_key=deposit_id,
            payload={"deposit_id": deposit_id},
            max_attempts=READ_ATTEMPTS,
            max_age=READ_WITHIN,
        ),
        now=clock.now(),
    )
    return True


async def note_unanswered(session: AsyncSession, deposit_id: str, code: str, clock: Clock) -> None:
    """The lookup ended with no answer. It is not asked again: the payment has counted."""
    await _answer(session, deposit_id, LookupStatus.UNANSWERED, None, code, clock)


async def record_answer(
    session: AsyncSession, deposit_id: str, answer: Found | NoName, clock: Clock
) -> str:
    """Save what the network said. Returns the outcome, for the job's result."""
    name = answer.registered_name.strip()[:120] if isinstance(answer, Found) else ""
    status = LookupStatus.FOUND if name else LookupStatus.NO_NAME
    written = await _answer(session, deposit_id, status, name or None, None, clock)
    return status.value if written else "moved_on"


async def _answer(
    session: AsyncSession,
    deposit_id: str,
    status: LookupStatus,
    name: str | None,
    code: str | None,
    clock: Clock,
) -> bool:
    written = await session.execute(
        _ANSWERED,
        {
            "id": deposit_id,
            "status": status.value,
            "registered_name": name,
            "error_code": code,
            "now": clock.now(),
        },
    )
    return bool(written.rowcount == 1)  # type: ignore[attr-defined]


def _payer_name(row: Row[Any]) -> PayerName:
    return PayerName(
        deposit_id=row.deposit_id,
        client_id=row.client_id,
        phone=row.phone,
        status=LookupStatus(row.status),
        request_id=None if row.request_id is None else str(row.request_id),
        registered_name=row.registered_name,
        created_at=row.created_at,
        answered_at=row.answered_at,
    )
