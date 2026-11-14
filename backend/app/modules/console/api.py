"""Routes for the console's Clients screen: find a client, see their page, lock or unlock them.

Seeing a client is `clients.view`. Proposing or deciding a lock or unlock is `clients.lock`.
Every lock and unlock needs two people: one proposes, a different one approves.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.console import clients, product_reach
from app.modules.identity import client_locks
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.shared.business_day import business_date
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
routers = [router, product_reach.router]

ClientViewer = Annotated[StaffSession, Depends(require_permission("clients.view"))]
ClientLocker = Annotated[StaffSession, Depends(require_permission("clients.lock"))]
# Deciding needs a passkey or app code from the last few minutes.
LockDecider = Annotated[StaffSession, Depends(require_confirmed("clients.lock"))]
Reason = Annotated[str, Field(min_length=1, max_length=client_locks.MAX_REASON)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class AccountView(Contract):
    id: PushId
    phone: str
    email: str | None
    status: client_locks.ClientStatus
    created_at: datetime
    restricted_until: datetime | None
    has_passkey: bool
    has_authenticator: bool
    last_signed_in_at: datetime | None


class IdentityCheckView(Contract):
    id: PushId
    status: str
    submitted_at: datetime | None
    decided_at: datetime | None
    account_number: str | None


class OwedView(Contract):
    account_code: str
    account_reference: str | None
    # Whole units of the smallest denomination: shillings for UGX, cents for USD.
    amount_minor: int
    currency: str


class DestinationView(Contract):
    id: PushId
    kind: str
    network: str | None
    bank_code: str | None
    masked_account: str
    holder_name: str
    status: str
    created_at: datetime


class LockView(Contract):
    id: PushId
    kind: client_locks.LockKind
    client_id: PushId
    reason: str
    proposed_by: PushId
    proposed_by_name: str
    proposed_at: datetime
    status: Literal["proposed", "approved", "rejected", "withdrawn"]
    decided_by: PushId | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    # The proposer can withdraw it. Anyone else who may lock clients can approve or reject it.
    yours: bool


class ConversationSummary(Contract):
    id: PushId
    topic: str
    status: str
    last_message_at: datetime
    waiting_since: datetime | None


class ComplaintSummary(Contract):
    id: PushId
    reference: str
    category: str
    summary: str
    status: str
    received_at: datetime
    due_on: date
    overdue: bool


class ClientPageView(Contract):
    account: AccountView
    verified_name: str | None
    identity_check: IdentityCheckView | None
    owed: list[OwedView]
    destinations: list[DestinationView]
    locks: list[LockView]
    # None when this member of staff may not see support chats or complaints.
    conversation: ConversationSummary | None
    complaints: list[ComplaintSummary] | None
    can_lock: bool


class SearchResult(Contract):
    clients: list[AccountView]


class ClientCardView(Contract):
    id: PushId
    # The name on the approved identity document. None until the check is approved.
    verified_name: str | None
    account_number: str | None
    status: client_locks.ClientStatus
    created_at: datetime


class ClientCards(Contract):
    cards: list[ClientCardView]


MAX_CARDS = 100


class ProposeLock(Contract):
    kind: client_locks.LockKind
    reason: Reason


class DecideLock(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


@router.get("/search")
async def search(
    staff: ClientViewer, database: Db, q: Annotated[str, Query(min_length=1, max_length=40)]
) -> SearchResult:
    """Find a client by ID, account number or mobile number."""
    async with database.actor_transaction(staff.actor) as session:
        found = await clients.find_client(session, q)
    return SearchResult(clients=[_account(found)] if found else [])


@router.get("/cards")
async def cards(
    staff: ClientViewer,
    database: Db,
    ids: Annotated[list[PushId], Query(min_length=1, max_length=MAX_CARDS)],
) -> ClientCards:
    """Who these clients are, by ID: name once verified, account number, status and when they
    joined. For screens that list clients by ID, so they need not read each whole page."""
    async with database.actor_transaction(staff.actor) as session:
        found = await clients.client_cards(session, ids)
    return ClientCards(
        cards=[
            ClientCardView(
                id=card.account.id,
                verified_name=card.verified_name,
                account_number=card.account_number,
                status=card.account.status,
                created_at=card.account.created_at,
            )
            for card in found
        ]
    )


@router.get("/{client_id}")
async def client_page(
    client_id: PushId, staff: ClientViewer, database: Db, clock: Time
) -> ClientPageView:
    """Everything staff need about one client, on one page."""
    async with database.actor_transaction(staff.actor) as session:
        page = await clients.client_page(
            session,
            client_id,
            business_date(clock.now()),
            with_support="support.view" in staff.permissions,
            with_complaints="complaints.view" in staff.permissions,
        )
    if page is None:
        raise ApplicationError("not_found")
    check = page.identity_check
    return ClientPageView(
        account=_account(page.account),
        verified_name=page.verified_name,
        identity_check=IdentityCheckView(
            id=check.id,
            status=check.status,
            submitted_at=check.submitted_at,
            decided_at=check.decided_at,
            account_number=check.account_number,
        )
        if check
        else None,
        owed=[
            OwedView(
                account_code=item.account_code,
                account_reference=item.account_reference,
                amount_minor=item.owed.minor,
                currency=item.owed.currency.value,
            )
            for item in page.owed
        ],
        destinations=[
            DestinationView(
                id=item.id,
                kind=item.kind,
                network=item.network,
                bank_code=item.bank_code,
                masked_account=item.masked_account,
                holder_name=item.holder_name,
                status=item.status,
                created_at=item.created_at,
            )
            for item in page.destinations
        ],
        locks=[_lock(item, staff) for item in page.locks],
        conversation=ConversationSummary(
            id=page.conversation.id,
            topic=page.conversation.topic,
            status=page.conversation.status,
            last_message_at=page.conversation.last_message_at,
            waiting_since=page.conversation.waiting_since,
        )
        if page.conversation
        else None,
        complaints=[
            ComplaintSummary(
                id=item.id,
                reference=item.reference,
                category=item.category,
                summary=item.summary,
                status=item.status,
                received_at=item.received_at,
                due_on=item.due_on,
                overdue=item.overdue,
            )
            for item in page.complaints
        ]
        if page.complaints is not None
        else None,
        can_lock="clients.lock" in staff.permissions,
    )


@router.post("/{client_id}/locks", status_code=201)
async def propose_lock(
    client_id: PushId, body: ProposeLock, staff: ClientLocker, database: Db, clock: Time
) -> LockView:
    """Ask for the client to be locked or unlocked. A different member of staff decides."""
    async with database.actor_transaction(staff.actor) as session:
        change = await client_locks.propose(
            session, staff.actor, client_id, body.kind, body.reason, clock
        )
    return _lock(change, staff)


@router.post("/locks/{lock_id}/decision")
async def decide_lock(
    lock_id: PushId, body: DecideLock, staff: LockDecider, database: Db, clock: Time
) -> LockView:
    """Approve or reject someone else's lock or unlock, or withdraw your own."""
    async with database.actor_transaction(staff.actor) as session:
        change = await client_locks.decide(
            session, staff.actor, lock_id, body.decision, body.reason, clock
        )
    return _lock(change, staff)


def _account(account: client_locks.ClientAccount) -> AccountView:
    return AccountView(
        id=account.id,
        phone=account.phone,
        email=account.email,
        status=account.status,
        created_at=account.created_at,
        restricted_until=account.restricted_until,
        has_passkey=account.has_passkey,
        has_authenticator=account.has_authenticator,
        last_signed_in_at=account.last_signed_in_at,
    )


def _lock(change: client_locks.LockChange, staff: StaffSession) -> LockView:
    return LockView(
        id=change.id,
        kind=change.kind,
        client_id=change.client_id,
        reason=change.reason,
        proposed_by=change.proposed_by,
        proposed_by_name=change.proposed_by_name,
        proposed_at=change.proposed_at,
        status=change.status,  # type: ignore[arg-type]
        decided_by=change.decided_by,
        decided_by_name=change.decided_by_name,
        decided_at=change.decided_at,
        decision_reason=change.decision_reason,
        yours=change.proposed_by == staff.staff_id,
    )
