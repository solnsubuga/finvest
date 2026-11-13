"""Filters, page cursors and the CSV export of the audit trail. Pure: no HTTP or persistence.

A page is read newest first and continues from a cursor: the time and ID of the last event
shown. The cursor is opaque to the console but carries nothing secret.
"""

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime

from app.modules.audit.domain.events import (
    ACTION,
    CORRELATION_ID,
    PUSH_ID,
    RESOURCE_ID,
    RESOURCE_TYPE,
    ActorKind,
    AuditCategory,
    AuditOutcome,
)

# An action prefix ends at a dot, such as `identity.team.`, and matches every action under it.
ACTION_PREFIX = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*\.")


class InvalidFilter(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


@dataclass(frozen=True, slots=True)
class AuditFilter:
    category: AuditCategory | None = None
    # A whole action, or a prefix ending in a dot.
    action: str | None = None
    outcome: AuditOutcome | None = None
    actor_kind: ActorKind | None = None
    actor_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    correlation_id: str | None = None
    # From this time, inclusive, to this time, exclusive.
    since: datetime | None = None
    until: datetime | None = None

    def __post_init__(self) -> None:
        if self.action is not None and not (
            ACTION.fullmatch(self.action) or ACTION_PREFIX.fullmatch(self.action)
        ):
            raise InvalidFilter("action")
        if self.action is not None and len(self.action) > 100:
            raise InvalidFilter("action")
        if self.actor_id is not None and not PUSH_ID.fullmatch(self.actor_id):
            raise InvalidFilter("actor_id")
        if self.resource_type is not None and not RESOURCE_TYPE.fullmatch(self.resource_type):
            raise InvalidFilter("resource_type")
        if self.resource_id is not None and not RESOURCE_ID.fullmatch(self.resource_id):
            raise InvalidFilter("resource_id")
        if self.correlation_id is not None and not CORRELATION_ID.fullmatch(self.correlation_id):
            raise InvalidFilter("correlation_id")
        for name, moment in (("since", self.since), ("until", self.until)):
            if moment is not None and moment.tzinfo is None:
                raise InvalidFilter(name)
        if self.since and self.until and self.since >= self.until:
            raise InvalidFilter("until")

    @property
    def is_prefix(self) -> bool:
        return self.action is not None and self.action.endswith(".")


@dataclass(frozen=True, slots=True)
class Cursor:
    occurred_at: datetime
    id: str


def encode_cursor(cursor: Cursor) -> str:
    raw = f"{cursor.occurred_at.isoformat()}|{cursor.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> Cursor:
    if len(value) > 200:
        raise InvalidFilter("cursor")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        moment, event_id = raw.split("|")
        occurred_at = datetime.fromisoformat(moment)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise InvalidFilter("cursor") from None
    if occurred_at.tzinfo is None or not PUSH_ID.fullmatch(event_id):
        raise InvalidFilter("cursor")
    return Cursor(occurred_at, event_id)


# A spreadsheet runs a cell that starts with one of these as a formula. The trail is redacted,
# but a reason or detail is still text someone typed.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: str) -> str:
    """A cell that a spreadsheet shows as text and never runs."""
    return f"'{value}" if value.startswith(_FORMULA_START) else value
