"""Rules for the help chat. Pure: no I/O."""

import re
from typing import Literal, get_args

Topic = Literal["deposit", "withdrawal", "investment", "identity", "account", "other"]
TOPICS: tuple[Topic, ...] = get_args(Topic)
ConversationStatus = Literal["open", "solved"]
AuthorKind = Literal["client", "staff"]

MAX_MESSAGE = 2000
MAX_SAVED_TITLE = 60
# The caller makes one up per message it means to send, and sends it again on a retry.
MESSAGE_KEY = re.compile(r"[A-Za-z0-9._:-]{8,64}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InvalidMessage(ValueError):
    def __init__(self, field: str, code: str) -> None:
        super().__init__(code)
        self.field = field
        self.code = code


def clean_message(body: str) -> str:
    """The message as it is stored: trimmed, with line endings made the same."""
    cleaned = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        raise InvalidMessage("body", "required")
    if len(cleaned) > MAX_MESSAGE:
        raise InvalidMessage("body", "too_long")
    if _CONTROL.search(cleaned):
        raise InvalidMessage("body", "invalid_characters")
    return cleaned


def check_key(key: str) -> str:
    if not MESSAGE_KEY.fullmatch(key):
        raise InvalidMessage("key", "invalid")
    return key


def first_name(full_name: str) -> str:
    """How a member of staff is named to a client: "Grace", never the full name."""
    parts = full_name.split()
    return parts[0][:40] if parts else "Finvest"
