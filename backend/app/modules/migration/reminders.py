"""A fresh claim link, every week, for migrated clients who have not used theirs.

The domain rule (`domain/claim.py`) decides who is due and caps how many reminders a client
gets. A client whose account is already theirs is skipped, even if their claim still shows the
link as unused: signing in commits in the identity module's own transaction, so a claim can lag
behind the account. Each reminder is a new link, sent as the first one was, by SMS and, when
there is an address, by email.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.service import client_contact
from app.modules.migration import claims
from app.modules.migration.carry_over import CLAIM_PATH
from app.modules.migration.domain.claim import LINK_LIFETIME
from app.modules.notifications.service import Channel, queue_message
from app.shared.actors import SYSTEM
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

# Claims read per job. A full page queues the next, after its last client.
PAGE_SIZE = 200


@dataclass(frozen=True)
class Page:
    sent: int
    skipped: int
    # Where the next page starts, or None when this was the last.
    last_id: str | None


async def send_page(
    session: AsyncSession, clock: Clock, *, client_app: str, after: str = "", limit: int = PAGE_SIZE
) -> Page:
    """Send the reminders due in one page of claims, in ID order after `after`, in the
    caller's transaction."""
    due = await claims.reminders_due(session, clock, after=after, limit=limit)
    sent = skipped = 0
    for client_id in due.client_ids:
        contact = await client_contact(session, client_id)
        if contact is None or contact.claimed:
            skipped += 1
            continue
        try:
            token = await claims.send_link(session, SYSTEM, clock, client_id)
        except ApplicationError:
            # Moved on, or reminded by another run since the list was read. send_link refuses
            # before it writes anything, so the transaction carries on.
            skipped += 1
            continue
        claim = await claims.claim_for(session, client_id)
        number = claim.reminders_sent if claim is not None else 0
        variables = {
            "link": f"{client_app}{CLAIM_PATH}#{token}",
            "hours": int(LINK_LIFETIME.total_seconds() // 3600),
        }
        channels: list[tuple[Channel, str]] = [("sms", contact.phone)]
        if contact.email:
            channels.append(("email", contact.email))
        for channel, recipient in channels:
            # The link expires with the message: a reminder is never sent after it stops working.
            await queue_message(
                session,
                channel=channel,
                recipient=recipient,
                template="claim_invitation",
                variables=variables,
                idempotency_key=f"migration-reminder:{client_id}:{number}:{channel}",
                now=clock.now(),
                deliver_within=LINK_LIFETIME,
            )
        sent += 1
    return Page(sent=sent, skipped=skipped, last_id=due.last_id)
