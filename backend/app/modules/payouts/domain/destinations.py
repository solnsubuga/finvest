"""Where a client can be paid: their own mobile money number or bank account. Pure: no I/O.

A destination is usable only after four things, in order: the client confirms it with an SMS
code, uploads proof that it is theirs, a staff member checks the proof against the name on the
client's ID, and 24 hours pass after that approval. Nothing here trusts the client app: every
step is a transition that the service applies and the database stores.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum

SAFETY_WAIT = timedelta(hours=24)
# A statement proves ownership only while it is recent.
STATEMENT_MAX_AGE_MONTHS = 3

_MSISDN = re.compile(r"256[0-9]{9}")
_TYPED_PHONE = re.compile(r"\+?[0-9]+( [0-9]+)*")
_BANK_CODE = re.compile(r"[a-z][a-z0-9_]{1,31}")
_BANK_ACCOUNT_NUMBER = re.compile(r"[0-9]{6,20}")
_TYPED_ACCOUNT_NUMBER = re.compile(r"[0-9]+([ -][0-9]+)*")
# O'Brien is one name, however the apostrophe was typed.
_APOSTROPHES = str.maketrans("", "", "'\N{RIGHT SINGLE QUOTATION MARK}")


class DestinationKind(StrEnum):
    MOBILE_MONEY = "mobile_money"
    BANK = "bank"


class MobileNetwork(StrEnum):
    MTN = "mtn"
    AIRTEL = "airtel"


# The two digits after 256. Uganda has no number portability, so the prefix names the network.
_NETWORK_PREFIXES: dict[str, MobileNetwork] = {
    "76": MobileNetwork.MTN,
    "77": MobileNetwork.MTN,
    "78": MobileNetwork.MTN,
    "39": MobileNetwork.MTN,
    "70": MobileNetwork.AIRTEL,
    "74": MobileNetwork.AIRTEL,
    "75": MobileNetwork.AIRTEL,
    "20": MobileNetwork.AIRTEL,
}


class InvalidDestination(ValueError):
    """The details do not describe a mobile money number or bank account we can pay."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class MobileMoneyNumber:
    """A Ugandan mobile money number, stored as 12 digits starting 256."""

    msisdn: str

    def __post_init__(self) -> None:
        if not isinstance(self.msisdn, str) or _MSISDN.fullmatch(self.msisdn) is None:
            raise InvalidDestination("phone_invalid")
        if self.msisdn[3:5] not in _NETWORK_PREFIXES:
            raise InvalidDestination("phone_network_unsupported")

    @classmethod
    def parse(cls, typed: str) -> MobileMoneyNumber:
        """Accept 0772123456, 772123456, 256772123456 or +256 772 123 456."""
        if not isinstance(typed, str) or _TYPED_PHONE.fullmatch(typed.strip()) is None:
            raise InvalidDestination("phone_invalid")
        digits = typed.strip().lstrip("+").replace(" ", "")
        if len(digits) == 10 and digits.startswith("0"):
            digits = "256" + digits[1:]
        elif len(digits) == 9:
            digits = "256" + digits
        return cls(digits)

    @property
    def network(self) -> MobileNetwork:
        return _NETWORK_PREFIXES[self.msisdn[3:5]]

    @property
    def masked(self) -> str:
        """Safe to show back to the client: +256 77• ••• 456."""
        return f"+256 {self.msisdn[3:5]}• ••• {self.msisdn[-3:]}"


@dataclass(frozen=True, slots=True)
class BankAccount:
    """A bank account. `bank_code` names a bank in the list the service keeps."""

    bank_code: str
    account_number: str

    def __post_init__(self) -> None:
        if not isinstance(self.bank_code, str) or _BANK_CODE.fullmatch(self.bank_code) is None:
            raise InvalidDestination("bank_invalid")
        if (
            not isinstance(self.account_number, str)
            or _BANK_ACCOUNT_NUMBER.fullmatch(self.account_number) is None
        ):
            raise InvalidDestination("bank_account_number_invalid")

    @classmethod
    def parse(cls, bank_code: str, typed_account_number: str) -> BankAccount:
        """Accept the account number with single spaces or hyphens between groups."""
        if (
            not isinstance(typed_account_number, str)
            or _TYPED_ACCOUNT_NUMBER.fullmatch(typed_account_number.strip()) is None
        ):
            raise InvalidDestination("bank_account_number_invalid")
        return cls(bank_code, re.sub(r"[ -]", "", typed_account_number.strip()))

    @property
    def masked(self) -> str:
        """Safe to show back to the client: only the last four digits."""
        return "•••• " + self.account_number[-4:]


# ---------------------------------------------------------------------------------------------
# The name lock
# ---------------------------------------------------------------------------------------------


class NameMatch(StrEnum):
    """How the name on the proof compares with the name on the client's ID.

    This is a hint for the staff member who checks the proof. It never approves anything.
    """

    EXACT = "exact"  # the same names, in any order
    PARTIAL = "partial"  # one is the other with a name missing; a person must decide
    DIFFERENT = "different"


def name_tokens(name: str) -> frozenset[str]:
    """The names in a full name, without case, accents, punctuation or order."""
    if not isinstance(name, str):
        raise TypeError("A name is text")
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return frozenset(re.findall(r"[a-z0-9]+", plain.translate(_APOSTROPHES)))


def compare_names(id_name: str, proof_name: str) -> NameMatch:
    """Compare the ID name with the name on a statement, letter, card or network record.

    Ugandan documents put the surname first or last, so order is ignored. Two things make a
    `PARTIAL` match, which only a person can accept: a missing middle name, when at least two
    names agree, and a name spelt slightly differently (Mohammed and Muhammad), when every
    other name agrees exactly. A single shared name is never enough.
    """
    on_id, on_proof = name_tokens(id_name), name_tokens(proof_name)
    if not on_id or not on_proof:
        return NameMatch.DIFFERENT
    if on_id == on_proof:
        return NameMatch.EXACT
    shared = on_id & on_proof
    if len(shared) >= 2 and shared in (on_id, on_proof):
        return NameMatch.PARTIAL
    if shared and len(on_id) == len(on_proof) and _spelt_alike(on_id - shared, on_proof - shared):
        return NameMatch.PARTIAL
    return NameMatch.DIFFERENT


# A spelling variant: both names long enough to mean something, and at most two letters apart.
_VARIANT_MIN_LENGTH = 5
_VARIANT_MAX_EDITS = 2


def _spelt_alike(left: frozenset[str], right: frozenset[str]) -> bool:
    """True if the leftover names pair off one to one as spelling variants of each other."""
    if not left:
        return not right
    first = min(left)
    return any(
        _is_variant(first, other) and _spelt_alike(left - {first}, right - {other})
        for other in sorted(right)
    )


def _is_variant(one: str, other: str) -> bool:
    if min(len(one), len(other)) < _VARIANT_MIN_LENGTH or one[0] != other[0]:
        return False
    return _edit_distance(one, other) <= _VARIANT_MAX_EDITS


def _edit_distance(one: str, other: str) -> int:
    previous = list(range(len(other) + 1))
    for row, a in enumerate(one, start=1):
        current = [row]
        for column, b in enumerate(other, start=1):
            current.append(
                min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + (a != b))
            )
        previous = current
    return previous[-1]


# ---------------------------------------------------------------------------------------------
# Proof of ownership
# ---------------------------------------------------------------------------------------------


class ProofKind(StrEnum):
    BANK_STATEMENT = "bank_statement"  # dated within the last three months
    BANK_LETTER = "bank_letter"
    BANK_CARD = "bank_card"  # a photo of the card, showing the name
    # The name the network holds for the number, read through the payment provider.
    NETWORK_REGISTERED_NAME = "network_registered_name"


_PROOF_FOR: dict[DestinationKind, frozenset[ProofKind]] = {
    DestinationKind.BANK: frozenset(
        {ProofKind.BANK_STATEMENT, ProofKind.BANK_LETTER, ProofKind.BANK_CARD}
    ),
    DestinationKind.MOBILE_MONEY: frozenset({ProofKind.NETWORK_REGISTERED_NAME}),
}


def proof_fits(kind: DestinationKind, proof: ProofKind) -> bool:
    return proof in _PROOF_FOR[kind]


def statement_is_recent(statement_date: date, today: date) -> bool:
    """True if the statement is dated within the last three calendar months, and not ahead."""
    if statement_date > today:
        return False
    month_index = today.year * 12 + (today.month - 1) - STATEMENT_MAX_AGE_MONTHS
    year, month = divmod(month_index, 12)
    month += 1
    # 31 May minus three months is the last day of February, not an invalid date.
    day = min(today.day, _days_in_month(year, month))
    return statement_date >= date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    first_of_next = date(year + month // 12, month % 12 + 1, 1)
    return (first_of_next - timedelta(days=1)).day


# ---------------------------------------------------------------------------------------------
# The life of a destination
# ---------------------------------------------------------------------------------------------


class DestinationStatus(StrEnum):
    AWAITING_CODE = "awaiting_code"  # added, SMS code not yet confirmed
    AWAITING_PROOF = "awaiting_proof"  # confirmed, no proof uploaded yet
    IN_REVIEW = "in_review"  # proof uploaded, waiting for staff
    APPROVED = "approved"  # staff checked it; payable once the safety wait has passed
    REJECTED = "rejected"  # staff refused the proof; the client may upload another
    REMOVED = "removed"  # taken off by the client or by staff; never used again


class RejectionReason(StrEnum):
    NAME_DIFFERENT = "name_different"
    PROOF_UNREADABLE = "proof_unreadable"
    PROOF_TOO_OLD = "proof_too_old"
    PROOF_WRONG_ACCOUNT = "proof_wrong_account"
    PROOF_WRONG_DOCUMENT = "proof_wrong_document"
    SUSPECTED_FRAUD = "suspected_fraud"


class DestinationError(Exception):
    """A step that the destination's current state does not allow."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProofCheck:
    """What the staff member recorded when approving. All of it goes to the audit trail."""

    proof: ProofKind
    name_match: NameMatch
    # Required for a statement: the date printed on it.
    statement_date: date | None = None


@dataclass(frozen=True, slots=True)
class Destination:
    """The state the rules need. Account details live beside it in the service's row."""

    kind: DestinationKind
    status: DestinationStatus
    approved_at: datetime | None = None
    first_paid_at: datetime | None = None
    rejection_reason: RejectionReason | None = None

    @classmethod
    def new(cls, kind: DestinationKind) -> Destination:
        return cls(kind=kind, status=DestinationStatus.AWAITING_CODE)


def confirm_code(destination: Destination) -> Destination:
    """The client entered the SMS code we sent when they added the destination."""
    _require(destination, DestinationStatus.AWAITING_CODE)
    return replace(destination, status=DestinationStatus.AWAITING_PROOF)


def submit_proof(destination: Destination, proof: ProofKind) -> Destination:
    """The client uploaded proof, or uploaded a better one after a rejection."""
    if destination.status not in (DestinationStatus.AWAITING_PROOF, DestinationStatus.REJECTED):
        raise DestinationError("destination_not_awaiting_proof")
    if destination.rejection_reason is RejectionReason.SUSPECTED_FRAUD:
        # Final. A better document does not answer a suspicion of fraud.
        raise DestinationError("destination_rejected_final")
    if not proof_fits(destination.kind, proof):
        raise DestinationError("proof_kind_not_accepted")
    return replace(destination, status=DestinationStatus.IN_REVIEW, rejection_reason=None)


def approve(destination: Destination, check: ProofCheck, now: datetime) -> Destination:
    """A staff member checked the proof against the ID name. Starts the 24-hour safety wait.

    Refused unless the names are the same or the staff member judged a partial match to be the
    same person. A different name can never be approved, whoever asks.
    """
    _require(destination, DestinationStatus.IN_REVIEW)
    _aware(now)
    if not proof_fits(destination.kind, check.proof):
        raise DestinationError("proof_kind_not_accepted")
    if check.name_match is NameMatch.DIFFERENT:
        raise DestinationError("name_different")
    if check.proof is ProofKind.BANK_STATEMENT:
        if check.statement_date is None:
            raise DestinationError("statement_date_required")
        if not statement_is_recent(check.statement_date, now.date()):
            raise DestinationError("statement_too_old")
    return replace(destination, status=DestinationStatus.APPROVED, approved_at=now)


def reject(destination: Destination, reason: RejectionReason) -> Destination:
    _require(destination, DestinationStatus.IN_REVIEW)
    return replace(destination, status=DestinationStatus.REJECTED, rejection_reason=reason)


def remove(destination: Destination) -> Destination:
    """Take the destination out of use. Allowed from any state except already removed."""
    if destination.status is DestinationStatus.REMOVED:
        raise DestinationError("destination_removed")
    return replace(destination, status=DestinationStatus.REMOVED)


def record_first_payout(destination: Destination, paid_at: datetime) -> Destination:
    """Remember when money first arrived there. Later payouts leave it unchanged."""
    _require(destination, DestinationStatus.APPROVED)
    _aware(paid_at)
    if destination.first_paid_at is not None:
        return destination
    return replace(destination, first_paid_at=paid_at)


def payable_from(destination: Destination) -> datetime | None:
    """When the safety wait ends, or None if the destination is not approved."""
    if destination.status is not DestinationStatus.APPROVED or destination.approved_at is None:
        return None
    return destination.approved_at + SAFETY_WAIT


class NotPayable(StrEnum):
    NOT_APPROVED = "destination_not_approved"
    SAFETY_WAIT = "destination_in_safety_wait"
    # The client signed in with a code, or reset their password, in the last 24 hours, and
    # this destination had not been paid before that.
    SESSION_RESTRICTED = "destination_not_known_yet"


def why_not_payable(
    destination: Destination, now: datetime, *, restricted_since: datetime | None = None
) -> NotPayable | None:
    """None if a payout may be sent there now, otherwise the reason it may not.

    `restricted_since` is when the client's current sign-in restriction began, if one is in
    force. Under it only a destination that had already received money counts as known.
    """
    _aware(now)
    ready_at = payable_from(destination)
    if ready_at is None:
        return NotPayable.NOT_APPROVED
    if now < ready_at:
        return NotPayable.SAFETY_WAIT
    if restricted_since is not None:
        _aware(restricted_since)
        first_paid = destination.first_paid_at
        if first_paid is None or first_paid >= restricted_since:
            return NotPayable.SESSION_RESTRICTED
    return None


def _require(destination: Destination, status: DestinationStatus) -> None:
    if destination.status is not status:
        raise DestinationError(f"destination_not_{status.value}")


def _aware(moment: datetime) -> None:
    if moment.utcoffset() is None:
        raise ValueError("Times must be timezone-aware")
