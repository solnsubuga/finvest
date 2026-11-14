"""Next of kin: cleaning what a client typed, and every way a list can be wrong."""

import pytest

from app.modules.kyc.domain.next_of_kin import (
    MAX_PEOPLE,
    Person,
    PersonProblem,
    Relationship,
    clean,
    mask_phone,
    problems,
)


def person(
    *,
    full_name: str = "Joseph Nakato",
    phone: str = "+256701555210",
    id_number: str = "CM90012345ABCD",
    relationship: Relationship = Relationship.SPOUSE,
) -> Person:
    return Person(full_name, relationship, phone, id_number)


def test_cleaning_tidies_spaces_and_writes_numbers_one_way() -> None:
    cleaned = clean(
        person(full_name="  Joseph   Nakato ", phone="0701 555 210", id_number="cm90-0123 45abcd")
    )
    assert cleaned.full_name == "Joseph Nakato"
    assert cleaned.phone == "+256701555210"
    assert cleaned.id_number == "CM90012345ABCD"


def test_a_number_that_cannot_be_read_is_kept_for_the_problem_to_name() -> None:
    assert clean(person(phone=" not a number ")).phone == "not a number"
    assert problems([clean(person(phone="12"))]) == [PersonProblem(0, "phone", "invalid_phone")]


def test_one_person_is_fine() -> None:
    assert problems([person()]) == []


def test_an_empty_list_removes_everyone() -> None:
    assert problems([]) == []


def test_next_of_kin_are_contacts_with_no_share() -> None:
    two = [person(), person(id_number="CM80000000AAAA")]
    assert problems(two) == []
    assert not hasattr(two[0], "share_bps")


def test_at_most_five_people() -> None:
    people = [person(id_number=f"CM9000000{index}ABCD") for index in range(MAX_PEOPLE + 1)]
    assert PersonProblem(None, "people", "too_many") in problems(people)


@pytest.mark.parametrize("name", ["", "Joseph", " ", "A" * 101 + " B"])
def test_a_full_name_has_at_least_two_names(name: str) -> None:
    found = problems([clean(person(full_name=name))])
    assert [p.field for p in found] == ["full_name"]


@pytest.mark.parametrize("id_number", ["", "AB12", "CM90!2345", "X" * 33])
def test_an_id_number_is_five_to_32_letters_and_digits(id_number: str) -> None:
    assert problems([person(id_number=id_number)]) == [
        PersonProblem(0, "id_number", "invalid_id_number")
    ]


def test_nobody_is_named_twice() -> None:
    twice = [person(), person(full_name="Grace Nakato")]
    assert problems(twice) == [PersonProblem(1, "id_number", "named_twice")]


def test_a_client_cannot_name_their_own_number() -> None:
    assert problems([person()], client_phone="+256701555210") == [
        PersonProblem(0, "phone", "your_own_number")
    ]
    assert problems([person()], client_phone="+256772123456") == []


def test_every_problem_is_reported_at_once() -> None:
    found = problems([clean(person(full_name="J", phone="1", id_number="!"))])
    assert {p.field for p in found} == {"full_name", "phone", "id_number"}


def test_a_masked_number_shows_its_start_and_its_last_three_digits() -> None:
    assert mask_phone("+256701555210") == "+2567•••••210"
    assert mask_phone("+2561") == "•••••"
