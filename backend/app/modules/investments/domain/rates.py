"""Product rates and how they change. Pure: no I/O.

A rate is a dated version: the yearly rate before tax, the tax on interest, and the day it
starts. A change is proposed by one member of staff and approved by another, and takes effect
on a later day, so a rate never changes under a day that has already begun.

A rate change only affects investments that start on or after its effective date. A running
investment keeps the terms it started with.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.investments.domain.interest import BASIS_POINTS, InvalidTerms, check_rate

MAX_REASON_LENGTH = 500


class InvalidRate(ValueError):
    """A rate, or a set of rates, that cannot be used."""


@dataclass(frozen=True, slots=True)
class RateVersion:
    """One approved rate for one product, in force from `effective_from` until the next one."""

    effective_from: date
    annual_rate_bps: int
    tax_bps: int

    def __post_init__(self) -> None:
        if type(self.effective_from) is not date:
            raise InvalidRate("The effective date is a calendar date")
        try:
            check_rate(self.annual_rate_bps, self.tax_bps)
        except InvalidTerms as error:
            raise InvalidRate(str(error)) from error

    @property
    def client_rate_percent(self) -> str:
        """The yearly rate after tax, as clients see it: "10.63" for 12.5% with 15% tax.

        Two decimal places, rounded to the nearest. It is a label only. No amount is ever
        worked out from it: amounts come from `interest.accrued`.
        """
        # In millionths of one percent, so the division below is exact integer arithmetic.
        millionths = self.annual_rate_bps * (BASIS_POINTS - self.tax_bps)
        hundredths, remainder = divmod(millionths, BASIS_POINTS)
        if remainder * 2 >= BASIS_POINTS:
            hundredths += 1
        return f"{hundredths // 100}.{hundredths % 100:02d}"


def rate_in_force(versions: Iterable[RateVersion], on: date) -> RateVersion | None:
    """The version with the latest effective date that is not after `on`, if there is one."""
    in_force: RateVersion | None = None
    seen: set[date] = set()
    for version in versions:
        if version.effective_from in seen:
            raise InvalidRate("Two rates for one product share an effective date")
        seen.add(version.effective_from)
        if version.effective_from <= on and (
            in_force is None or version.effective_from > in_force.effective_from
        ):
            in_force = version
    return in_force


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    # The second person declined it, with a note. The proposer starts a new one.
    SENT_BACK = "sent_back"
    # The proposer took it back before anyone decided.
    WITHDRAWN = "withdrawn"

    @property
    def is_final(self) -> bool:
        return self is not ProposalStatus.PROPOSED


class Refusal(StrEnum):
    REASON_REQUIRED = "reason_required"
    # The change must start on a later day than the one it is proposed or approved on.
    EFFECTIVE_DATE_NOT_IN_FUTURE = "effective_date_not_in_future"
    # It is the rate already in force on that date.
    NOTHING_CHANGES = "nothing_changes"
    ALREADY_DECIDED = "already_decided"
    # Nobody approves, or sends back, their own proposal.
    SAME_PERSON = "same_person"
    # Only the proposer withdraws a proposal.
    NOT_THE_PROPOSER = "not_the_proposer"
    # Another approved rate already starts on that date.
    DATE_ALREADY_TAKEN = "date_already_taken"


@dataclass(frozen=True, slots=True)
class RateProposal:
    rate: RateVersion
    proposed_by: str
    reason: str
    status: ProposalStatus = ProposalStatus.PROPOSED


def check_proposal(
    rate: RateVersion, reason: str, existing: Iterable[RateVersion], today: date
) -> Refusal | None:
    """Whether a new proposal may be saved. `existing` is the product's approved rates."""
    if not valid_reason(reason):
        return Refusal.REASON_REQUIRED
    if rate.effective_from <= today:
        return Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE
    return _clash(rate, list(existing))


def check_approval(
    proposal: RateProposal, approver: str, existing: Iterable[RateVersion], today: date
) -> Refusal | None:
    """Whether `approver` may approve the proposal today.

    The date and the clash with other rates are checked again here, because days may pass and
    other changes may be approved between the proposal and its approval.
    """
    if proposal.status.is_final:
        return Refusal.ALREADY_DECIDED
    if approver == proposal.proposed_by:
        return Refusal.SAME_PERSON
    if proposal.rate.effective_from <= today:
        return Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE
    return _clash(proposal.rate, list(existing))


def check_send_back(proposal: RateProposal, by: str, note: str) -> Refusal | None:
    if proposal.status.is_final:
        return Refusal.ALREADY_DECIDED
    if by == proposal.proposed_by:
        return Refusal.SAME_PERSON
    return None if valid_reason(note) else Refusal.REASON_REQUIRED


def check_withdrawal(proposal: RateProposal, by: str) -> Refusal | None:
    if proposal.status.is_final:
        return Refusal.ALREADY_DECIDED
    return None if by == proposal.proposed_by else Refusal.NOT_THE_PROPOSER


def valid_reason(text: str) -> bool:
    return isinstance(text, str) and len(text.strip()) > 0 and len(text) <= MAX_REASON_LENGTH


def _clash(rate: RateVersion, existing: list[RateVersion]) -> Refusal | None:
    if any(version.effective_from == rate.effective_from for version in existing):
        return Refusal.DATE_ALREADY_TAKEN
    before = rate_in_force(existing, rate.effective_from)
    if (
        before is not None
        and before.annual_rate_bps == rate.annual_rate_bps
        and before.tax_bps == rate.tax_bps
    ):
        return Refusal.NOTHING_CHANGES
    return None


# The rates Finvest 1.0 sold at, by product code: yearly rate and tax on interest.
SEED_RATES: dict[str, tuple[int, int]] = {
    "TREA-91": (1250, 1500),
    "TREA-182": (1375, 1500),
    "USD-BOND": (725, 500),
}
