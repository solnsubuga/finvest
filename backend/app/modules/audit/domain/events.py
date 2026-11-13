"""Audit events and the redaction of their details. Pure: no HTTP or persistence imports.

An event names what happened (`action`), to what (`resource_type`, `resource_id`), who did it
(`actor`), why (`reason`) and in which request or job (`correlation_id`). Structural fields are
validated and refused when unsafe, because a bad one is a programming error. Free-form `details`
and `reason` are redacted instead, so auditing is never the reason a business change fails:
sensitive keys are masked, phone numbers, account numbers, email addresses and credentials
inside strings are replaced, and nesting, size and types are bounded.

The trail records identifiers, never the personal data they point to.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, get_args

AuditCategory = Literal["change", "access", "approval", "operator_control", "security"]
AuditOutcome = Literal["succeeded", "denied", "failed"]
ActorKind = Literal["client", "staff", "system", "provider", "anonymous"]

CATEGORIES: tuple[AuditCategory, ...] = get_args(AuditCategory)
OUTCOMES: tuple[AuditOutcome, ...] = get_args(AuditOutcome)
ACTOR_KINDS: tuple[ActorKind, ...] = get_args(ActorKind)
# A person is always identified. Finvest itself, a provider and a visitor who is not signed in
# have no ID.
PERSON_KINDS = frozenset({"client", "staff"})
# These categories record a decision someone made, so they must say why.
REASON_REQUIRED = frozenset({"approval", "operator_control"})

REDACTED = "[redacted]"
MAX_DETAIL_KEYS = 32
MAX_DETAIL_DEPTH = 3
MAX_LIST_ITEMS = 50
MAX_STRING_LENGTH = 500
MAX_REASON_LENGTH = 500
MAX_INTEGER = 2**63 - 1

ACTION = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
RESOURCE_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}")
RESOURCE_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")
CORRELATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
PUSH_ID = re.compile(r"[-0-9A-Z_a-z]{20}")
DETAIL_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")

# Keys whose values are never stored, whatever they contain. Words match whole
# underscore-separated parts of a key (`error_code` matches `code`, `pinned_version` does not
# match `pin`); phrases match anywhere.
SENSITIVE_KEY_WORDS = frozenset(
    {
        "password",
        "passwords",
        "passcode",
        "secret",
        "secrets",
        "token",
        "tokens",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "cookies",
        "phone",
        "msisdn",
        "mobile",
        "email",
        "nin",
        "address",
        "body",
        "code",
        "codes",
        "otp",
        "pin",
        "payload",
        "passkey",
    }
)
SENSITIVE_KEY_PHRASES = (
    "apikey",
    "api_key",
    "private_key",
    "passwd",
    "account_number",
    "date_of_birth",
    "full_name",
)

# Value patterns replaced wherever they appear inside an otherwise allowed string.
VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Credentials in URLs, authorisation schemes and key=value assignments.
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"), "[redacted-credential]@"),
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), "[redacted-credential]"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|code|pin)\s*[=:]\s*\S+"),
        "[redacted-credential]",
    ),
    # Email addresses identify people as surely as phone numbers.
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[redacted-email]"),
    # Seven or more digits, optionally with +, spaces, dots, dashes or brackets: a phone number,
    # a Finvest account number or a bank account number.
    (re.compile(r"(?<![\w-])\+?\d(?:[\s().-]*\d){6,}(?![\w-])"), "[redacted-number]"),
)
# ISO dates and times are safe; they are set aside before the patterns run.
PROTECTED = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?"
)

DetailValue = str | int | bool | None | list["DetailValue"] | dict[str, "DetailValue"]


class UnsafeAuditEvent(ValueError):
    """A structural field would store unsafe or unbounded data."""


@dataclass(frozen=True, slots=True)
class AuditActor:
    kind: ActorKind
    id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ACTOR_KINDS:
            raise UnsafeAuditEvent("Unknown actor kind")
        if (self.kind in PERSON_KINDS) != (self.id is not None):
            raise UnsafeAuditEvent("Clients and staff need an ID; other actors must not have one")
        if self.id is not None and PUSH_ID.fullmatch(self.id) is None:
            raise UnsafeAuditEvent("Actor ID must be a push ID")

    @classmethod
    def client(cls, client_id: str) -> AuditActor:
        return cls("client", client_id)

    @classmethod
    def staff(cls, staff_id: str) -> AuditActor:
        return cls("staff", staff_id)

    @classmethod
    def system(cls) -> AuditActor:
        return cls("system")

    @classmethod
    def provider(cls) -> AuditActor:
        return cls("provider")

    @classmethod
    def anonymous(cls) -> AuditActor:
        return cls("anonymous")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """What a feature asks to be recorded. `details` and `reason` are redacted when stored."""

    category: AuditCategory
    action: str
    resource_type: str
    actor: AuditActor
    resource_id: str | None = None
    outcome: AuditOutcome = "succeeded"
    reason: str | None = None
    correlation_id: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise UnsafeAuditEvent("Unknown audit category")
        if self.outcome not in OUTCOMES:
            raise UnsafeAuditEvent("Unknown audit outcome")
        if not isinstance(self.actor, AuditActor):
            raise UnsafeAuditEvent("An audit event needs an actor")
        if not ACTION.fullmatch(self.action) or len(self.action) > 100:
            raise UnsafeAuditEvent("Action must be a dotted lowercase name")
        if not RESOURCE_TYPE.fullmatch(self.resource_type):
            raise UnsafeAuditEvent("Resource type must be a lowercase name")
        if self.resource_id is not None and not safe_identifier(self.resource_id, RESOURCE_ID):
            raise UnsafeAuditEvent("Resource ID must be an opaque identifier, not personal data")
        if self.correlation_id is not None and not CORRELATION_ID.fullmatch(self.correlation_id):
            raise UnsafeAuditEvent("Correlation ID must be an opaque identifier")
        if self.reason is not None and not self.reason.strip():
            raise UnsafeAuditEvent("A reason cannot be blank")
        if self.category in REASON_REQUIRED and self.reason is None:
            raise UnsafeAuditEvent("Approvals and operator controls need a reason")
        if not isinstance(self.details, Mapping):
            raise UnsafeAuditEvent("Audit details must be a mapping")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """A validated, redacted event ready to store."""

    id: str
    occurred_at: datetime
    event: AuditEvent
    reason: str | None
    details: dict[str, DetailValue]


def build_record(event: AuditEvent, *, record_id: str, occurred_at: datetime) -> AuditRecord:
    if occurred_at.tzinfo is None:
        raise UnsafeAuditEvent("Audit times must be timezone-aware")
    if PUSH_ID.fullmatch(record_id) is None:
        raise UnsafeAuditEvent("Audit record ID must be a push ID")
    reason = None
    if event.reason is not None:
        reason = redact_text(event.reason.strip(), limit=MAX_REASON_LENGTH)
    return AuditRecord(
        id=record_id,
        occurred_at=occurred_at,
        event=event,
        reason=reason,
        details=redact_details(event.details),
    )


def redact_details(details: Mapping[str, object]) -> dict[str, DetailValue]:
    """A bounded, JSON-compatible copy with sensitive keys and values masked."""
    return _redact_mapping(details, depth=1)


def redact_text(value: str, *, limit: int = MAX_STRING_LENGTH) -> str:
    """Replace personal data and credentials inside a string, then bound its length."""
    kept: list[str] = []

    def set_aside(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    redacted = PROTECTED.sub(set_aside, value.replace("\x00", ""))
    for pattern, replacement in VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    redacted = re.sub(r"\x00(\d+)\x00", lambda match: kept[int(match.group(1))], redacted)
    if len(redacted) > limit:
        redacted = redacted[: limit - 1] + "…"
    return redacted


def sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if any(phrase in normalized for phrase in SENSITIVE_KEY_PHRASES):
        return True
    return not SENSITIVE_KEY_WORDS.isdisjoint(normalized.split("_"))


def safe_identifier(value: str, pattern: re.Pattern[str]) -> bool:
    return bool(pattern.fullmatch(value)) and redact_text(value) == value


def _redact_mapping(value: Mapping[str, object], depth: int) -> dict[str, DetailValue]:
    result: dict[str, DetailValue] = {}
    for index, (raw_key, item) in enumerate(value.items()):
        if index >= MAX_DETAIL_KEYS:
            result["truncated"] = True
            break
        key = str(raw_key)
        if not DETAIL_KEY.fullmatch(key):
            # An unsafe key could itself carry data; keep only the fact that it was dropped.
            result["dropped_keys"] = True
            continue
        if sensitive_key(key):
            result[key] = REDACTED
            continue
        result[key] = _redact_value(item, depth)
    return result


def _redact_value(value: object, depth: int) -> DetailValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        # Integers are kept: a changed limit or rate is exactly what the trail must show.
        # Phone and account numbers are strings, and are caught as strings.
        return value if abs(value) <= MAX_INTEGER else REDACTED
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str):
        return redact_text(value)
    if depth >= MAX_DETAIL_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        return _redact_mapping(value, depth + 1)
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else list(value)
        return [_redact_value(item, depth + 1) for item in items[:MAX_LIST_ITEMS]]
    # Floats and unknown objects are never stored: there are no floats in this codebase, and an
    # unknown object could serialise anything.
    return REDACTED
