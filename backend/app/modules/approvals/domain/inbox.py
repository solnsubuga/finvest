"""Which waiting items a member of staff sees, and whether they may decide each one.

Pure rules: the service reads the items, this decides what to show.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.modules.identity.domain.permissions import Permission

Kind = Literal[
    "wording_change",
    "ledger_correction",
    "pot_transfer",
    "bank_transfer_match",
    "day_close",
    "dealing_order",
    "dealing_result",
    "rate_change",
    "migration_totals",
    "migration_sign_off",
    "phone_change",
    "team_change",
    "client_lock",
    "suppression_lift",
    "complaint_outcome",
    "announcement",
    "payout_account",
    "investment_correction",
]


@dataclass(frozen=True, slots=True)
class Source:
    # Needed to see the item at all.
    view: Permission
    # Needed to approve it. The owning route checks this too.
    decide: Permission


# One row per kind. Where a module has no separate permission for deciding, it is the
# screen's own, and the owning module still refuses the person who proposed it.
SOURCES: dict[Kind, Source] = {
    "wording_change": Source("settings.view", "wordings.change"),
    "ledger_correction": Source("finance.view", "finance.view"),
    "pot_transfer": Source("reconciliation.view", "reconciliation.view"),
    "bank_transfer_match": Source("reconciliation.view", "bank_transfers.change"),
    "day_close": Source("reconciliation.view", "reconciliation.view"),
    "dealing_order": Source("dealing_desk.view", "dealing_orders.change"),
    "dealing_result": Source("dealing_desk.view", "dealing_orders.change"),
    "rate_change": Source("rates.view", "rates.view"),
    "migration_totals": Source("migration.view", "migration.sign_off"),
    "migration_sign_off": Source("migration.view", "migration.sign_off"),
    "phone_change": Source("client_phones.change", "client_phones.change"),
    "team_change": Source("team.view", "team.change"),
    "client_lock": Source("clients.view", "clients.lock"),
    "suppression_lift": Source("clients.view", "email_suppressions.lift"),
    "complaint_outcome": Source("complaints.view", "complaints.close"),
    "announcement": Source("settings.view", "announcements.send"),
    # Checking a payout account is part of the review queue, so approving the check is too.
    "payout_account": Source("review_queue.view", "payout_accounts.change"),
    "investment_correction": Source("investments.view", "investment_corrections.change"),
}


@dataclass(frozen=True, slots=True)
class Waiting:
    """One item as its module reports it."""

    kind: Kind
    id: str
    # A short label from the item itself, such as a product code or a date. Never personal data.
    subject: str
    proposed_by: str
    proposed_at: datetime
    # The member of staff the item changes, when it changes one. They never decide it.
    about_staff: str | None = None


@dataclass(frozen=True, slots=True)
class InboxItem:
    kind: Kind
    id: str
    subject: str
    proposed_by: str
    proposed_at: datetime
    # True when the reader proposed it: they can only withdraw it, never approve it.
    yours: bool
    # True when the reader may approve or reject it.
    can_decide: bool


def visible_kinds(permissions: Iterable[Permission]) -> set[Kind]:
    held = set(permissions)
    return {kind for kind, source in SOURCES.items() if source.view in held}


def inbox(
    items: Iterable[Waiting], staff_id: str, permissions: Iterable[Permission]
) -> list[InboxItem]:
    """The items the reader may see, oldest first, marked with what they may do."""
    held = set(permissions)
    shown = [
        InboxItem(
            kind=item.kind,
            id=item.id,
            subject=item.subject,
            proposed_by=item.proposed_by,
            proposed_at=item.proposed_at,
            yours=item.proposed_by == staff_id,
            can_decide=item.proposed_by != staff_id
            and item.about_staff != staff_id
            and SOURCES[item.kind].decide in held,
        )
        for item in items
        if SOURCES[item.kind].view in held
    ]
    return sorted(shown, key=lambda item: (item.proposed_at, item.kind, item.id))
