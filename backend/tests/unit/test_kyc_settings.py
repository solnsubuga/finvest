"""The identity data key: taken from the environment, validated, never echoed."""

import base64
import os

import pytest
from pydantic import ValidationError

from app.infrastructure.sealing import associated_data
from app.modules.kyc.settings import KycSettings

PLACE = associated_data("kyc_checks", "document_number", "-Nabc0000000000000A1")


def fresh_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def test_the_key_from_the_environment_builds_the_sealer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_IDENTITY_DATA_KEY", fresh_key())
    first, second = KycSettings().sealer(), KycSettings().sealer()
    assert second.open(first.seal("CM94062210ABCD", PLACE), PLACE) == "CM94062210ABCD"


def test_a_different_key_does_not_open_what_another_sealed() -> None:
    first = KycSettings(identity_data_key=fresh_key()).sealer()
    second = KycSettings(identity_data_key=fresh_key()).sealer()
    assert first.fingerprint("x") != second.fingerprint("x")


@pytest.mark.parametrize("bad", ["", "too-short", "not base64 at all!!"])
def test_a_bad_key_is_refused_without_being_shown(bad: str) -> None:
    with pytest.raises(ValidationError) as error:
        KycSettings(identity_data_key=bad)
    assert "32 bytes" in str(error.value)
    if bad:
        assert bad not in str(error.value)


def test_a_process_without_the_key_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_IDENTITY_DATA_KEY", raising=False)
    with pytest.raises(ValidationError):
        KycSettings()
