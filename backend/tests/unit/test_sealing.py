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
    for bad in (flipped, sealed[:10], b"", b"\x03" + sealed[1:]):
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


def test_the_first_format_without_a_key_id_is_refused_by_name() -> None:
    sealed = sealer().seal("secret", PLACE)
    assert sealed[:2] == b"\x02\x01"  # format version, then key id
    # Version 1 was version byte, nonce, ciphertext. Its first nonce byte must never be read
    # as a key id, whatever that byte is.
    for first_nonce_byte in (b"\x01", b"\x7f"):
        old_layout = b"\x01" + first_nonce_byte + sealed[2:]
        with pytest.raises(SealError, match="format version 1"):
            sealer().open(old_layout, PLACE)


def test_a_replaced_key_still_opens_what_it_sealed_and_new_values_use_the_new_key() -> None:
    old, new = bytes(range(32)), bytes(range(1, 33))
    before = Sealer(old, purpose="identity")
    after = Sealer(new, purpose="identity", key_id=2, retired={1: old})
    kept = before.seal("sealed last year", PLACE)
    fresh = after.seal("sealed today", PLACE)

    assert after.open(kept, PLACE) == "sealed last year"
    assert after.open(fresh, PLACE) == "sealed today"
    assert not after.sealed_with_current_key(kept)
    assert after.sealed_with_current_key(fresh)
    # The old process cannot read what the new key sealed, and a dropped key reads nothing.
    with pytest.raises(SealError):
        before.open(fresh, PLACE)
    with pytest.raises(SealError):
        Sealer(new, purpose="identity", key_id=2).open(kept, PLACE)
    # Swapping the key id on a sealed value does not make another key open it.
    with pytest.raises(SealError):
        after.open(kept[:1] + b"\x02" + kept[2:], PLACE)


@pytest.mark.parametrize(
    ("key_id", "retired"),
    [(0, {}), (256, {}), (1, {1: bytes(32)}), (2, {1: bytes(16)}), (2, {0: bytes(32)})],
)
def test_key_ids_are_small_distinct_numbers(key_id: int, retired: dict[int, bytes]) -> None:
    with pytest.raises(ValueError, match="key"):
        Sealer(KEY, purpose="identity", key_id=key_id, retired=retired)
