"""Reads the counts beside the console menu, inside the caller's staff transaction.

Only the sources the reader's permissions show are read, and each is read no further than the
menu counts (CAP), so the menu costs a few small queries whichever role asks.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.service import StaffSession
from app.modules.investments import service as investments
from app.modules.investments.domain.rates import ProposalStatus
from app.modules.review_queue import service as review_queue
from app.modules.review_queue.domain.menu import CAP, Screen, counted_screens, menu_counts
from app.modules.screening import service as screening
from app.modules.screening.domain.cases import CaseStatus
from app.modules.support import complaints, conversations
from app.shared.business_day import business_date
from app.shared.clock import Clock

# Read from the review queue, which already reads only the kinds the reader may see.
_FROM_QUEUE: frozenset[Screen] = frozenset({"review-queue", "identity-checks", "reconciliation"})


async def counts(session: AsyncSession, staff: StaffSession, clock: Clock) -> dict[Screen, int]:
    now = clock.now()
    screens = set(counted_screens(staff.permissions))
    found: dict[Screen, int] = {}
    if screens & _FROM_QUEUE:
        waiting = (await review_queue.waiting_for(session, staff, now)).items
        found["review-queue"] = len(waiting)
        found["identity-checks"] = sum(1 for item in waiting if item.kind == "identity_check")
        # The Reconciliation screen lists bank transfers still to find on the statement or to
        # approve, the same ones the review queue shows under deposits.
        found["reconciliation"] = sum(1 for item in waiting if item.kind == "bank_transfer")
    if "maturities" in screens:
        # Terms the run could not close, and matured money waiting on the client's choice: what
        # the Maturities screen says needs a person.
        due = await investments.maturities(session, staff.actor, clock)
        found["maturities"] = len(due.not_closed) + len(due.held)
    if "support" in screens:
        found["support"] = await _support(session)
    if "complaints" in screens:
        found["complaints"] = await _complaints(session, business_date(now))
    if "rates" in screens:
        found["rates"] = len(
            await investments.list_proposals(
                session, staff.actor, status=ProposalStatus.PROPOSED, limit=CAP
            )
        )
    if "compliance" in screens:
        found["compliance"] = len(
            await screening.list_cases(session, status=CaseStatus.OPEN, limit=CAP)
        )
    return menu_counts(found, staff.permissions)


async def _support(session: AsyncSession) -> int:
    # Open chats whose last word is the client's: they wait for a reply from us.
    chats = await conversations.inbox(session, status="open", limit=CAP)
    return sum(1 for chat in chats if chat.last_author == "client")


async def _complaints(session: AsyncSession, today: date) -> int:
    return len(await complaints.register(session, today, show="open", limit=CAP))
