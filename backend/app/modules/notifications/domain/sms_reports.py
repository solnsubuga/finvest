"""What Africa's Talking tells us about a text message after it was sent.

Africa's Talking posts a delivery report to the callback address set in its dashboard (SMS,
Callback URLs, Delivery Reports), as a form: `id` (its message id), `status`, and for a
failure `failureReason`, plus `phoneNumber`, `networkCode` and `retryCount`. Only the id, the
status and the reason are kept. The phone number is read past and never stored.

Delivery moves forward, except that a failure can still turn into a delivery: Africa's Talking
retries a message the network did not take, and "Success" is the last word.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs

from app.modules.notifications.domain.email_events import Delivery

# Africa's Talking's report status, and the delivery state it means. A status not listed here
# is answered and dropped.
STATUSES: dict[str, Delivery] = {
    "Sent": "accepted",
    "Submitted": "accepted",
    "Buffered": "delayed",
    "Success": "delivered",
    "Failed": "failed",
    "Rejected": "failed",
    "Expired": "failed",
}
RANK: dict[str, int] = {"accepted": 1, "delayed": 2, "failed": 3, "delivered": 4}
MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
# Africa's Talking's reasons are single words such as `InsufficientCredit` or
# `UserInBlacklist`. Anything else is not stored.
REASON = re.compile(r"^[A-Za-z]{1,40}$")


class UnreadableReport(ValueError):
    """A body that is not an Africa's Talking delivery report we can read."""


@dataclass(frozen=True, slots=True)
class SmsReport:
    provider_message_id: str
    # Africa's Talking's own word, kept so one report per status is recorded.
    status: str
    delivery: Delivery
    reason: str | None


def moves_forward(current: str | None, new: Delivery) -> bool:
    if current is None:
        return True
    return RANK[new] > RANK.get(current, 0)


def read_report(body: bytes, content_type: str | None) -> SmsReport | None:
    """The report in a callback body, or None for a status we do not act on."""
    fields = _fields(body, content_type)
    message_id = fields.get("id")
    if not isinstance(message_id, str) or not MESSAGE_ID.fullmatch(message_id):
        raise UnreadableReport("The report names no message")
    status = fields.get("status")
    if not isinstance(status, str):
        raise UnreadableReport("The report has no status")
    delivery = STATUSES.get(status)
    if delivery is None:
        return None
    reason = fields.get("failureReason")
    kept = reason if delivery == "failed" and isinstance(reason, str) else None
    if kept is not None and not REASON.fullmatch(kept):
        kept = "Unknown"
    return SmsReport(provider_message_id=message_id, status=status, delivery=delivery, reason=kept)


def _fields(body: bytes, content_type: str | None) -> dict[str, object]:
    try:
        text = body.decode()
    except UnicodeDecodeError:
        raise UnreadableReport("The body is not text") from None
    if (content_type or "").split(";")[0].strip().lower() == "application/json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise UnreadableReport("The body is not JSON") from None
        if not isinstance(parsed, dict):
            raise UnreadableReport("The body is not an object")
        return parsed
    # A form, as Africa's Talking sends it. A field given twice is not believed.
    found = parse_qs(text, keep_blank_values=True)
    return {name: values[0] for name, values in found.items() if len(values) == 1}
