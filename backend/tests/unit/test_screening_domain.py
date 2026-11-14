"""Screening rules: what a screen found, where a client stands, and who closes a case."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.screening.domain.cases import (
    OUTCOMES,
    CaseOutcome,
    CaseSource,
    Decision,
    may_decide,
    outcome_allowed,
)
from app.modules.screening.domain.provider import Match, MatchCategory, ScreeningSubject
from app.modules.screening.domain.standing import (
    RESCREEN_EVERY,
    LastRun,
    Outcome,
    RunStatus,
    due_for_rescreen,
    standing,
    what_was_found,
)

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def match(entity_id: str = "e-1", category: MatchCategory = MatchCategory.SANCTIONS) -> Match:
    return Match(entity_id=entity_id, name="Listed Person", category=category, strength=90)


def test_nothing_found_is_clear() -> None:
    found = what_was_found([], frozenset())
    assert found.status is RunStatus.CLEAR and found.new == ()


def test_a_match_is_possible_and_new_unless_already_decided() -> None:
    first, second = match("e-1"), match("e-2", MatchCategory.PEP)
    found = what_was_found([first, second], frozenset({"e-1"}))
    assert found.status is RunStatus.POSSIBLE_MATCH
    assert found.new == (second,)
    again = what_was_found([first], frozenset({"e-1"}))
    assert again.status is RunStatus.POSSIBLE_MATCH and again.new == ()


def run(status: RunStatus) -> LastRun:
    return LastRun(status, NOW, None if status is RunStatus.PENDING else NOW)


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (RunStatus.PENDING, Outcome.PENDING),
        (RunStatus.CLEAR, Outcome.CLEAR),
        # Nothing new in it: every entity was already ruled out or accepted.
        (RunStatus.POSSIBLE_MATCH, Outcome.CLEAR),
        (RunStatus.UNAVAILABLE, Outcome.UNAVAILABLE),
        (RunStatus.NOT_RUN, Outcome.NOT_RUN),
        (RunStatus.NO_SUBJECT, Outcome.NOT_RUN),
    ],
)
def test_the_last_screen_speaks_when_no_case_does(status: RunStatus, outcome: Outcome) -> None:
    assert standing(run(status), open_case=False, sanctioned=False) is outcome


@pytest.mark.parametrize("status", list(RunStatus))
def test_a_confirmed_sanctions_match_outweighs_everything(status: RunStatus) -> None:
    assert standing(run(status), open_case=True, sanctioned=True) is Outcome.CONFIRMED_MATCH
    assert standing(None, open_case=False, sanctioned=True) is Outcome.CONFIRMED_MATCH


@pytest.mark.parametrize("status", list(RunStatus))
def test_an_open_case_is_a_possible_match_whatever_the_last_screen(status: RunStatus) -> None:
    assert standing(run(status), open_case=True, sanctioned=False) is Outcome.POSSIBLE_MATCH


def test_a_client_never_screened_has_no_standing() -> None:
    assert standing(None, open_case=False, sanctioned=False) is None


def test_rescreen_is_due_a_month_after_the_last_finished_screen() -> None:
    assert due_for_rescreen(None, NOW)
    assert not due_for_rescreen(NOW - RESCREEN_EVERY + timedelta(seconds=1), NOW)
    assert due_for_rescreen(NOW - RESCREEN_EVERY, NOW)


def test_outcomes_fit_their_source_only() -> None:
    for source, outcomes in OUTCOMES.items():
        for outcome in CaseOutcome:
            assert outcome_allowed(source, outcome) is (outcome in outcomes)
    assert OUTCOMES[CaseSource.SCREENING].isdisjoint(OUTCOMES[CaseSource.MONITORING])
    assert OUTCOMES[CaseSource.SCREENING] | OUTCOMES[CaseSource.MONITORING] == set(CaseOutcome)


def test_only_the_proposer_withdraws_and_only_someone_else_decides() -> None:
    assert may_decide(proposer="a", decider="a", decision=Decision.WITHDRAWN)
    assert not may_decide(proposer="a", decider="b", decision=Decision.WITHDRAWN)
    for decision in (Decision.APPROVED, Decision.SENT_BACK):
        assert may_decide(proposer="a", decider="b", decision=decision)
        assert not may_decide(proposer="a", decider="a", decision=decision)


def test_a_match_strength_is_a_whole_percentage() -> None:
    for strength in (-1, 101):
        with pytest.raises(ValueError, match="percentage"):
            Match(entity_id="e", name="n", category=MatchCategory.PEP, strength=strength)
    with pytest.raises(ValueError, match="percentage"):
        Match(entity_id="e", name="n", category=MatchCategory.PEP, strength=0.9)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="entity"):
        Match(entity_id="", name="n", category=MatchCategory.PEP, strength=1)


def test_a_subject_has_a_name_and_a_two_letter_country() -> None:
    subject = ScreeningSubject(" NAKATO ", "Amara Grace", date(1994, 6, 22), "UG")
    assert subject.full_name == "Amara Grace NAKATO"
    with pytest.raises(ValueError, match="name"):
        ScreeningSubject(" ", "", None, None)
    with pytest.raises(ValueError, match="country"):
        ScreeningSubject("NAKATO", "Amara", None, "UGA")
