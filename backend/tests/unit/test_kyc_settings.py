"""The identity data key: taken from the environment, validated, never echoed."""

import base64
import os
from datetime import date

import pytest
from pydantic import ValidationError

from app.infrastructure.sealing import associated_data
from app.modules.kyc.domain.checks import DocumentType, IdentityDetails
from app.modules.kyc.domain.provider import (
    Accepted,
    AutomaticCheck,
    Completed,
    ProviderRequest,
    Verdict,
)
from app.modules.kyc.providers import DevelopmentStubProvider, ManualProvider
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


def test_without_a_key_the_default_is_the_manual_provider_and_nothing_is_sealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("IDENTITY_DATA_KEY", "IDENTITY_PROVIDER", "IDENTITY_AUTOMATIC_APPROVAL"):
        monkeypatch.delenv(f"FINVEST_{name}", raising=False)
    settings = KycSettings()
    assert isinstance(settings.provider(), ManualProvider)
    assert not settings.identity_automatic_approval
    with pytest.raises(ValueError, match="FINVEST_IDENTITY_DATA_KEY is not set"):
        settings.sealer()


def test_a_provider_needs_the_key() -> None:
    with pytest.raises(ValidationError, match="needs FINVEST_IDENTITY_DATA_KEY"):
        KycSettings(identity_provider="development-stub")


@pytest.mark.parametrize(
    ("environment", "starts"), [("production", False), ("staging", True), (None, True)]
)
def test_the_development_stub_does_not_start_in_production(
    environment: str | None, starts: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINVEST_IDENTITY_DATA_KEY", fresh_key())
    monkeypatch.setenv("FINVEST_IDENTITY_PROVIDER", "development-stub")
    if environment is None:
        monkeypatch.delenv("FINVEST_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("FINVEST_ENVIRONMENT", environment)
    if starts:
        assert isinstance(KycSettings().provider(), DevelopmentStubProvider)
    else:
        with pytest.raises(ValidationError, match="may not run in production"):
            KycSettings()
    # The manual provider is unaffected by where it runs.
    monkeypatch.setenv("FINVEST_IDENTITY_PROVIDER", "manual")
    assert isinstance(KycSettings().provider(), ManualProvider)


def test_the_development_stub_can_never_approve_clients() -> None:
    with pytest.raises(ValidationError, match="may not approve clients"):
        KycSettings(
            identity_data_key=fresh_key(),
            identity_provider="development-stub",
            identity_automatic_approval=True,
        )
    stub = KycSettings(identity_data_key=fresh_key(), identity_provider="development-stub")
    assert isinstance(stub.provider(), DevelopmentStubProvider)


async def test_the_stub_passes_everyone_except_document_numbers_ending_in_zero() -> None:
    stub = DevelopmentStubProvider()
    for number, verdict in (("CM94062210ABCD", Verdict.PASS), ("CM940622100", Verdict.UNSURE)):
        request = ProviderRequest(
            idempotency_key="check:1",
            document_type=DocumentType.NATIONAL_ID,
            document_country="UG",
            details=IdentityDetails(
                surname="NAKATO",
                given_names="Amara",
                document_number=number,
                date_of_birth=date(1994, 6, 22),
                document_expires_on=date(2030, 9, 18),
            ),
        )
        accepted = await stub.submit(request)
        assert isinstance(accepted, Accepted)
        result = await stub.result(accepted.reference)
        assert isinstance(result, Completed)
        selfie = next(o for o in result.outcomes if o.check is AutomaticCheck.SELFIE_MATCH)
        assert selfie.verdict is verdict
        assert await stub.find("check:1") is None
