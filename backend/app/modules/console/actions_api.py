"""Routes for staff notes and for acting for a client from their page.

Reading notes and actions is `clients.view`. Writing a note is `client_notes.write`, by one
person. Proposing or deciding an action needs the permission its kind names (see
`actions.PERMISSIONS`), and deciding also needs a passkey or app code from the last few minutes.
Every action takes two people: one proposes, a different one approves.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.console import actions, notes
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}
router = APIRouter(prefix="/staff/clients", tags=["staff clients"], responses=_REFUSALS)

ClientViewer = Annotated[StaffSession, Depends(require_permission("clients.view"))]
NoteWriter = Annotated[StaffSession, Depends(require_permission("client_notes.write"))]
# Deciding needs a passkey or app code from the last few minutes. The kind's own permission is
# checked once the action is read.
ActionDecider = Annotated[StaffSession, Depends(require_confirmed("clients.view"))]
Reason = Annotated[str, Field(min_length=1, max_length=actions.MAX_REASON)]
ActionKind = actions.Kind
NoteBody = Annotated[str, Field(min_length=1, max_length=notes.MAX_BODY)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class NoteView(Contract):
    id: PushId
    client_id: PushId
    body: str
    written_by: PushId
    written_by_name: str
    written_at: datetime


class NotesView(Contract):
    notes: list[NoteView]
    # The reader may write a note.
    can_write: bool


class WriteNote(Contract):
    body: NoteBody


class ActionView(Contract):
    id: PushId
    client_id: PushId
    kind: actions.Kind
    reason: str
    proposed_by: PushId
    proposed_by_name: str
    proposed_at: datetime
    status: actions.Status
    decided_by: PushId | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    # The proposer can withdraw it. Anyone else who holds the kind's permission decides it.
    yours: bool


class ActionsView(Contract):
    actions: list[ActionView]
    # The kinds the reader may propose or decide.
    allowed: list[ActionKind]


class ProposeAction(Contract):
    kind: actions.Kind
    reason: Reason


class DecideAction(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


@router.get("/{client_id}/notes")
async def client_notes(client_id: PushId, staff: ClientViewer, database: Db) -> NotesView:
    """The client's staff notes, newest first."""
    async with database.actor_transaction(staff.actor) as session:
        found = await notes.notes_for_client(session, client_id)
    return NotesView(
        notes=[_note(item) for item in found],
        can_write="client_notes.write" in staff.permissions,
    )


@router.post("/{client_id}/notes", status_code=201)
async def write_note(
    client_id: PushId, body: WriteNote, staff: NoteWriter, database: Db, clock: Time
) -> NoteView:
    """Keep a note on the client for other staff. It is never changed or deleted."""
    async with database.actor_transaction(staff.actor) as session:
        note = await notes.write(session, staff.actor, client_id, body.body, clock)
    return _note(note)


@router.get("/{client_id}/actions")
async def client_actions(client_id: PushId, staff: ClientViewer, database: Db) -> ActionsView:
    """Every action proposed for the client, newest first, and what the reader may do."""
    async with database.actor_transaction(staff.actor) as session:
        found = await actions.actions_for_client(session, client_id)
    return ActionsView(actions=[_action(item, staff) for item in found], allowed=_allowed(staff))


@router.post("/{client_id}/actions", status_code=201)
async def propose_action(
    client_id: PushId, body: ProposeAction, staff: ClientViewer, database: Db, clock: Time
) -> ActionView:
    """Ask to act for the client. A different member of staff decides."""
    if body.kind not in _allowed(staff):
        raise ApplicationError("forbidden")
    async with database.actor_transaction(staff.actor) as session:
        action = await actions.propose(
            session, staff.actor, client_id, body.kind, body.reason, clock, _details(body)
        )
    return _action(action, staff)


@router.post("/actions/{action_id}/decision")
async def decide_action(
    action_id: PushId, body: DecideAction, staff: ActionDecider, database: Db, clock: Time
) -> ActionView:
    """Approve or reject someone else's proposal, or withdraw your own."""
    async with database.actor_transaction(staff.actor) as session:
        action = await actions.decide(
            session,
            staff.actor,
            action_id,
            body.decision,
            body.reason,
            clock,
            permissions=staff.permissions,
        )
    return _action(action, staff)


def _details(body: ProposeAction) -> dict[str, Any]:
    return {}


def _allowed(staff: StaffSession) -> list[actions.Kind]:
    return [kind for kind in actions.KINDS if actions.PERMISSIONS[kind] in staff.permissions]


def _note(note: notes.Note) -> NoteView:
    return NoteView(
        id=note.id,
        client_id=note.client_id,
        body=note.body,
        written_by=note.written_by,
        written_by_name=note.written_by_name,
        written_at=note.written_at,
    )


def _action(action: actions.ClientAction, staff: StaffSession) -> ActionView:
    return ActionView(
        id=action.id,
        client_id=action.client_id,
        kind=action.kind,
        reason=action.reason,
        proposed_by=action.proposed_by,
        proposed_by_name=action.proposed_by_name,
        proposed_at=action.proposed_at,
        status=action.status,
        decided_by=action.decided_by,
        decided_by_name=action.decided_by_name,
        decided_at=action.decided_at,
        decision_reason=action.decision_reason,
        yours=action.proposed_by == staff.staff_id,
    )
