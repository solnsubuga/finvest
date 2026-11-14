"""The investing questions: four short questions that say what kind of investor a client is.

Each answer carries points. The total gives a profile, and the profile gives the longest term
of investment that suits the client. There are no wrong answers, and nothing here refuses an
investment: the profile is used to suggest products, and is asked again once a year.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Asked again a year after the last answers.
GOOD_FOR = timedelta(days=365)


class Profile(StrEnum):
    # Money the client may need soon: short bills only.
    CAREFUL = "careful"
    # Some can wait: bills up to a year.
    BALANCED = "balanced"
    # Long-term money: bills and bonds of any term.
    LONG_TERM = "long_term"


@dataclass(frozen=True, slots=True)
class Option:
    code: str
    points: int


@dataclass(frozen=True, slots=True)
class Question:
    code: str
    options: tuple[Option, ...]


QUESTIONS: tuple[Question, ...] = (
    Question(
        "purpose",
        (
            Option("emergency_fund", 0),
            Option("planned_purchase", 1),
            Option("grow_savings", 2),
            Option("retirement_or_children", 3),
        ),
    ),
    Question(
        "need_back",
        (
            Option("within_3_months", 0),
            Option("3_to_12_months", 1),
            Option("1_to_3_years", 2),
            Option("over_3_years", 3),
        ),
    ),
    Question(
        "early_need",
        (
            # Asked as: if you needed the money before the end of the term, what would you do?
            Option("would_withdraw_early", 0),
            Option("would_use_other_savings", 2),
            Option("would_wait", 3),
        ),
    ),
    Question(
        "experience",
        (
            Option("none", 0),
            Option("savings_or_sacco", 1),
            Option("bills_or_bonds", 2),
            Option("shares_or_unit_trusts", 2),
        ),
    ),
)
_BY_CODE = {question.code: question for question in QUESTIONS}
# The longest term, in days, of an investment that suits each profile.
LONGEST_TERM_DAYS: dict[Profile, int | None] = {
    Profile.CAREFUL: 182,
    Profile.BALANCED: 364,
    Profile.LONG_TERM: None,
}


class AnswerProblem(ValueError):
    def __init__(self, question: str, code: str) -> None:
        super().__init__(code)
        self.question = question
        self.code = code


def score(answers: dict[str, str]) -> int:
    """The points for a full set of answers. Every question is answered once, from its options."""
    unknown = sorted(set(answers) - set(_BY_CODE))
    if unknown:
        raise AnswerProblem(unknown[0], "unknown_question")
    total = 0
    for question in QUESTIONS:
        chosen = answers.get(question.code)
        if chosen is None:
            raise AnswerProblem(question.code, "required")
        option = next((o for o in question.options if o.code == chosen), None)
        if option is None:
            raise AnswerProblem(question.code, "unknown_answer")
        total += option.points
    return total


def profile_for(answers: dict[str, str]) -> Profile:
    """The profile for a full set of answers.

    Money needed within three months is careful whatever the other answers: the term matters
    more than the client's appetite.
    """
    total = score(answers)
    if answers["need_back"] == "within_3_months" or total <= 3:
        return Profile.CAREFUL
    if total <= 7:
        return Profile.BALANCED
    return Profile.LONG_TERM


def due_again(answered_at: datetime) -> datetime:
    return answered_at + GOOD_FOR


def suits(profile: Profile, term_days: int) -> bool:
    longest = LONGEST_TERM_DAYS[profile]
    return longest is None or term_days <= longest
