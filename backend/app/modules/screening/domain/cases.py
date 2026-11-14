"""Compliance cases and how staff close them. Pure: no I/O.

A case is opened by the system: a screen found a listed entity that may be the client, or a
monitoring rule saw something unusual. Staff close it with two people, from the owner's standing
maker and checker rule: one proposes the outcome with a reason, a different one approves it or
sends it back with a note, and the proposer may withdraw their own. Migration 0180 holds the
same rule in PostgreSQL.
"""

from __future__ import annotations

from enum import StrEnum

MAX_TEXT = 1000


class CaseSource(StrEnum):
    SCREENING = "screening"
    MONITORING = "monitoring"


class CaseStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class CaseOutcome(StrEnum):
    # Screening.
    NOT_A_MATCH = "not_a_match"  # the listed entity is someone else
    # The client is a politically exposed person (or family or close associate) and stays a
    # client, with enhanced due diligence recorded in the reason.
    PEP_ACCEPTED = "pep_accepted"
    # The client is on a sanctions list. Payouts stay held and a report is owed to the
    # Financial Intelligence Authority.
    SANCTIONED = "sanctioned"
    # Monitoring.
    NO_CONCERN = "no_concern"
    # Suspicious: a suspicious transaction report is owed to the Financial Intelligence Authority.
    REPORT_TO_FIA = "report_to_fia"


OUTCOMES: dict[CaseSource, frozenset[CaseOutcome]] = {
    CaseSource.SCREENING: frozenset(
        {CaseOutcome.NOT_A_MATCH, CaseOutcome.PEP_ACCEPTED, CaseOutcome.SANCTIONED}
    ),
    CaseSource.MONITORING: frozenset({CaseOutcome.NO_CONCERN, CaseOutcome.REPORT_TO_FIA}),
}
# A screening case closed with one of these tells the next screen the entity is already decided.
SETTLES_ENTITY = frozenset({CaseOutcome.NOT_A_MATCH, CaseOutcome.PEP_ACCEPTED})


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"


class Decision(StrEnum):
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"


def outcome_allowed(source: CaseSource, outcome: CaseOutcome) -> bool:
    return outcome in OUTCOMES[source]


def may_decide(*, proposer: str, decider: str, decision: Decision) -> bool:
    """Only the proposer withdraws, and only someone else approves or sends back."""
    return (proposer == decider) == (decision is Decision.WITHDRAWN)
