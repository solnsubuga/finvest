"""Looking up the name a mobile network holds for a payout number. Rules in
`domain/name_lookups.py`; the worker's side in `name_lookup_jobs.py`.

A lookup is asked for in a client transaction: the one that spends the code texted to the
number, or a later one asking again. The row and its job are saved together, so a lookup is
never asked for twice by accident and never lost. Only the worker records what ioTec said.

Nothing here logs or audits the number or the name the network gave.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue, is_active
from app.modules.kyc import service as kyc
from app.modules.payouts import destinations
from app.modules.payouts.destinations import DestinationView
from app.modules.payouts.domain.destinations import DestinationKind, DestinationStatus
from app.modules.payouts.domain.name_lookups import (
    READ_WITHIN,
    Found,
    LookupStatus,
    NameCheck,
    NoName,
    ToStaff,
    decide,
    name_check,
    why_not_again,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

SEND_KIND = "payouts.name_lookup.send"
READ_KIND = "payouts.name_lookup.read"
# Decide a name already found, once the client's ID is approved. Calls no provider.
DECIDE_KIND = "payouts.name_lookup.decide"
# A send that certainly never arrived is tried again by the job, this many times in all.
SEND_ATTEMPTS = 5
READ_ATTEMPTS = 12


@dataclass(frozen=True, slots=True)
class Lookup:
    id: str
    destination_id: str
    number: int
    status: LookupStatus
    request_id: str | None
    registered_name: str | None
    created_at: datetime


_COLUMNS = "id, destination_id, number, status, request_id, registered_name, created_at"
_INSERT = text(
    "INSERT INTO payout_name_lookups (id, destination_id, number, status, created_at, updated_at) "
    "VALUES (:id, :destination_id, :number, 'queued', :now, :now)"
)
_GET = text(f"SELECT {_COLUMNS} FROM payout_name_lookups WHERE id = :id")  # noqa: S608
_LATEST = text(
    f"SELECT {_COLUMNS} FROM payout_name_lookups "  # noqa: S608 - constant column list
    "WHERE destination_id = :destination_id ORDER BY number DESC LIMIT 1"
)
_SENT = text(
    "UPDATE payout_name_lookups SET status = 'sent', request_id = CAST(:request_id AS uuid), "
    "updated_at = :now WHERE id = :id AND status = 'queued'"
)
_ANSWERED = text(
    "UPDATE payout_name_lookups SET status = :status, registered_name = :registered_name, "
    "error_code = :error_code, answered_at = :now, updated_at = :now "
    "WHERE id = :id AND status IN ('queued', 'sent')"
)


async def ask(
    session: AsyncSession, client: Actor, destination: DestinationView, clock: Clock
) -> Lookup:
    """Ask for a lookup of a client's number waiting for its name. Client transactions only.

    The caller holds the destination's row lock, so two asks for one number wait on each other.
    """
    latest = await latest_lookup(session, destination.id)
    if latest is not None:
        refused = why_not_again(latest.status, latest.created_at, latest.number, clock.now())
        if refused is not None:
            raise _refused(refused.value)
    lookup_id = push_id(clock)
    number = 1 if latest is None else latest.number + 1
    now = clock.now()
    await session.execute(
        _INSERT,
        {"id": lookup_id, "destination_id": destination.id, "number": number, "now": now},
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
    del client  # the row policy checks the number is theirs
    return await get_lookup(session, lookup_id)


async def check_again(
    session: AsyncSession, client: Actor, destination_id: str, clock: Clock
) -> DestinationView:
    """The client asks for their number to be looked up again, after a lookup with no answer.

    If the last lookup found a name while the client's ID was not yet approved, nothing is sent
    to ioTec: the worker decides the same answer again. Either way the number moves on in the
    worker, never in this transaction.
    """
    view = await destinations.locked(session, destination_id)
    if view.kind is not DestinationKind.MOBILE_MONEY:
        raise _refused("name_check_not_for_this_account")
    if view.status is not DestinationStatus.AWAITING_PROOF:
        raise _refused("destination_not_awaiting_proof")
    latest = await latest_lookup(session, destination_id)
    if latest is not None and latest.status is LookupStatus.FOUND:
        if await kyc.verified_name(session, view.client_id) is None:
            raise _refused("client_not_verified")
        await enqueue(
            session,
            JobRequest(
                kind=DECIDE_KIND,
                idempotency_key=latest.id,
                payload={"lookup_id": latest.id},
            ),
            now=clock.now(),
        )
    else:
        await ask(session, client, view, clock)
    return await destinations.get_destination(session, destination_id)


async def name_check_for(
    session: AsyncSession, view: DestinationView, clock: Clock, *, asking: bool = True
) -> NameCheck | None:
    """For a mobile money number waiting for its name, where its lookup stands. Else None.

    `asking` is False while no provider reads names: the number waits, shown as `checking`, and
    nothing is asked, so none of its lookups are used up.
    """
    if view.kind is not DestinationKind.MOBILE_MONEY or (
        view.status is not DestinationStatus.AWAITING_PROOF
    ):
        return None
    if not asking:
        return NameCheck.CHECKING
    latest = await latest_lookup(session, view.id)
    if latest is None:
        return name_check(None, None, 0, clock.now())
    deciding = latest.status is LookupStatus.FOUND and await is_active(
        session, kind=DECIDE_KIND, idempotency_key=latest.id
    )
    return name_check(
        latest.status, latest.created_at, latest.number, clock.now(), deciding=deciding
    )


async def latest_lookup(session: AsyncSession, destination_id: str) -> Lookup | None:
    row = (await session.execute(_LATEST, {"destination_id": destination_id})).one_or_none()
    return None if row is None else _lookup(row)


async def get_lookup(session: AsyncSession, lookup_id: str) -> Lookup:
    row = (await session.execute(_GET, {"id": lookup_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _lookup(row)


# The worker's steps. -----------------------------------------------------------------------


async def number_to_look_up(session: AsyncSession, lookup_id: str) -> str | None:
    """The full number for a queued lookup, or None if there is nothing to send any more."""
    lookup = await get_lookup(session, lookup_id)
    if lookup.status is not LookupStatus.QUEUED:
        return None
    view = await destinations.get_destination(session, lookup.destination_id)
    if view.status is not DestinationStatus.AWAITING_PROOF:
        return None
    _, msisdn = await destinations.mobile_money_number(session, lookup.destination_id)
    return msisdn


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
    """The lookup ended with no answer. The number waits, and the client may ask again."""
    await session.execute(
        _ANSWERED,
        {
            "id": lookup_id,
            "status": LookupStatus.UNANSWERED,
            "registered_name": None,
            "error_code": code,
            "now": clock.now(),
        },
    )


async def record_answer(
    session: AsyncSession, actor: Actor, lookup_id: str, answer: Found | NoName, clock: Clock
) -> str:
    """Save what the network said and move the number on. Returns what happened, as a code.

    The worker's step. A name found while the client's ID is not approved yet is kept, and the
    number waits: it is decided when the client asks again (`check_again`).
    """
    name = answer.registered_name.strip()[:120] if isinstance(answer, Found) else None
    if isinstance(answer, Found) and not name:
        answer = NoName()
    found = isinstance(answer, Found)
    written = await session.execute(
        _ANSWERED,
        {
            "id": lookup_id,
            "status": LookupStatus.FOUND if found else LookupStatus.NO_NAME,
            "registered_name": name if found else None,
            "error_code": None,
            "now": clock.now(),
        },
    )
    if written.rowcount != 1:  # type: ignore[attr-defined]
        return "moved_on"
    return await decide_lookup(session, actor, lookup_id, clock)


async def decide_lookup(session: AsyncSession, actor: Actor, lookup_id: str, clock: Clock) -> str:
    """Move a number on from its lookup's answer. The worker's step. Returns what happened."""
    lookup = await get_lookup(session, lookup_id)
    if lookup.status not in (LookupStatus.FOUND, LookupStatus.NO_NAME):
        return "moved_on"
    view = await destinations.locked(session, lookup.destination_id)
    latest = await latest_lookup(session, lookup.destination_id)
    if view.status is not DestinationStatus.AWAITING_PROOF or (
        latest is None or latest.id != lookup.id
    ):
        return "moved_on"
    answer: Found | NoName = (
        NoName() if lookup.registered_name is None else Found(lookup.registered_name)
    )
    id_name = await kyc.verified_name(session, view.client_id)
    if id_name is None and isinstance(answer, Found):
        return "waiting_for_id"
    decision = decide(id_name or "", answer)
    if isinstance(decision, ToStaff):
        decided = await destinations.network_name_matches(
            session, actor, view.id, lookup.id, lookup.registered_name or "", decision.match, clock
        )
    else:
        decided = await destinations.refuse_by_network(
            session, actor, view.id, lookup.id, lookup.registered_name, decision.reason, clock
        )
    return decided.status.value


def _refused(code: str) -> ApplicationError:
    return ApplicationError("conflict", fields=[FieldProblem(field_path=["body"], code=code)])


def _lookup(row: Row[Any]) -> Lookup:
    return Lookup(
        id=row.id,
        destination_id=row.destination_id,
        number=int(row.number),
        status=LookupStatus(row.status),
        request_id=None if row.request_id is None else str(row.request_id),
        registered_name=row.registered_name,
        created_at=row.created_at,
    )
