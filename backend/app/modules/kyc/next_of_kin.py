"""A client's next of kin: who Finvest contacts if it cannot reach them, and their shares.

Every function works inside the caller's transaction. Names, mobile numbers and ID numbers are
sealed with the identity data key, each bound to its row and column. A save replaces the whole
list, and migration 0230 checks at commit that the shares add up to 100%.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.sealing import Sealer, associated_data
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.kyc.domain.next_of_kin import Person, Relationship, clean, problems
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

TABLE = "kyc_next_of_kin"


@dataclass(frozen=True, slots=True)
class SavedPerson:
    id: str
    full_name: str
    relationship: Relationship
    phone: str
    id_number: str
    share_bps: int


@dataclass(frozen=True, slots=True)
class NextOfKin:
    people: list[SavedPerson]
    # None until the client first saves a list.
    updated_at: datetime | None


_PEOPLE = text(
    "SELECT id, relationship, share_bps, full_name_sealed, phone_sealed, id_number_sealed "
    "FROM kyc_next_of_kin WHERE client_id = :client_id ORDER BY position"
)
_LIST = text("SELECT updated_at FROM kyc_next_of_kin_lists WHERE client_id = :client_id")
# Takes the list's row lock, so two saves for one client run one after the other.
_CLAIM = text(
    "INSERT INTO kyc_next_of_kin_lists (client_id, people, updated_at) "
    "VALUES (:client_id, :people, :now) ON CONFLICT (client_id) "
    "DO UPDATE SET people = EXCLUDED.people, updated_at = EXCLUDED.updated_at"
)
_CLEAR = text("DELETE FROM kyc_next_of_kin WHERE client_id = :client_id")
_ADD = text(
    "INSERT INTO kyc_next_of_kin (id, client_id, position, relationship, share_bps, "
    "full_name_sealed, phone_sealed, id_number_sealed, created_at) VALUES (:id, :client_id, "
    ":position, :relationship, :share_bps, :full_name, :phone, :id_number, :now)"
)


async def next_of_kin(session: AsyncSession, sealer: Sealer, client_id: str) -> NextOfKin:
    """The client's list, in the order they gave it. Staff may read it too."""
    updated_at = await session.scalar(_LIST, {"client_id": client_id})
    rows = (await session.execute(_PEOPLE, {"client_id": client_id})).all()
    return NextOfKin(people=[_person(sealer, row) for row in rows], updated_at=updated_at)


async def save(
    session: AsyncSession,
    sealer: Sealer,
    client: Actor,
    people: list[Person],
    clock: Clock,
    *,
    client_phone: str | None = None,
) -> NextOfKin:
    """Replace the signed-in client's list. An empty list removes everyone."""
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    cleaned = [clean(person) for person in people]
    found = problems(cleaned, client_phone)
    if found:
        raise ApplicationError(
            "validation_failed",
            fields=[
                FieldProblem(field_path=_path(problem.index, problem.field), code=problem.code)
                for problem in found
            ],
        )
    now = clock.now()
    await session.execute(_CLAIM, {"client_id": client.id, "people": len(cleaned), "now": now})
    await session.execute(_CLEAR, {"client_id": client.id})
    for position, person in enumerate(cleaned):
        person_id = push_id(clock)
        await session.execute(
            _ADD,
            {
                "id": person_id,
                "client_id": client.id,
                "position": position,
                "relationship": person.relationship.value,
                "share_bps": person.share_bps,
                "full_name": _seal(sealer, person_id, "full_name", person.full_name),
                "phone": _seal(sealer, person_id, "phone", person.phone),
                "id_number": _seal(sealer, person_id, "id_number", person.id_number),
                "now": now,
            },
        )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="kyc.next_of_kin.saved",
            resource_type="next_of_kin",
            resource_id=client.id,
            actor=AuditActor.client(client.id),
            details={"people": len(cleaned)},
        ),
        clock,
    )
    return await next_of_kin(session, sealer, client.id)


def _path(index: int | None, field: str) -> list[str | int]:
    path: list[str | int] = ["body", "people"]
    return [*path, index, field] if index is not None else path


def _seal(sealer: Sealer, person_id: str, column: str, value: str) -> bytes:
    return sealer.seal(value, associated_data(TABLE, f"{column}_sealed", person_id))


def _open(sealer: Sealer, person_id: str, column: str, sealed: bytes) -> str:
    return sealer.open(sealed, associated_data(TABLE, f"{column}_sealed", person_id))


def _person(sealer: Sealer, row: Row[Any]) -> SavedPerson:
    return SavedPerson(
        id=row.id,
        full_name=_open(sealer, row.id, "full_name", row.full_name_sealed),
        relationship=Relationship(row.relationship),
        phone=_open(sealer, row.id, "phone", row.phone_sealed),
        id_number=_open(sealer, row.id, "id_number", row.id_number_sealed),
        share_bps=row.share_bps,
    )
