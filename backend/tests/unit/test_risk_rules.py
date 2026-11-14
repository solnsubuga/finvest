"""The investing questions: every answer, every profile, and when they are asked again."""

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.kyc.domain.risk import (
    QUESTIONS,
    AnswerProblem,
    Profile,
    due_again,
    profile_for,
    score,
    suits,
)

CAREFUL = {
    "purpose": "emergency_fund",
    "need_back": "within_3_months",
    "early_need": "would_withdraw_early",
    "experience": "none",
}
LONG = {
    "purpose": "retirement_or_children",
    "need_back": "over_3_years",
    "early_need": "would_wait",
    "experience": "bills_or_bonds",
}


def every_answer_set() -> list[dict[str, str]]:
    choices = [[(q.code, o.code) for o in q.options] for q in QUESTIONS]
    return [dict(combination) for combination in itertools.product(*choices)]


def test_the_lowest_and_highest_answers_give_the_two_ends() -> None:
    assert score(CAREFUL) == 0 and profile_for(CAREFUL) is Profile.CAREFUL
    assert score(LONG) == 11 and profile_for(LONG) is Profile.LONG_TERM


def test_every_answer_set_gives_one_profile_that_follows_the_points() -> None:
    for answers in every_answer_set():
        points = score(answers)
        profile = profile_for(answers)
        if answers["need_back"] == "within_3_months" or points <= 3:
            assert profile is Profile.CAREFUL, answers
        elif points <= 7:
            assert profile is Profile.BALANCED, answers
        else:
            assert profile is Profile.LONG_TERM, answers


def test_money_needed_within_three_months_is_always_careful() -> None:
    assert profile_for({**LONG, "need_back": "within_3_months"}) is Profile.CAREFUL


def test_a_middle_set_is_balanced() -> None:
    middle = {**LONG, "need_back": "3_to_12_months", "early_need": "would_withdraw_early"}
    assert score(middle) == 6 and profile_for(middle) is Profile.BALANCED


@pytest.mark.parametrize("question", [q.code for q in QUESTIONS])
def test_every_question_must_be_answered(question: str) -> None:
    answers = {k: v for k, v in LONG.items() if k != question}
    with pytest.raises(AnswerProblem) as raised:
        score(answers)
    assert (raised.value.question, raised.value.code) == (question, "required")


def test_an_answer_must_be_one_of_the_questions_options() -> None:
    with pytest.raises(AnswerProblem) as raised:
        score({**LONG, "purpose": "lottery"})
    assert (raised.value.question, raised.value.code) == ("purpose", "unknown_answer")


def test_an_unknown_question_is_refused() -> None:
    with pytest.raises(AnswerProblem) as raised:
        score({**LONG, "favourite_colour": "green"})
    assert (raised.value.question, raised.value.code) == ("favourite_colour", "unknown_question")


def test_answers_are_due_again_a_year_later() -> None:
    answered = datetime(2026, 11, 14, 9, tzinfo=UTC)
    assert due_again(answered) == answered + timedelta(days=365)


@pytest.mark.parametrize(
    ("profile", "term", "expected"),
    [
        (Profile.CAREFUL, 91, True),
        (Profile.CAREFUL, 182, True),
        (Profile.CAREFUL, 364, False),
        (Profile.BALANCED, 364, True),
        (Profile.BALANCED, 730, False),
        (Profile.LONG_TERM, 3650, True),
    ],
)
def test_which_terms_suit_each_profile(profile: Profile, term: int, expected: bool) -> None:
    assert suits(profile, term) is expected
