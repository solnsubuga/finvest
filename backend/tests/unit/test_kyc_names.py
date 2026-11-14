"""How a client's name is shown: proper case, given names first (kyc/domain/names.py)."""

import pytest

from app.modules.kyc.domain.names import first_name, full_name, proper_case


@pytest.mark.parametrize(
    ("printed", "shown"),
    [
        ("AMARA", "Amara"),
        ("amara", "Amara"),
        ("AMARA JOY", "Amara Joy"),
        ("  amara   JOY ", "Amara Joy"),
        ("SSALI-KATO", "Ssali-Kato"),
        ("O'NEIL", "O'Neil"),
        ("McDONALD", "McDONALD"),
        ("McDonald", "McDonald"),
        ("deSouza", "deSouza"),
        ("ÉLODIE", "Élodie"),
        ("J.", "J."),
        ("", ""),
    ],
)
def test_proper_case(printed: str, shown: str) -> None:
    assert proper_case(printed) == shown


def test_full_name_puts_given_names_first() -> None:
    assert full_name("NAKATO", "AMARA") == "Amara Nakato"
    assert full_name("NAKATO", "AMARA JOY") == "Amara Joy Nakato"
    assert full_name("okello", "joseph") == "Joseph Okello"


def test_full_name_with_a_name_missing() -> None:
    assert full_name("NAKATO", "") == "Nakato"
    assert full_name("", "AMARA") == "Amara"
    assert full_name(" ", " ") == ""


def test_first_name_is_the_first_given_name() -> None:
    assert first_name("NAKATO", "AMARA JOY") == "Amara"
    assert first_name("NAKATO", "  amara ") == "Amara"


def test_first_name_falls_back_to_the_surname() -> None:
    assert first_name("NAKATO", "") == "Nakato"
