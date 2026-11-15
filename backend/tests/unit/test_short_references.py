"""Short references: readable, never start with a dash, and lead back to the record."""

import pytest

from app.shared.push_ids import push_id
from app.shared.references import CROCKFORD, push_id_suffix, short_reference

# The same pairs are in apps/client/src/shared/__tests__/references.test.ts. Change both together.
KNOWN = [
    ("-P6TNMgBJ0qtT-C9Ed1Q", "FV-3R0D5-3X44V"),
    ("--------------------", "FV-00000-00000"),
    ("zzzzzzzzzzzzzzzzzzzz", "FV-7ZZZZ-ZZZZZ"),
    ("-P6TYZZTunCqFtwZMYmp", "FV-23KWJ-5WF5N"),
]


@pytest.mark.parametrize(("record_id", "expected"), KNOWN)
def test_known_ids_give_the_same_reference_in_every_app(record_id: str, expected: str) -> None:
    assert short_reference(record_id) == expected


def test_a_reference_never_starts_with_a_dash_and_has_only_readable_characters() -> None:
    for _ in range(500):
        reference = short_reference(push_id())
        prefix, first, second = reference.split("-")
        assert prefix == "FV"
        assert len(first) == len(second) == 5
        assert set(first + second) <= set(CROCKFORD)


def test_the_prefix_names_the_kind_of_record() -> None:
    assert short_reference("-P6TNMgBJ0qtT-C9Ed1Q", "ST").startswith("ST-")


def test_it_leads_back_to_the_last_eight_characters_of_the_id() -> None:
    for _ in range(500):
        record_id = push_id()
        assert push_id_suffix(short_reference(record_id)) == record_id[-8:]
        assert push_id_suffix(short_reference(record_id, "IN")) == record_id[-8:]


@pytest.mark.parametrize(
    "typed",
    ["FV-3R0D5-3X44V", "fv-3r0d5-3x44v", "3R0D5-3X44V", "3R0D53X44V", " FV 3R0D5 - 3X44V "],
)
def test_staff_can_type_it_as_the_client_read_it_out(typed: str) -> None:
    assert push_id_suffix(typed) == "T-C9Ed1Q"


def test_o_i_and_l_are_read_as_digits() -> None:
    assert push_id_suffix("FV-00000-00001") == push_id_suffix("FV-OOOOO-OOOOI")
    assert push_id_suffix("FV-00000-00001") == push_id_suffix("FV-ooooo-oooOl")


@pytest.mark.parametrize(
    "typed", ["", "FV-", "FV-3R0D5", "FV-3R0D5-3X44V9", "FV-UUUUU-UUUUU", "8ZZZZ-ZZZZZ"]
)
def test_anything_else_is_not_a_reference(typed: str) -> None:
    assert push_id_suffix(typed) is None


def test_an_id_that_is_not_a_push_id_is_shown_as_it_is() -> None:
    assert short_reference("entry-17") == "entry-17"
