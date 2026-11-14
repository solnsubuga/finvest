"""Staff notes on a client: what one member of staff wrote for the others.

A note is written by one person under their own name and kept as written. Nothing edits or
deletes one: a mistake is put right by a later note. Only staff read notes, and migration 0350
enforces both in PostgreSQL. A note moves nothing and changes nothing about the client, so it
is not a two-person step.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity.client_locks import client_status
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "client_note"
MAX_BODY = 1000
# A client page shows the newest notes. Older ones are kept, never shown here.
SHOWN = 100


@dataclass(frozen=True, slots=True)
class Note:
    id: str
    client_id: str
    body: str
    written_by: str
    written_by_name: str
    written_at: datetime


_NOTES = text(
    "SELECT n.id, n.client_id, n.body, n.written_by, s.name AS written_by_name, n.written_at "
    "FROM console_client_notes n JOIN identity_staff s ON s.id = n.written_by "
    "WHERE n.client_id = :client_id ORDER BY n.written_at DESC, n.id DESC LIMIT :limit"
)
_NOTE = text(
    "SELECT n.id, n.client_id, n.body, n.written_by, s.name AS written_by_name, n.written_at "
    "FROM console_client_notes n JOIN identity_staff s ON s.id = n.written_by WHERE n.id = :id"
)


async def notes_for_client(session: AsyncSession, client_id: str) -> list[Note]:
    """The client's newest notes first."""
    rows = await session.execute(_NOTES, {"client_id": client_id, "limit": SHOWN})
    return [_note(row) for row in rows]


async def write(
    session: AsyncSession, staff: Actor, client_id: str, body: str, clock: Clock
) -> Note:
    """Keep a note on the client, under the acting member of staff's name."""
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    cleaned = body.strip()
    if not cleaned or len(cleaned) > MAX_BODY:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["body", "body"], code="required")]
        )
    if await client_status(session, client_id) is None:
        raise ApplicationError("not_found")
    note_id = push_id(clock)
    await session.execute(
        text(
            "INSERT INTO console_client_notes (id, client_id, body, written_by, written_at) "
            "VALUES (:id, :client_id, :body, :staff, :now)"
        ),
        {
            "id": note_id,
            "client_id": client_id,
            "body": cleaned,
            "staff": staff.id,
            "now": clock.now(),
        },
    )
    # The audit trail records that a note was written, never what it says.
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="console.client.note_written",
            resource_type=RESOURCE,
            resource_id=note_id,
            actor=AuditActor.staff(staff.id),
            details={"client_id": client_id},
        ),
        clock,
    )
    row = (await session.execute(_NOTE, {"id": note_id})).one()
    return _note(row)


def _note(row: Row[Any]) -> Note:
    return Note(
        id=row.id,
        client_id=row.client_id,
        body=row.body,
        written_by=row.written_by,
        written_by_name=row.written_by_name,
        written_at=row.written_at,
    )
