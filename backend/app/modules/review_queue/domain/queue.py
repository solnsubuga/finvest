"""Which waiting items a member of staff sees in the review queue, in what order, and the
figures above it. Pure: the service reads the items, this decides what to show.

The queue is for work the system could not finish on its own. A change one person proposed
and a second must approve is in the approvals inbox on the Overview instead; an item here can
still say that a first person has looked at it, so nobody looks twice.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.modules.identity.domain.permissions import Permission
from app.shared.money import Money

Kind = Literal[
    # A bank transfer a client says they made, not yet found on the trust statement.
    "bank_transfer",
    # An identity check the automatic checks could not pass.
    "identity_check",
    # A payout account whose proof a person must compare with the client's ID.
    "payout_account",
    # A withdrawal above the amount that is paid without a person.
    "held_payout",
    # A mobile money payout the provider keeps taking nothing for.
    "payout_held_up",
    # A payout the provider never said the end of.
    "payout_unanswered",
    # A transfer of an investment to another client, held because it is large or screened.
    "investment_transfer",
    # An application for a company or group account.
    "organisation",
]

# The type filter on the screen, in the order the screen shows it.
Group = Literal["deposits", "withdrawals", "identity", "transfers", "accounts"]

GROUPS: dict[Kind, Group] = {
    "bank_transfer": "deposits",
    "identity_check": "identity",
    "payout_account": "accounts",
    "held_payout": "withdrawals",
    "payout_held_up": "withdrawals",
    "payout_unanswered": "withdrawals",
    "investment_transfer": "transfers",
    "organisation": "accounts",
}

# Needed to see each kind. The owning screen checks its own permissions again.
VIEW: dict[Kind, Permission] = {
    "bank_transfer": "reconciliation.view",
    "identity_check": "identity_checks.view",
    "payout_account": "review_queue.view",
    "held_payout": "review_queue.view",
    "payout_held_up": "money_movement.view",
    "payout_unanswered": "reconciliation.view",
    "investment_transfer": "review_queue.view",
    "organisation": "organisations.review",
}


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One piece of work, as its module reports it."""

    kind: Kind
    id: str
    # The client it is about. None for a company application, which names its applicant.
    client_id: str | None
    amount: Money | None
    # Why it is here, as a short code the console words. Never personal data.
    reason: str
    # Since when it has waited for a person.
    waiting_since: datetime
    # Someone has already looked and proposed what to do: it waits for a different person.
    # The reader may not be the one who decides it, which the owning screen enforces.
    proposed_by: str | None = None


@dataclass(frozen=True, slots=True)
class Queue:
    items: list[QueueItem]
    # Every kind this reader may see, so the screen can show a filter at zero.
    kinds: list[Kind]
    # Work the system finished on its own today, Kampala time.
    cleared_automatically_today: int

    @property
    def oldest(self) -> datetime | None:
        return min((item.waiting_since for item in self.items), default=None)


def visible_kinds(permissions: Iterable[Permission]) -> list[Kind]:
    """The kinds the reader may see, in a fixed order."""
    held = set(permissions)
    return [kind for kind, permission in VIEW.items() if permission in held]


def queue(
    found: Iterable[QueueItem], permissions: Iterable[Permission], cleared_today: int
) -> Queue:
    """What the reader sees, longest waiting first. A kind the reader may not see is dropped
    even if a caller read it."""
    kinds = visible_kinds(permissions)
    allowed = set(kinds)
    if cleared_today < 0:
        raise ValueError("A count is never negative")
    items = sorted(
        (item for item in found if item.kind in allowed),
        key=lambda item: (item.waiting_since, item.kind, item.id),
    )
    return Queue(items=items, kinds=kinds, cleared_automatically_today=cleared_today)
