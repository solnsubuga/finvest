"""What Resend tells us about an email after it was sent, read from its webhook body.

Only the facts that change what we do are kept: which message, what happened, when, and who
it was addressed to as a one-way hash. The rest of the body, which repeats the subject and
the addresses, is dropped as soon as it is read.

Delivery only moves forward. An event that arrives late, such as `email.sent` after
`email.delivered`, changes nothing. A complaint outranks everything, because it is what the
person did last.
"""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Delivery = Literal["accepted", "delayed", "delivered", "failed", "bounced", "complained"]

# Resend's event type, and the delivery state it means. Other types (opened, clicked, contact
# and domain events) are acknowledged and ignored.
EVENT_TYPES: dict[str, Delivery] = {
    "email.sent": "accepted",
    "email.delivery_delayed": "delayed",
    "email.delivered": "delivered",
    "email.failed": "failed",
    "email.bounced": "bounced",
    "email.complained": "complained",
}
RANK: dict[str, int] = {
    "accepted": 1,
    "delayed": 2,
    "delivered": 3,
    "failed": 4,
    "bounced": 4,
    "complained": 5,
}
# Resend's own email ids are UUIDs. Kept to a safe shape before storing.
EMAIL_ID = re.compile(r"^[A-Za-z0-9-]{1,100}$")
MAX_RECIPIENTS = 50


class UnreadableEvent(ValueError):
    """A signed body that is not a Resend email event we can read."""


@dataclass(frozen=True, slots=True)
class EmailEvent:
    delivery: Delivery
    email_id: str
    occurred_at: datetime | None
    # sha256 of each lower-cased address. Enough to suppress an address, not to read one.
    recipients: tuple[str, ...]
    # A bounce Resend calls "Transient" may clear up by itself, so it suppresses nothing.
    permanent: bool

    @property
    def suppresses(self) -> bool:
        return self.delivery == "complained" or (self.delivery == "bounced" and self.permanent)


def address_hash(address: str) -> str:
    """The plain hash, as stored until the keyed one replaces it."""
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


def keyed_hash(plain: str, key: bytes) -> str:
    """HMAC-SHA256 under the server's key, over the plain hash. Taken over the plain hash, not
    the address, so every hash already stored can be keyed without the address it came from."""
    return hmac.new(key, plain.encode(), hashlib.sha256).hexdigest()


def keyed_hashes(plain: tuple[str, ...] | list[str], key: bytes | None) -> list[str] | None:
    return None if key is None else [keyed_hash(value, key) for value in plain]


def moves_forward(current: str | None, new: Delivery) -> bool:
    if current is None:
        return True
    return RANK[new] > RANK.get(current, 0)


def read_event(body: bytes) -> EmailEvent | None:
    """The event in a webhook body, or None for a type we do not act on."""
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UnreadableEvent("The body is not JSON") from None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("type"), str):
        raise UnreadableEvent("The body has no event type")
    delivery = EVENT_TYPES.get(parsed["type"])
    if delivery is None:
        return None
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise UnreadableEvent("The event has no data")
    email_id = data.get("email_id")
    if not isinstance(email_id, str) or not EMAIL_ID.fullmatch(email_id):
        raise UnreadableEvent("The event names no email")
    to = data.get("to", [])
    if isinstance(to, str):
        to = [to]
    if not isinstance(to, list) or len(to) > MAX_RECIPIENTS:
        raise UnreadableEvent("The event's recipients are not a list")
    recipients = tuple(sorted({address_hash(a) for a in to if isinstance(a, str) and "@" in a}))
    bounce = data.get("bounce")
    bounce_type = bounce.get("type") if isinstance(bounce, dict) else None
    return EmailEvent(
        delivery=delivery,
        email_id=email_id,
        occurred_at=_when(parsed.get("created_at")),
        recipients=recipients,
        permanent=bounce_type != "Transient",
    )


def _when(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        found = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo is not None else None
