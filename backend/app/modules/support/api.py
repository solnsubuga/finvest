"""Routes for help chat and complaints, for the client app and the console."""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.support import complaints, conversations
from app.modules.support.domain.chat import MAX_MESSAGE, MAX_SAVED_TITLE, AuthorKind, Topic
from app.modules.support.domain.complaints import (
    MAX_DETAILS,
    MAX_NOTE,
    MAX_RESOLUTION,
    MAX_SUMMARY,
    Category,
    Closure,
    InvalidComplaint,
    NewComplaint,
    Outcome,
    ReceivedVia,
    Status,
)
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

client_router = APIRouter(prefix="/client", tags=["client help"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff", tags=["staff support"], responses=_REFUSALS)
routers = [client_router, staff_router]

SupportStaff = Annotated[StaffSession, Depends(require_permission("support.view"))]
ComplaintsStaff = Annotated[StaffSession, Depends(require_permission("complaints.view"))]
# Looking is the `.view` permissions. Every route that writes needs its own.
SupportReplier = Annotated[StaffSession, Depends(require_permission("support.reply"))]
ComplaintsManager = Annotated[StaffSession, Depends(require_permission("complaints.manage"))]
# Deciding needs a passkey or app code from the last few minutes.
OutcomeDecider = Annotated[StaffSession, Depends(require_confirmed("complaints.manage"))]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
After = Annotated[PushId | None, Query()]
Limit = Annotated[int, Query(ge=1, le=conversations.MAX_PAGE_SIZE)]
MessageKey = Annotated[str, Field(min_length=8, max_length=64)]
MessageBody = Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE * 2)]


# Help chat ----------------------------------------------------------------------------------------


class MessageItem(Contract):
    id: PushId
    conversation_id: PushId
    # "client" or "staff". The app draws its own messages on the right.
    author: AuthorKind
    # A member of staff's first name, such as "Grace".
    author_name: str | None
    body: str
    created_at: datetime


class ConversationItem(Contract):
    id: PushId
    topic: Topic
    status: Literal["open", "solved"]
    created_at: datetime
    last_message_at: datetime
    waiting_since: datetime | None


class ClientChat(Contract):
    # The open chat, or the last one. None before the client's first message.
    conversation: ConversationItem | None
    messages: list[MessageItem]
    # Replies the client has not opened Help to read yet.
    unread: int


class SendMessage(Contract):
    body: MessageBody
    # Made up by the app for each message, and sent again unchanged on a retry.
    key: MessageKey
    topic: Topic = "other"


class StaffSendMessage(Contract):
    body: MessageBody
    key: MessageKey


class StaffWriteFirst(Contract):
    body: MessageBody
    key: MessageKey
    topic: Topic = "other"


class InboxRow(Contract):
    conversation: ConversationItem
    client_id: PushId
    last_message: str
    last_author: AuthorKind
    unread: int


class Inbox(Contract):
    conversations: list[InboxRow]


class StaffChat(Contract):
    conversation: ConversationItem
    client_id: PushId
    messages: list[MessageItem]


class SetTopic(Contract):
    topic: Topic


class SavedReplyItem(Contract):
    id: PushId
    title: str
    body: str


class SavedReplies(Contract):
    replies: list[SavedReplyItem]


class NewSavedReply(Contract):
    title: Annotated[str, Field(min_length=1, max_length=MAX_SAVED_TITLE * 2)]
    body: MessageBody


def _message(message: conversations.MessageView) -> MessageItem:
    return MessageItem(
        id=message.id,
        conversation_id=message.conversation_id,
        author=message.author_kind,
        author_name=message.author_name,
        body=message.body,
        created_at=message.created_at,
    )


def _conversation(view: conversations.ConversationView) -> ConversationItem:
    return ConversationItem(
        id=view.id,
        topic=view.topic,
        status=view.status,
        created_at=view.created_at,
        last_message_at=view.last_message_at,
        waiting_since=view.waiting_since,
    )


@client_router.get("/support/chat")
async def client_chat(
    client: CurrentClient, database: Db, after: After = None, limit: Limit = 100
) -> ClientChat:
    """The client's help chat. Poll with `after` set to the last message seen."""
    async with database.actor_transaction(client.actor) as session:
        conversation = await conversations.conversation_for_client(session, client.client_id)
        messages, unread = await conversations.messages_for_client(
            session, client.client_id, after=after, limit=limit
        )
    return ClientChat(
        conversation=_conversation(conversation) if conversation else None,
        messages=[_message(message) for message in messages],
        unread=unread,
    )


@client_router.post("/support/messages", status_code=201)
async def client_send(
    body: SendMessage, client: CurrentClient, database: Db, clock: Time
) -> MessageItem:
    """Send a message to Finvest. Opens a chat if none is open."""
    async with database.actor_transaction(client.actor) as session:
        message = await conversations.send_as_client(
            session, client.actor, body.body, body.key, clock.now(), topic=body.topic
        )
    return _message(message)


@client_router.post("/support/read", status_code=204)
async def client_read(client: CurrentClient, database: Db, clock: Time) -> None:
    """The client has the chat open and has seen the replies."""
    async with database.actor_transaction(client.actor) as session:
        await conversations.mark_read_by_client(session, client.actor, clock.now())


@staff_router.get("/support/conversations")
async def staff_inbox(
    staff: SupportStaff,
    database: Db,
    status: Annotated[Literal["open", "solved"], Query()] = "open",
    limit: Limit = 100,
) -> Inbox:
    """Chats, the client who has waited longest first."""
    async with database.actor_transaction(staff.actor) as session:
        items = await conversations.inbox(session, status=status, limit=limit)
    return Inbox(
        conversations=[
            InboxRow(
                conversation=_conversation(item.conversation),
                client_id=item.conversation.client_id,
                last_message=item.last_message,
                last_author=item.last_author,
                unread=item.unread,
            )
            for item in items
        ]
    )


@staff_router.get("/support/conversations/{conversation_id}")
async def staff_chat(
    conversation_id: PushId,
    staff: SupportStaff,
    database: Db,
    clock: Time,
    after: After = None,
    limit: Limit = 200,
) -> StaffChat:
    """One chat and its messages. Opening it marks it read."""
    async with database.actor_transaction(staff.actor) as session:
        conversation, messages = await conversations.conversation_for_staff(
            session, staff.actor, conversation_id, clock.now(), after=after, limit=limit
        )
    return StaffChat(
        conversation=_conversation(conversation),
        client_id=conversation.client_id,
        messages=[_message(message) for message in messages],
    )


@staff_router.post("/support/conversations/{conversation_id}/messages", status_code=201)
async def staff_send(
    conversation_id: PushId,
    body: StaffSendMessage,
    staff: SupportReplier,
    database: Db,
    clock: Time,
) -> MessageItem:
    """Reply to the client. They see the sender's first name."""
    async with database.actor_transaction(staff.actor) as session:
        message = await conversations.send_as_staff(
            session, staff.actor, staff.name, conversation_id, body.body, body.key, clock.now()
        )
    return _message(message)


@staff_router.post("/support/clients/{client_id}/messages", status_code=201)
async def staff_write_first(
    client_id: PushId,
    body: StaffWriteFirst,
    staff: SupportReplier,
    database: Db,
    clock: Time,
) -> MessageItem:
    """Write to a client in their open chat, opening one if there is none. 404 for a client
    who is not active."""
    async with database.actor_transaction(staff.actor) as session:
        message = await conversations.write_to_client(
            session,
            staff.actor,
            staff.name,
            client_id,
            body.body,
            body.key,
            clock.now(),
            topic=body.topic,
        )
    return _message(message)


@staff_router.post("/support/conversations/{conversation_id}/solve")
async def staff_solve(
    conversation_id: PushId, staff: SupportReplier, database: Db, clock: Time
) -> ConversationItem:
    async with database.actor_transaction(staff.actor) as session:
        return _conversation(
            await conversations.solve(session, staff.actor, conversation_id, clock.now())
        )


@staff_router.put("/support/conversations/{conversation_id}/topic")
async def staff_set_topic(
    conversation_id: PushId, body: SetTopic, staff: SupportReplier, database: Db
) -> ConversationItem:
    async with database.actor_transaction(staff.actor) as session:
        return _conversation(
            await conversations.set_topic(session, staff.actor, conversation_id, body.topic)
        )


@staff_router.get("/support/saved-replies")
async def list_saved_replies(staff: SupportStaff, database: Db) -> SavedReplies:
    async with database.actor_transaction(staff.actor) as session:
        replies = await conversations.saved_replies(session)
    return SavedReplies(
        replies=[SavedReplyItem(id=r.id, title=r.title, body=r.body) for r in replies]
    )


@staff_router.post("/support/saved-replies", status_code=201)
async def add_saved_reply(
    body: NewSavedReply, staff: SupportReplier, database: Db, clock: Time
) -> SavedReplyItem:
    async with database.actor_transaction(staff.actor) as session:
        reply = await conversations.save_reply(
            session, staff.actor, body.title, body.body, clock.now()
        )
    return SavedReplyItem(id=reply.id, title=reply.title, body=reply.body)


@staff_router.delete("/support/saved-replies/{reply_id}", status_code=204)
async def retire_saved_reply(
    reply_id: PushId, staff: SupportReplier, database: Db, clock: Time
) -> None:
    async with database.actor_transaction(staff.actor) as session:
        await conversations.retire_reply(session, staff.actor, reply_id, clock.now())


# Complaints ---------------------------------------------------------------------------------------


class Amount(Contract):
    """An amount in the smallest unit of its currency: whole shillings, or cents."""

    minor: Annotated[int, Field(gt=0, le=2**63 - 1)]
    currency: Literal["UGX", "USD"]


class ClientComplaint(Contract):
    """A complaint as its client sees it. Staff notes and the owner are not shown."""

    id: PushId
    reference: str
    category: Category
    summary: str
    details: str
    status: Status
    received_at: datetime
    # The day by which Finvest promised to answer.
    due_on: date
    closed_at: datetime | None
    outcome: Outcome | None
    resolution: str | None


class ClientComplaints(Contract):
    complaints: list[ClientComplaint]


# Sent with every new complaint. A request repeated after a lost answer logs it once.
ClientKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{8,64}$")]


class MakeComplaint(Contract):
    client_key: ClientKey
    category: Category
    summary: Annotated[str, Field(min_length=1, max_length=MAX_SUMMARY * 2)]
    details: Annotated[str, Field(min_length=1, max_length=MAX_DETAILS * 2)]


class StaffComplaint(Contract):
    id: PushId
    reference: str
    client_id: PushId
    conversation_id: PushId | None
    category: Category
    received_via: ReceivedVia
    summary: str
    details: str
    amount: Amount | None
    status: Status
    owner_id: PushId | None
    received_at: datetime
    due_on: date
    overdue: bool
    closed_at: datetime | None
    outcome: Outcome | None
    resolution: str | None
    client_accepted: bool | None


class ComplaintHistoryItem(Contract):
    id: PushId
    action: complaints.EventAction
    actor: Literal["client", "staff"]
    actor_id: PushId
    from_status: Status | None
    to_status: Status | None
    note: str | None
    occurred_at: datetime


class PendingOutcome(Contract):
    """An outcome one member of staff proposed. A different one confirms or rejects it."""

    id: PushId
    outcome: Outcome
    resolution: str
    client_accepted: bool
    proposed_by: PushId
    proposed_at: datetime


class RejectedOutcome(Contract):
    """The outcome a second person rejected last, and why, for the next proposal to start from."""

    outcome: Outcome
    resolution: str
    client_accepted: bool
    decided_by: PushId
    decided_at: datetime
    reason: str


class ComplaintDetail(Contract):
    complaint: StaffComplaint
    history: list[ComplaintHistoryItem]
    pending_outcome: PendingOutcome | None
    # Only while the latest proposal is the rejected one.
    last_rejected: RejectedOutcome | None


class ComplaintSummary(Contract):
    open: int
    overdue: int
    received_this_quarter: int
    closed_this_quarter: int
    upheld_this_quarter: int
    # Working days, in tenths: 24 is 2.4 days. None when nothing was closed this quarter.
    average_days_to_close_tenths: int | None
    target_working_days: int


class Register(Contract):
    summary: ComplaintSummary
    complaints: list[StaffComplaint]


class LogComplaint(Contract):
    client_key: ClientKey
    client_id: PushId
    category: Category
    received_via: Literal["chat", "phone", "email", "in_person", "letter"]
    summary: Annotated[str, Field(min_length=1, max_length=MAX_SUMMARY * 2)]
    details: Annotated[str, Field(min_length=1, max_length=MAX_DETAILS * 2)]
    amount: Amount | None = None
    conversation_id: PushId | None = None
    owner_id: PushId | None = None


class AssignComplaint(Contract):
    owner_id: PushId


class MoveComplaint(Contract):
    status: Literal["investigating", "waiting_for_client", "escalated"]
    note: Annotated[str, Field(min_length=1, max_length=MAX_NOTE * 2)]


class ComplaintNote(Contract):
    note: Annotated[str, Field(min_length=1, max_length=MAX_NOTE * 2)]


class ProposeOutcome(Contract):
    outcome: Outcome
    # What was done about it. The client reads this once the outcome is confirmed.
    resolution: Annotated[str, Field(min_length=1, max_length=MAX_RESOLUTION * 2)]
    client_accepted: bool


class DecideOutcome(Contract):
    # The proposal being decided, so a newer one is never confirmed by mistake.
    proposal_id: PushId
    decision: Literal["confirmed", "rejected", "withdrawn"]
    # Required to reject.
    reason: Annotated[str | None, Field(default=None, max_length=MAX_NOTE * 2)] = None


def _new(
    category: Category,
    summary: str,
    details: str,
    received_via: ReceivedVia,
    amount: Amount | None = None,
) -> NewComplaint:
    try:
        return NewComplaint(
            category=category,
            summary=summary,
            details=details,
            received_via=received_via,
            amount=Money(amount.minor, Currency(amount.currency)) if amount else None,
        )
    except InvalidComplaint as error:
        raise _invalid(error) from None


def _invalid(error: InvalidComplaint) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[error.field], code=error.code)]
    )


def _for_client(view: complaints.ComplaintView) -> ClientComplaint:
    return ClientComplaint(
        id=view.id,
        reference=view.reference,
        category=view.category,
        summary=view.summary,
        details=view.details,
        status=view.status,
        received_at=view.received_at,
        due_on=view.due_on,
        closed_at=view.closed_at,
        outcome=view.outcome,
        resolution=view.resolution,
    )


def _for_staff(view: complaints.ComplaintView) -> StaffComplaint:
    return StaffComplaint(
        id=view.id,
        reference=view.reference,
        client_id=view.client_id,
        conversation_id=view.conversation_id,
        category=view.category,
        received_via=view.received_via,
        summary=view.summary,
        details=view.details,
        amount=(
            Amount(minor=view.amount.minor, currency=view.amount.currency.value)
            if view.amount
            else None
        ),
        status=view.status,
        owner_id=view.owner_id,
        received_at=view.received_at,
        due_on=view.due_on,
        overdue=view.overdue,
        closed_at=view.closed_at,
        outcome=view.outcome,
        resolution=view.resolution,
        client_accepted=view.client_accepted,
    )


@client_router.get("/complaints")
async def client_complaints(client: CurrentClient, database: Db, clock: Time) -> ClientComplaints:
    """The client's complaints, newest first, each with its reference and reply date."""
    async with database.actor_transaction(client.actor) as session:
        listed = await complaints.complaints_for_client(
            session, client.client_id, business_date(clock.now())
        )
    return ClientComplaints(complaints=[_for_client(view) for view in listed])


@client_router.post("/complaints", status_code=201)
async def make_complaint(
    body: MakeComplaint, client: CurrentClient, database: Db, clock: Time
) -> ClientComplaint:
    """Make a formal complaint. It goes on the register and gets a reference and a reply date."""
    new = _new(body.category, body.summary, body.details, "app")
    async with database.actor_transaction(client.actor) as session:
        return _for_client(
            await complaints.log_by_client(session, client.actor, new, body.client_key, clock)
        )


@client_router.get("/complaints/{complaint_id}")
async def client_complaint(
    complaint_id: PushId, client: CurrentClient, database: Db, clock: Time
) -> ClientComplaint:
    async with database.actor_transaction(client.actor) as session:
        return _for_client(
            await complaints.complaint_for_client(
                session, client.client_id, complaint_id, business_date(clock.now())
            )
        )


@staff_router.get("/complaints")
async def complaint_register(
    staff: ComplaintsStaff,
    database: Db,
    clock: Time,
    show: Annotated[Literal["open", "closed", "all"], Query()] = "open",
    limit: Annotated[int, Query(ge=1, le=complaints.MAX_PAGE_SIZE)] = complaints.DEFAULT_PAGE_SIZE,
) -> Register:
    """The register with its figures. Open complaints come by deadline, the nearest first."""
    async with database.actor_transaction(staff.actor) as session:
        figures = await complaints.summary(session, clock)
        listed = await complaints.register(
            session, business_date(clock.now()), show=show, limit=limit
        )
    return Register(
        summary=ComplaintSummary(
            open=figures.open,
            overdue=figures.overdue,
            received_this_quarter=figures.received_this_quarter,
            closed_this_quarter=figures.closed_this_quarter,
            upheld_this_quarter=figures.upheld_this_quarter,
            average_days_to_close_tenths=figures.average_days_to_close_tenths,
            target_working_days=complaints.WORKING_DAYS_TO_REPLY,
        ),
        complaints=[_for_staff(view) for view in listed],
    )


@staff_router.post("/complaints", status_code=201)
async def log_complaint(
    body: LogComplaint, staff: ComplaintsManager, database: Db, clock: Time
) -> StaffComplaint:
    """Log a complaint a client made by phone, email, letter, in person or in chat."""
    new = _new(body.category, body.summary, body.details, body.received_via, body.amount)
    async with database.actor_transaction(staff.actor) as session:
        return _for_staff(
            await complaints.log_by_staff(
                session,
                staff.actor,
                body.client_id,
                new,
                body.client_key,
                clock,
                conversation_id=body.conversation_id,
                owner_id=body.owner_id,
            )
        )


@staff_router.get("/complaints/{complaint_id}")
async def complaint_detail(
    complaint_id: PushId, staff: ComplaintsStaff, database: Db, clock: Time
) -> ComplaintDetail:
    async with database.actor_transaction(staff.actor) as session:
        view, history = await complaints.complaint_for_staff(
            session, complaint_id, business_date(clock.now())
        )
        pending = await complaints.pending_outcome(session, complaint_id)
        rejected = await complaints.last_rejected(session, complaint_id)
    return ComplaintDetail(
        last_rejected=(
            RejectedOutcome(
                outcome=rejected.outcome,
                resolution=rejected.resolution,
                client_accepted=rejected.client_accepted,
                decided_by=rejected.decided_by,
                decided_at=rejected.decided_at,
                reason=rejected.reason,
            )
            if rejected
            else None
        ),
        pending_outcome=(
            PendingOutcome(
                id=pending.id,
                outcome=pending.outcome,
                resolution=pending.resolution,
                client_accepted=pending.client_accepted,
                proposed_by=pending.proposed_by,
                proposed_at=pending.proposed_at,
            )
            if pending
            else None
        ),
        complaint=_for_staff(view),
        history=[
            ComplaintHistoryItem(
                id=event.id,
                action=event.action,
                actor=event.actor_kind,
                actor_id=event.actor_id,
                from_status=event.from_status,
                to_status=event.to_status,
                note=event.note,
                occurred_at=event.occurred_at,
            )
            for event in history
        ],
    )


@staff_router.put("/complaints/{complaint_id}/owner")
async def assign_complaint(
    complaint_id: PushId, body: AssignComplaint, staff: ComplaintsManager, database: Db, clock: Time
) -> StaffComplaint:
    async with database.actor_transaction(staff.actor) as session:
        return _for_staff(
            await complaints.assign(session, staff.actor, complaint_id, body.owner_id, clock)
        )


@staff_router.post("/complaints/{complaint_id}/status")
async def move_complaint(
    complaint_id: PushId, body: MoveComplaint, staff: ComplaintsManager, database: Db, clock: Time
) -> StaffComplaint:
    """Move the complaint on, with a note saying why. `escalated` sends it to a manager."""
    async with database.actor_transaction(staff.actor) as session:
        return _for_staff(
            await complaints.move(session, staff.actor, complaint_id, body.status, body.note, clock)
        )


@staff_router.post("/complaints/{complaint_id}/notes")
async def note_complaint(
    complaint_id: PushId, body: ComplaintNote, staff: ComplaintsManager, database: Db, clock: Time
) -> StaffComplaint:
    async with database.actor_transaction(staff.actor) as session:
        return _for_staff(
            await complaints.add_note(session, staff.actor, complaint_id, body.note, clock)
        )


@staff_router.post("/complaints/{complaint_id}/outcome", status_code=201)
async def propose_outcome(
    complaint_id: PushId, body: ProposeOutcome, staff: ComplaintsManager, database: Db, clock: Time
) -> PendingOutcome:
    """Propose the outcome. A different member of staff confirms it, which closes the complaint."""
    try:
        closure = Closure(body.outcome, body.resolution, body.client_accepted)
    except InvalidComplaint as error:
        raise _invalid(error) from None
    async with database.actor_transaction(staff.actor) as session:
        proposal = await complaints.propose_outcome(
            session, staff.actor, complaint_id, closure, clock
        )
    return PendingOutcome(
        id=proposal.id,
        outcome=proposal.outcome,
        resolution=proposal.resolution,
        client_accepted=proposal.client_accepted,
        proposed_by=proposal.proposed_by,
        proposed_at=proposal.proposed_at,
    )


@staff_router.post("/complaints/{complaint_id}/outcome/decision")
async def decide_outcome(
    complaint_id: PushId, body: DecideOutcome, staff: OutcomeDecider, database: Db, clock: Time
) -> StaffComplaint:
    """Confirm or reject someone else's proposed outcome, or withdraw your own.

    Confirming closes the complaint for good and needs `complaints.close`. Neither the proposer
    nor whoever logged the complaint may confirm it.
    """
    if body.decision != "withdrawn" and "complaints.close" not in staff.permissions:
        raise ApplicationError("forbidden")
    async with database.actor_transaction(staff.actor) as session:
        return _for_staff(
            await complaints.decide_outcome(
                session,
                staff.actor,
                complaint_id,
                body.proposal_id,
                body.decision,
                body.reason,
                clock,
            )
        )
