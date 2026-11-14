"""The code printed on a statement for checking it on the website."""

from collections.abc import Callable

import pytest

from app.modules.documents.domain.checks import ALPHABET, LENGTH, new_code, normalised, shown


def test_a_code_is_twelve_characters_from_the_alphabet_with_every_character_reachable() -> None:
    every = bytes(range(256))
    code = new_code(lambda n: every[:n])
    assert len(code) == LENGTH and set(code) <= set(ALPHABET)

    def from_(start: int) -> Callable[[int], bytes]:
        return lambda n: bytes((start + i) % 256 for i in range(n))

    seen = {new_code(from_(start)) for start in range(32)}
    assert {char for code in seen for char in code} == set(ALPHABET)


def test_a_code_is_shown_in_three_groups() -> None:
    assert shown("K7Q29XMA4TPD") == "K7Q2-9XMA-4TPD"
    with pytest.raises(ValueError):
        shown("k7q2-9xma-4tpd")


@pytest.mark.parametrize(
    "typed",
    ["K7Q2-9XMA-4TPD", "k7q2 9xma 4tpd", " K7Q29XMA4TPD ", "k7q2.9xma.4tpd"],
)
def test_what_someone_types_is_read_as_the_code(typed: str) -> None:
    assert normalised(typed) == "K7Q29XMA4TPD"


def test_letters_that_look_like_digits_are_read_as_the_digits() -> None:
    assert normalised("OOOO-IIII-LLLL") == "000011111111"


@pytest.mark.parametrize(
    "typed", ["", "K7Q2-9XMA", "K7Q2-9XMA-4TPDX", "K7Q2-9XMA-4TP!", "U" * 12, "A" * 41]
)
def test_anything_else_is_not_a_code(typed: str) -> None:
    assert normalised(typed) is None
