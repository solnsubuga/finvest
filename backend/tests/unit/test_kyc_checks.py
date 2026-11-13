"""The identity check rules: who may move a check, what a document needs, valid details."""

from dataclasses import replace
from datetime import date
from itertools import product

import pytest

from app.modules.kyc.domain.checks import (
    FINAL,
    CheckStatus,
    DocumentType,
    IdentityDetails,
    PhotoKind,
    always_needs_person,
    can_move,
    detail_problems,
    missing_for_submission,
    required_photos,
    retake_problem,
    status_on_submit,
)
from app.modules.kyc.domain.provider import (
    AutomaticCheck,
    CheckOutcome,
    Verdict,
    decide,
)

TODAY = date(2026, 11, 13)
S = CheckStatus
ALLOWED = {
    ("client", S.DRAFT, S.SUBMITTED),
    ("client", S.DRAFT, S.NEEDS_REVIEW),
    ("client", S.AWAITING_CLIENT, S.SUBMITTED),
    ("client", S.AWAITING_CLIENT, S.NEEDS_REVIEW),
    ("system", S.SUBMITTED, S.APPROVED),
    ("system", S.SUBMITTED, S.NEEDS_REVIEW),
    ("staff", S.NEEDS_REVIEW, S.APPROVED),
    ("staff", S.NEEDS_REVIEW, S.REJECTED),
    ("staff", S.NEEDS_REVIEW, S.AWAITING_CLIENT),
    ("staff", S.NEEDS_REVIEW, S.WITH_COMPLIANCE),
    ("staff", S.WITH_COMPLIANCE, S.APPROVED),
    ("staff", S.WITH_COMPLIANCE, S.REJECTED),
    ("staff", S.WITH_COMPLIANCE, S.AWAITING_CLIENT),
}


@pytest.mark.parametrize(
    ("actor", "current", "target"), list(product(("client", "staff", "system"), S, S))
)
def test_only_the_listed_moves_are_allowed(actor: str, current: S, target: S) -> None:
    assert can_move(actor, current, target) is ((actor, current, target) in ALLOWED)  # type: ignore[arg-type]


def test_nobody_moves_a_decided_check_and_a_client_never_approves() -> None:
    assert not any(current in FINAL for _, current, _ in ALLOWED)
    assert not any(actor == "client" and target in FINAL for actor, _, target in ALLOWED)


def test_a_passport_needs_no_back_photo() -> None:
    assert required_photos(DocumentType.PASSPORT) == {PhotoKind.FRONT, PhotoKind.SELFIE}
    for document_type in set(DocumentType) - {DocumentType.PASSPORT}:
        assert required_photos(document_type) == set(PhotoKind)


@pytest.mark.parametrize("document_type", DocumentType)
def test_a_check_goes_to_a_person_unless_something_automatic_applies(
    document_type: DocumentType,
) -> None:
    assert status_on_submit(document_type, automatic_checks=False) is S.NEEDS_REVIEW
    expected = S.NEEDS_REVIEW if always_needs_person(document_type) else S.SUBMITTED
    assert status_on_submit(document_type, automatic_checks=True) is expected
    assert always_needs_person(document_type) is (document_type is DocumentType.REFUGEE_ID)


def test_missing_lists_absent_photos_then_details() -> None:
    have = frozenset({PhotoKind.FRONT})
    assert missing_for_submission(DocumentType.NATIONAL_ID, have, details_confirmed=False) == [
        "back",
        "selfie",
        "details",
    ]
    assert missing_for_submission(DocumentType.PASSPORT, have, details_confirmed=True) == ["selfie"]
    assert not missing_for_submission(
        DocumentType.PASSPORT,
        frozenset({PhotoKind.FRONT, PhotoKind.SELFIE}),
        details_confirmed=True,
    )


def test_staff_can_only_ask_for_photos_the_document_has() -> None:
    assert retake_problem(DocumentType.PASSPORT, frozenset()) == "no_photos_named"
    assert (
        retake_problem(DocumentType.PASSPORT, frozenset({PhotoKind.BACK}))
        == "photo_not_part_of_document"
    )
    assert retake_problem(DocumentType.NATIONAL_ID, frozenset({PhotoKind.BACK})) is None


GOOD = IdentityDetails(
    surname="NAKATO",
    given_names="Amara Grace",
    document_number="CF94123456782KX",
    date_of_birth=date(1994, 6, 22),
    document_expires_on=date(2030, 9, 18),
    tin="1000123456",
)


def test_good_details_have_no_problems() -> None:
    assert detail_problems(GOOD, TODAY) == []
    assert detail_problems(replace(GOOD, tin=None, surname="O'Brien-Okello"), TODAY) == []


def test_details_are_normalised_before_they_are_checked() -> None:
    typed = replace(
        GOOD,
        surname="  NAKATO ",
        given_names="Amara   Grace",
        document_number="cf94 1234-5678 2kx",
        tin="1000 123 456",
    )
    assert typed.normalised() == GOOD
    assert replace(GOOD, tin="  ").normalised().tin is None


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        ({"surname": ""}, "surname", "invalid_name"),
        ({"surname": "N4KATO"}, "surname", "invalid_name"),
        ({"given_names": "A" * 101}, "given_names", "invalid_name"),
        ({"document_number": "AB1"}, "document_number", "invalid_document_number"),
        ({"document_number": "CF94-1234"}, "document_number", "invalid_document_number"),
        ({"date_of_birth": date(2026, 11, 14)}, "date_of_birth", "in_the_future"),
        ({"date_of_birth": date(2008, 11, 14)}, "date_of_birth", "under_age"),
        ({"document_expires_on": date(2026, 11, 12)}, "document_expires_on", "document_expired"),
        ({"tin": "12345"}, "tin", "invalid_tin"),
    ],
)
def test_each_bad_detail_is_named(change: dict[str, object], field: str, code: str) -> None:
    problems = detail_problems(replace(GOOD, **change), TODAY)  # type: ignore[arg-type]
    assert [(problem.field, problem.code) for problem in problems] == [(field, code)]


def test_a_client_is_an_adult_on_their_eighteenth_birthday_and_an_id_is_good_on_its_last_day() -> (
    None
):
    assert detail_problems(replace(GOOD, date_of_birth=date(2008, 11, 13)), TODAY) == []
    assert detail_problems(replace(GOOD, document_expires_on=TODAY), TODAY) == []


def passed(*checks: AutomaticCheck) -> tuple[CheckOutcome, ...]:
    return tuple(CheckOutcome(check, Verdict.PASS) for check in checks)


def test_every_check_passing_approves() -> None:
    decision = decide(passed(*AutomaticCheck))
    assert decision.approve
    assert decision.flags == ()


@pytest.mark.parametrize("check", AutomaticCheck)
@pytest.mark.parametrize("verdict", [Verdict.FAIL, Verdict.UNSURE, None])
def test_any_check_that_did_not_pass_sends_the_client_to_a_person(
    check: AutomaticCheck, verdict: Verdict | None
) -> None:
    outcomes = passed(*(other for other in AutomaticCheck if other is not check))
    if verdict is not None:
        outcomes += (CheckOutcome(check, verdict, 62),)
    decision = decide(outcomes)
    assert not decision.approve
    assert decision.flags == (f"{check.value}_{verdict.value if verdict else 'not_run'}",)


def test_no_checks_at_all_never_approves() -> None:
    assert not decide(()).approve


def test_a_check_reported_twice_is_not_trusted() -> None:
    outcomes = (*passed(*AutomaticCheck), CheckOutcome(AutomaticCheck.SCREENING, Verdict.FAIL))
    assert decide(outcomes) == decide(outcomes[::-1])
    assert not decide(outcomes).approve


@pytest.mark.parametrize("score", [-1, 101, True])
def test_a_score_is_a_whole_percentage(score: int) -> None:
    with pytest.raises(ValueError, match="whole percentage"):
        CheckOutcome(AutomaticCheck.SELFIE_MATCH, Verdict.UNSURE, score)
