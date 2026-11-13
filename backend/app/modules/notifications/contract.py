"""What other modules import to tell a client something. See docs/notifications.md.

    from app.modules.notifications import contract as notifications

    await notifications.notify(
        session,
        event_name="payout_sent",
        client_id=payout.client_id,
        idempotency_key=payout.id,
        variables={"amount": payout.amount, "destination": "MTN Mobile Money"},
        link=notifications.Link("payout", payout.id),
        now=clock.now(),
    )

Call it in the same transaction as the change it reports. A module that emails a client itself,
such as documents with a PDF attached, asks `is_suppressed(session, address)` first; it reads
the suppression list, which only the worker role can. Nothing else in this module is for other
modules to call.
"""

from app.modules.notifications.domain.events import EVENTS, Category, EventSpec, UnknownEvent
from app.modules.notifications.domain.wording import InvalidVariables, Value
from app.modules.notifications.email_delivery import is_suppressed
from app.modules.notifications.inbox import Link, LinkKind, notify

__all__ = [
    "EVENTS",
    "Category",
    "EventSpec",
    "InvalidVariables",
    "Link",
    "LinkKind",
    "UnknownEvent",
    "Value",
    "is_suppressed",
    "notify",
]
