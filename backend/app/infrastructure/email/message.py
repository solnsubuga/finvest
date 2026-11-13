"""A provider-neutral outgoing email and its validation.

Every message is HTML with a plain-text alternative, to one recipient. Addresses are ASCII
only and no header value may contain a line break, so caller data can never inject a header.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.utils import parseaddr

MAX_SUBJECT = 200
MAX_BODY_BYTES = 500_000
MAX_TAGS = 5

ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
# Provider tags (Resend): ASCII letters, digits, underscores and dashes only.
TAG = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:@-]{1,200}$")


class InvalidEmail(ValueError):
    """The message cannot be sent as given. Retrying the same message cannot succeed."""


def check_address(value: str) -> str:
    if len(value) > 254 or not ADDRESS.fullmatch(value):
        raise InvalidEmail("An email address is not valid")
    return value


def single_line(value: str, *, limit: int, what: str) -> str:
    if not value or len(value) > limit or "\r" in value or "\n" in value:
        raise InvalidEmail(f"The {what} must be one line of at most {limit} characters")
    return value


@dataclass(frozen=True)
class Sender:
    """The From identity: an address and a display name."""

    address: str
    name: str = ""

    @classmethod
    def parse(cls, value: str) -> "Sender":
        """Accept `no-reply@finvestug.com` or `Finvest <no-reply@finvestug.com>`."""
        single_line(value, limit=320, what="sender")
        name, address = parseaddr(value, strict=True)
        if not address:
            raise InvalidEmail("The sender is not a valid address")
        return cls(address=check_address(address), name=name.strip())

    @property
    def domain(self) -> str:
        return self.address.rpartition("@")[2]

    def header(self) -> str:
        local, _, domain = self.address.rpartition("@")
        return str(Address(self.name, local, domain))


@dataclass(frozen=True)
class EmailMessage:
    """One message. `idempotency_key` lets the provider drop a repeated send of it."""

    to: str = field(repr=False)
    subject: str = field(repr=False)
    html: str = field(repr=False)
    text: str = field(repr=False)
    idempotency_key: str
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        check_address(self.to)
        single_line(self.subject, limit=MAX_SUBJECT, what="subject")
        if not IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise InvalidEmail("The idempotency key is not valid")
        if not self.html.strip() or not self.text.strip():
            raise InvalidEmail("A message needs both an HTML and a plain-text body")
        if len(self.html.encode()) + len(self.text.encode()) > MAX_BODY_BYTES:
            raise InvalidEmail("The message body is too large")
        if len(self.tags) > MAX_TAGS or not all(
            TAG.fullmatch(name) and TAG.fullmatch(value) for name, value in self.tags.items()
        ):
            raise InvalidEmail("A message tag is not valid")
