"""The rules a company or group application follows before it can be handed in."""

import pytest

from app.modules.organisations.domain.applications import (
    MAX_NAME,
    MAX_ROLE,
    DocumentKind,
    Kind,
    Problem,
    Readiness,
    clean_name,
    clean_registration,
    clean_role,
    document_matches_type,
    problems,
)

COMPANY_DOCUMENTS = frozenset(
    {DocumentKind.CERTIFICATE_OF_INCORPORATION, DocumentKind.BOARD_RESOLUTION}
)
GROUP_DOCUMENTS = frozenset({DocumentKind.CONSTITUTION, DocumentKind.GROUP_RESOLUTION})


def ready(kind: Kind, **changes: object) -> Readiness:
    values: dict[str, object] = {
        "kind": kind,
        "registration_number": "80020001234567" if kind is Kind.COMPANY else None,
        "approvals_needed": 1 if kind is Kind.COMPANY else 2,
        "joined": 1 if kind is Kind.COMPANY else 2,
        "invited": 0,
        "documents": COMPANY_DOCUMENTS if kind is Kind.COMPANY else GROUP_DOCUMENTS,
    }
    values.update(changes)
    return Readiness(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", list(Kind))
def test_a_complete_application_has_no_problems(kind: Kind) -> None:
    assert problems(ready(kind)) == []


def test_a_sole_director_is_enough_for_a_company() -> None:
    assert problems(ready(Kind.COMPANY, joined=1, approvals_needed=1)) == []


def test_a_group_needs_two_people_and_two_approvals() -> None:
    assert problems(ready(Kind.GROUP, joined=1, approvals_needed=1)) == [
        Problem.TOO_FEW_SIGNATORIES,
        Problem.APPROVALS_TOO_FEW,
    ]


def test_every_problem_is_listed_in_the_order_to_fix_it() -> None:
    found = ready(
        Kind.COMPANY,
        registration_number=None,
        approvals_needed=3,
        joined=0,
        invited=2,
        documents=frozenset({DocumentKind.BOARD_RESOLUTION}),
    )
    assert problems(found) == [
        Problem.SIGNATORIES_WAITING,
        Problem.TOO_FEW_SIGNATORIES,
        Problem.APPROVALS_MORE_THAN_SIGNATORIES,
        Problem.DOCUMENTS_MISSING,
        Problem.REGISTRATION_NUMBER_MISSING,
    ]


def test_a_group_may_have_no_registration_number() -> None:
    assert problems(ready(Kind.GROUP, registration_number=None)) == []


def test_one_kinds_documents_do_not_count_for_the_other() -> None:
    assert problems(ready(Kind.GROUP, documents=COMPANY_DOCUMENTS)) == [Problem.DOCUMENTS_MISSING]


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("  Kampala   Traders Ltd ", "Kampala Traders Ltd"),
        ("Kira Rd. Savings Group", "Kira Rd. Savings Group"),
        ("Nakato & Sons (U) Ltd", "Nakato & Sons (U) Ltd"),
        ("Bodaboda Riders' SACCO, Ntinda", "Bodaboda Riders' SACCO, Ntinda"),
        ("Women\u2019s Savings Club", "Women's Savings Club"),
        ("Ekibiina kya Bakyala-Mukono", "Ekibiina kya Bakyala-Mukono"),
        ("Café Société 2", "Café Société 2"),
        ("Class of 2024 Investment Club 123456", "Class of 2024 Investment Club 123456"),
    ],
)
def test_a_name_is_tidied(given: str, kept: str) -> None:
    assert clean_name(given) == kept


@pytest.mark.parametrize("given", ["", "   ", "x" * (MAX_NAME + 1)])
def test_an_empty_or_long_name_is_no_name(given: str) -> None:
    assert clean_name(given) is None


@pytest.mark.parametrize(
    "given",
    [
        "Win at http://x.example",
        "finvest-help.com",
        "Visit www.example",
        "Write to me@example",
        "Group / Club",
        "Call +256 700 000000",
        "Finvest Security, call 0700 123456 to keep your account",
        "Group 0772-123-456",
        "Kampala Traders Ltd!",
        "<b>Traders</b>",
        "-Traders",
        "Line​break",
    ],
)
def test_a_name_cannot_carry_an_address_or_odd_marks(given: str) -> None:
    with pytest.raises(ValueError, match="not a name"):
        clean_name(given)


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        (None, None),
        ("  ", None),
        ("80020001234567", "80020001234567"),
        ("ug/2024/123", "UG/2024/123"),
        ("cs  12-34", "CS 12-34"),
    ],
)
def test_a_registration_number_is_tidied(given: str | None, kept: str | None) -> None:
    assert clean_registration(given) == kept


@pytest.mark.parametrize("given", ["12", "#12345", "12345/", "1" * 41])
def test_a_registration_number_must_look_like_one(given: str) -> None:
    with pytest.raises(ValueError, match="registration"):
        clean_registration(given)


def test_a_role_is_tidied_and_limited() -> None:
    assert clean_role("  Chair   person ") == "Chair person"
    assert clean_role(" ") is None
    assert clean_role("x" * (MAX_ROLE + 1)) is None


@pytest.mark.parametrize(
    ("content_type", "data", "matches"),
    [
        ("application/pdf", b"%PDF-1.7\n", True),
        ("application/pdf", b"<html>", False),
        ("image/jpeg", b"\xff\xd8\xff\xe0", True),
        ("image/jpeg", b"\x89PNG\r\n\x1a\n", False),
        ("image/png", b"\x89PNG\r\n\x1a\n", True),
        ("image/png", b"GIF89a", False),
        ("text/html", b"%PDF-1.7", False),
    ],
)
def test_a_document_must_start_as_its_type_does(
    content_type: str, data: bytes, matches: bool
) -> None:
    assert document_matches_type(content_type, data) is matches
