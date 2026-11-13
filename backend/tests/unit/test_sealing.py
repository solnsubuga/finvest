import base64

import pytest

from app.infrastructure.sealing import Sealer, SealError, associated_data, key_from_text

KEY = bytes(range(32))
PLACE = associated_data("kyc_checks", "document_number", "-Nabc0000000000000A1")


def sealer(key: bytes = KEY, purpose: str = "identity") -> Sealer:
    return Sealer(key, purpose=purpose)


def test_a_sealed_value_opens_to_what_was_sealed() -> None:
    sealed = sealer().seal("CF94123456782KX é", PLACE)
    assert sealer().open(sealed, PLACE) == "CF94123456782KX é"
    assert b"CF94" not in sealed


def test_sealing_the_same_value_twice_gives_different_bytes() -> None:
    assert sealer().seal("same", PLACE) != sealer().seal("same", PLACE)


@pytest.mark.parametrize(
    "other",
    [
        associated_data("kyc_checks", "tin", "-Nabc0000000000000A1"),
        associated_data("kyc_checks", "document_number", "-Nabc0000000000000B1"),
        b"",
    ],
)
def test_a_value_moved_to_another_place_does_not_open(other: bytes) -> None:
    sealed = sealer().seal("secret", PLACE)
    with pytest.raises(SealError):
        sealer().open(sealed, other)


def test_another_key_or_purpose_does_not_open_it() -> None:
    sealed = sealer().seal("secret", PLACE)
    with pytest.raises(SealError):
        sealer(bytes(32)).open(sealed, PLACE)
    with pytest.raises(SealError):
        sealer(purpose="authenticator").open(sealed, PLACE)


def test_an_altered_truncated_or_unversioned_value_does_not_open() -> None:
    sealed = sealer().seal("secret", PLACE)
    flipped = sealed[:-1] + bytes([sealed[-1] ^ 1])
    for bad in (flipped, sealed[:10], b"", b"\x02" + sealed[1:]):
        with pytest.raises(SealError) as error:
            sealer().open(bad, PLACE)
        assert error.value.__cause__ is None


def test_a_fingerprint_matches_equal_values_only_under_the_same_key_and_purpose() -> None:
    one = sealer().fingerprint("UG|national_id|CF94123456782KX")
    assert one == sealer().fingerprint("UG|national_id|CF94123456782KX")
    assert len(one) == 64
    assert one != sealer().fingerprint("UG|national_id|CF94123456782KY")
    assert one != sealer(bytes(32)).fingerprint("UG|national_id|CF94123456782KX")
    assert one != sealer(purpose="other").fingerprint("UG|national_id|CF94123456782KX")


def test_a_key_is_32_bytes_of_base64url_with_or_without_padding() -> None:
    text = base64.urlsafe_b64encode(KEY).decode()
    assert key_from_text(text) == KEY
    assert key_from_text(text.rstrip("=")) == KEY
    for bad in ("", "short", base64.urlsafe_b64encode(bytes(16)).decode(), "!" * 43):
        with pytest.raises(ValueError, match="32 bytes"):
            key_from_text(bad)
    with pytest.raises(ValueError, match="32 bytes"):
        Sealer(bytes(16), purpose="identity")
