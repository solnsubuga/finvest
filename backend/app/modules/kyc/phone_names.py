"""Looking up the name the network holds for a client's phone number, for their identity check.

Rules in `domain/phone_names.py`; the worker's side in `phone_name_jobs.py`. The lookup itself
is the payouts module's ioTec Verify adapter (`RegisteredNamesProvider`).

A lookup is asked for in the transaction that hands the check in, with its job, so a check is
never handed in and its lookup lost. Only the worker records what ioTec said.

Nothing here logs or audits the number or the name the network gave.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.identity.service import client_contact
from app.modules.kyc.domain.phone_names import PhoneName, ask_again, msisdn, phone_name
from app.modules.payouts.domain.destinations import NameMatch, compare_names
from app.modules.payouts.domain.name_lookups import READ_WITHIN, Found, LookupStatus, NoName
from app.shared.clock import Clock
from app.shared.push_ids import push_id

SEND_KIND = "kyc.phone_name.send"
READ_KIND = "kyc.phone_name.read"
# A send that certainly never arrived is tried again by the job, this many times in all.
SEND_ATTEMPTS = 5
READ_ATTEMPTS = 12


@dataclass(frozen=True, slots=True)
class Lookup:
    id: str
    check_id: str
    number: int
    status: LookupStatus
    request_id: str | None
    registered_name: str | None
    name_match: NameMatch | None
    created_at: datetime
    answered_at: datetime | None


@dataclass(frozen=True, slots=True)
class PhoneNameView:
    """What staff see: where the lookup stands, and the network's name once it gave one."""

    status: PhoneName
    registered_name: str | None
    answered_at: datetime | None


_COLUMNS = (
    "id, check_id, number, status, request_id, registered_name, name_match, created_at, answered_at"
)
_INSERT = text(
    "INSERT INTO kyc_phone_names (id, check_id, number, status, created_at, updated_at) "
    "VALUES (:id, :check_id, :number, 'queued', :now, :now)"
)
_GET = text(f"SELECT {_COLUMNS} FROM kyc_phone_names WHERE id = :id")  # noqa: S608
_LATEST = text(
    f"SELECT {_COLUMNS} FROM kyc_phone_names "  # noqa: S608 - constant column list
    "WHERE check_id = :check_id ORDER BY number DESC LIMIT 1"
)
_CHECK = text("SELECT client_id, surname, given_names FROM kyc_checks WHERE id = :id")
_SENT = text(
    "UPDATE kyc_phone_names SET status = 'sent', request_id = CAST(:request_id AS uuid), "
    "updated_at = :now WHERE id = :id AND status = 'queued'"
)
_ANSWERED = text(
    "UPDATE kyc_phone_names SET status = :status, registered_name = :registered_name, "
    "name_match = :name_match, error_code = :error_code, answered_at = :now, updated_at = :now "
    "WHERE id = :id AND status IN ('queued', 'sent')"
)


async def ask_at_hand_in(
    session: AsyncSession, check_id: str, client_id: str, clock: Clock
) -> Lookup | None:
    """Ask for a lookup of the client's phone as their check is handed in, when one is due.

    The client's transaction, which holds the check's row lock. Nothing is asked for a number
    that is not Ugandan, or once the network has answered for this check.
    """
    contact = await client_contact(session, client_id)
    if contact is None or msisdn(contact.phone) is None:
        return None
    latest = await latest_lookup(session, check_id)
    now = clock.now()
    if latest is not None and not ask_again(latest.status, latest.created_at, latest.number, now):
        return None
    lookup_id = push_id(clock)
    await session.execute(
        _INSERT,
        {
            "id": lookup_id,
            "check_id": check_id,
            "number": 1 if latest is None else latest.number + 1,
            "now": now,
        },
    )
    await enqueue(
        session,
        JobRequest(
            kind=SEND_KIND,
            idempotency_key=lookup_id,
            payload={"lookup_id": lookup_id},
            max_attempts=SEND_ATTEMPTS,
        ),
        now=now,
    )
    return await get_lookup(session, lookup_id)


async def phone_name_for_check(session: AsyncSession, check_id: str) -> PhoneNameView | None:
    """The latest lookup of the client's phone for this check, or None if none was asked."""
    latest = await latest_lookup(session, check_id)
    if latest is None:
        return None
    return PhoneNameView(
        status=phone_name(latest.status, latest.name_match),
        registered_name=latest.registered_name,
        answered_at=latest.answered_at,
    )


async def latest_lookup(session: AsyncSession, check_id: str) -> Lookup | None:
    row = (await session.execute(_LATEST, {"check_id": check_id})).one_or_none()
    return None if row is None else _lookup(row)


async def get_lookup(session: AsyncSession, lookup_id: str) -> Lookup | None:
    row = (await session.execute(_GET, {"id": lookup_id})).one_or_none()
    return None if row is None else _lookup(row)


# The worker's steps. -----------------------------------------------------------------------


async def number_to_look_up(session: AsyncSession, lookup_id: str) -> str | None:
    """The number for a queued lookup, or None if there is nothing to send any more."""
    lookup = await get_lookup(session, lookup_id)
    if lookup is None or lookup.status is not LookupStatus.QUEUED:
        return None
    check = (await session.execute(_CHECK, {"id": lookup.check_id})).one()
    contact = await client_contact(session, check.client_id)
    return None if contact is None else msisdn(contact.phone)


async def note_sent(session: AsyncSession, lookup_id: str, request_id: str, clock: Clock) -> bool:
    """ioTec accepted the lookup. Queues the read. False if the lookup had moved on."""
    noted = await session.execute(
        _SENT, {"id": lookup_id, "request_id": request_id, "now": clock.now()}
    )
    if noted.rowcount != 1:  # type: ignore[attr-defined]
        return False
    await enqueue(
        session,
        JobRequest(
            kind=READ_KIND,
            idempotency_key=lookup_id,
            payload={"lookup_id": lookup_id},
            max_attempts=READ_ATTEMPTS,
            max_age=READ_WITHIN,
        ),
        now=clock.now(),
    )
    return True


async def note_unanswered(session: AsyncSession, lookup_id: str, code: str, clock: Clock) -> None:
    """The lookup ended with no answer. It is asked again at the next hand-in."""
    await _answer(session, lookup_id, LookupStatus.UNANSWERED, None, None, code, clock)


async def record_answer(
    session: AsyncSession, lookup_id: str, answer: Found | NoName, clock: Clock
) -> str:
    """Save what the network said, compared with the name on the check. Returns the outcome."""
    name = answer.registered_name.strip()[:120] if isinstance(answer, Found) else ""
    lookup = await get_lookup(session, lookup_id)
    if lookup is None:
        return "moved_on"
    if not name:
        written = await _answer(session, lookup_id, LookupStatus.NO_NAME, None, None, None, clock)
        return PhoneName.NO_NAME.value if written else "moved_on"
    check = (await session.execute(_CHECK, {"id": lookup.check_id})).one()
    match = compare_names(f"{check.surname or ''} {check.given_names or ''}", name)
    written = await _answer(session, lookup_id, LookupStatus.FOUND, name, match, None, clock)
    return phone_name(LookupStatus.FOUND, match).value if written else "moved_on"


async def _answer(
    session: AsyncSession,
    lookup_id: str,
    status: LookupStatus,
    name: str | None,
    match: NameMatch | None,
    code: str | None,
    clock: Clock,
) -> bool:
    written = await session.execute(
        _ANSWERED,
        {
            "id": lookup_id,
            "status": status.value,
            "registered_name": name,
            "name_match": None if match is None else match.value,
            "error_code": code,
            "now": clock.now(),
        },
    )
    return bool(written.rowcount == 1)  # type: ignore[attr-defined]


def _lookup(row: Row[Any]) -> Lookup:
    return Lookup(
        id=row.id,
        check_id=row.check_id,
        number=int(row.number),
        status=LookupStatus(row.status),
        request_id=None if row.request_id is None else str(row.request_id),
        registered_name=row.registered_name,
        name_match=None if row.name_match is None else NameMatch(row.name_match),
        created_at=row.created_at,
        answered_at=row.answered_at,
    )
