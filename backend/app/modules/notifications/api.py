"""Routes for the client's notification centre and the console's message wordings."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.identity.api import CurrentClient, require_permission
from app.modules.identity.service import StaffSession
from app.modules.notifications import announcements, inbox, preferences, wording_store
from app.modules.notifications.domain.events import EVENTS, Category, Channel
from app.modules.notifications.domain.preferences import ALWAYS_ON, CHANNELS
from app.modules.notifications.domain.wording import (
    MAX_ANNOUNCEMENT_MESSAGE,
    MAX_ANNOUNCEMENT_TITLE,
    Wording,
    render,
    sample_values,
)
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
}

client_router = APIRouter(prefix="/client", tags=["client notifications"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff", tags=["staff message wordings"], responses=_REFUSALS)
routers = [client_router, staff_router]

# Message wordings sit on the console's Settings screen. Opening it shows them. Changing what
# every client is told is a permission of its own, so read-only access to Settings can never
# be one of the two people a change needs.
SettingsStaff = Annotated[StaffSession, Depends(require_permission("settings.view"))]
WordingStaff = Annotated[StaffSession, Depends(require_permission("wordings.change"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
# The wire names. Contracts are strict, so a name arrives as text and is checked against these.
CategoryName = Literal["money", "investments", "earnings", "account", "security", "support", "news"]
ChannelName = Literal["push", "sms", "email"]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class LinkView(Contract):
    kind: inbox.LinkKind
    id: str


class NotificationItem(Contract):
    id: PushId
    event: str
    category: CategoryName
    title: str
    body: str
    link: LinkView | None
    created_at: datetime
    read: bool


class NotificationPage(Contract):
    items: list[NotificationItem]
    unread: int
    next_before: PushId | None


class MarkRead(Contract):
    # Leave out to mark every notification read.
    ids: Annotated[list[PushId], Field(max_length=inbox.MAX_MARKED)] | None = None


class Unread(Contract):
    unread: int


class CategoryChannels(Contract):
    category: CategoryName
    channels: list[ChannelName]
    # True when at least one of SMS and email must stay on.
    always_on: bool


class Preferences(Contract):
    categories: list[CategoryChannels]


class PreferenceChange(Contract):
    category: CategoryName
    channels: Annotated[list[ChannelName], Field(max_length=len(CHANNELS))]


class SavePreferences(Contract):
    categories: Annotated[list[PreferenceChange], Field(min_length=1, max_length=len(Category))]


def _preferences(channels: dict[Category, frozenset[Channel]]) -> Preferences:
    return Preferences(
        categories=[
            CategoryChannels(
                category=category.value,
                channels=[channel.value for channel in CHANNELS if channel in channels[category]],
                always_on=category in ALWAYS_ON,
            )
            for category in Category
        ]
    )


@client_router.get("/notifications")
async def list_notifications(
    client: CurrentClient,
    database: Db,
    before: Annotated[PushId | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=inbox.MAX_PAGE_SIZE)] = inbox.DEFAULT_PAGE_SIZE,
) -> NotificationPage:
    """The client's notifications, newest first."""
    async with database.actor_transaction(client.actor) as session:
        page = await inbox.page_for_client(session, client.client_id, before=before, limit=limit)
    return NotificationPage(
        items=[
            NotificationItem(
                id=item.id,
                event=item.event,
                category=item.category.value,
                title=item.title,
                body=item.body,
                link=LinkView(kind=item.link.kind, id=item.link.id) if item.link else None,
                created_at=item.created_at,
                read=item.read_at is not None,
            )
            for item in page.items
        ],
        unread=page.unread,
        next_before=page.next_before,
    )


@client_router.post("/notifications/read")
async def mark_notifications_read(
    body: MarkRead, client: CurrentClient, database: Db, clock: Time
) -> Unread:
    """Mark the named notifications read, or all of them."""
    async with database.actor_transaction(client.actor) as session:
        unread = await inbox.mark_read(session, client.actor, body.ids, clock.now())
    return Unread(unread=unread)


@client_router.get("/notification-preferences")
async def get_preferences(client: CurrentClient, database: Db) -> Preferences:
    async with database.actor_transaction(client.actor) as session:
        return _preferences(await preferences.channels_for_client(session, client.client_id))


@client_router.put("/notification-preferences")
async def save_preferences(
    body: SavePreferences, client: CurrentClient, database: Db, clock: Time
) -> Preferences:
    """Change the channels for the kinds of alert named. The others stay as they are."""
    choices = {
        Category(change.category): frozenset(Channel(channel) for channel in change.channels)
        for change in body.categories
    }
    async with database.actor_transaction(client.actor) as session:
        return _preferences(await preferences.save(session, client.actor, choices, clock.now()))


class WordingText(Contract):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1, max_length=1000)]
    sms: Annotated[str, Field(min_length=1, max_length=1000)]


class ProposalView(Contract):
    id: PushId
    wording: WordingText
    preview: WordingText
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    # The proposer can withdraw it. Anyone else who may change wordings can approve or reject.
    yours: bool


class WordingView(Contract):
    event: str
    category: CategoryName
    variables: list[str]
    wording: WordingText
    # The words with sample values in place of the placeholders.
    preview: WordingText
    edited: bool
    changed_at: datetime | None
    proposal: ProposalView | None


class WordingList(Contract):
    wordings: list[WordingView]


class ProposeWording(Contract):
    wording: WordingText
    reason: Reason


class DecideWording(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


def _text(wording: Wording) -> WordingText:
    return WordingText(title=wording.title, body=wording.body, sms=wording.sms)


def _preview(event_name: str, wording: Wording) -> WordingText:
    spec = EVENTS[event_name]
    rendered = render(spec, wording, sample_values(spec))
    return WordingText(title=rendered.title, body=rendered.body, sms=rendered.sms)


def _proposal(proposal: wording_store.Proposal, staff: StaffSession) -> ProposalView:
    return ProposalView(
        id=proposal.id,
        wording=_text(proposal.wording),
        preview=_preview(proposal.event, proposal.wording),
        reason=proposal.reason,
        proposed_by=proposal.proposed_by,
        proposed_at=proposal.proposed_at,
        yours=proposal.proposed_by == staff.staff_id,
    )


@staff_router.get("/notification-wordings")
async def list_wordings(staff: SettingsStaff, database: Db) -> WordingList:
    """Every message a client can receive, its words, and any change waiting for a decision."""
    async with database.actor_transaction(staff.actor) as session:
        listed = await wording_store.list_wordings(session)
    return WordingList(
        wordings=[
            WordingView(
                event=item.event,
                category=EVENTS[item.event].category.value,
                variables=list(EVENTS[item.event].variables),
                wording=_text(item.wording),
                preview=_preview(item.event, item.wording),
                edited=item.edited,
                changed_at=item.changed_at,
                proposal=_proposal(item.proposal, staff) if item.proposal else None,
            )
            for item in listed
        ]
    )


@staff_router.post("/notification-wordings/{event}/proposals", status_code=201)
async def propose_wording(
    event: Annotated[str, Field(max_length=64)],
    body: ProposeWording,
    staff: WordingStaff,
    database: Db,
    clock: Time,
) -> ProposalView:
    """Put a change of words forward. A different member of staff must approve it."""
    wording = Wording(body.wording.title, body.wording.body, body.wording.sms)
    async with database.actor_transaction(staff.actor) as session:
        proposal = await wording_store.propose(
            session, staff.actor, event, wording, body.reason, clock
        )
    return _proposal(proposal, staff)


@staff_router.post("/notification-wordings/proposals/{proposal_id}/decision")
async def decide_wording(
    proposal_id: PushId, body: DecideWording, staff: WordingStaff, database: Db, clock: Time
) -> ProposalView:
    """Approve or reject another person's proposal, or withdraw your own."""
    async with database.actor_transaction(staff.actor) as session:
        proposal = await wording_store.decide(
            session, staff.actor, proposal_id, body.decision, body.reason, clock
        )
    return _proposal(proposal, staff)


# Announcements go to every client, so they sit with the wordings and need the same two people.


class AnnouncementView(Contract):
    id: PushId
    title: str
    message: str
    reason: str
    status: Literal["proposed", "approved", "rejected", "withdrawn"]
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_reason: str | None
    # Clients told so far. It grows while the worker sends an approved one.
    told: int
    # The proposer can withdraw it. Anyone else who may change wordings can approve or reject.
    yours: bool


class AnnouncementList(Contract):
    announcements: list[AnnouncementView]


class ProposeAnnouncement(Contract):
    # Checked again in full, with the reasons, by the wording rules.
    title: Annotated[str, Field(min_length=1, max_length=MAX_ANNOUNCEMENT_TITLE * 2)]
    message: Annotated[str, Field(min_length=1, max_length=MAX_ANNOUNCEMENT_MESSAGE * 2)]
    reason: Reason


class DecideAnnouncement(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    # Required to reject.
    reason: Annotated[str | None, Field(default=None, max_length=500)] = None


def _announcement(item: announcements.Announcement, staff: StaffSession) -> AnnouncementView:
    return AnnouncementView(
        id=item.id,
        title=item.title,
        message=item.message,
        reason=item.reason,
        status=item.status,  # type: ignore[arg-type]
        proposed_by=item.proposed_by,
        proposed_at=item.proposed_at,
        decided_by=item.decided_by,
        decided_at=item.decided_at,
        decision_reason=item.decision_reason,
        told=item.told,
        yours=item.proposed_by == staff.staff_id,
    )


@staff_router.get("/announcements")
async def list_announcements(staff: SettingsStaff, database: Db) -> AnnouncementList:
    """The latest announcements, newest first, with any waiting for a second person."""
    async with database.actor_transaction(staff.actor) as session:
        listed = await announcements.list_announcements(session)
    return AnnouncementList(announcements=[_announcement(item, staff) for item in listed])


@staff_router.post("/announcements", status_code=201)
async def propose_announcement(
    body: ProposeAnnouncement, staff: WordingStaff, database: Db, clock: Time
) -> AnnouncementView:
    """Put an announcement to every client forward. A different member of staff approves it."""
    async with database.actor_transaction(staff.actor) as session:
        item = await announcements.propose(
            session, staff.actor, body.title, body.message, body.reason, clock
        )
    return _announcement(item, staff)


@staff_router.post("/announcements/{announcement_id}/decision")
async def decide_announcement(
    announcement_id: PushId,
    body: DecideAnnouncement,
    staff: WordingStaff,
    database: Db,
    clock: Time,
) -> AnnouncementView:
    """Approve, which starts sending, or reject someone else's announcement, or withdraw yours."""
    async with database.actor_transaction(staff.actor) as session:
        item = await announcements.decide(
            session, staff.actor, announcement_id, body.decision, body.reason, clock
        )
    return _announcement(item, staff)
