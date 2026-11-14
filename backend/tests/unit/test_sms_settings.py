import pytest
from pydantic import ValidationError

from app.infrastructure.sms import ConsoleSms, DisabledSms, SmsSettings
from app.infrastructure.sms.iotec import IotecSms


@pytest.mark.parametrize("environment", [None, "", "local"])
def test_the_console_backend_runs_on_a_developers_machine(
    monkeypatch: pytest.MonkeyPatch, environment: str | None
) -> None:
    monkeypatch.setenv("FINVEST_SMS_BACKEND", "local-console")
    if environment is None:
        monkeypatch.delenv("FINVEST_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("FINVEST_ENVIRONMENT", environment)
    assert isinstance(SmsSettings().transport(), ConsoleSms)


@pytest.mark.parametrize("environment", ["staging", "production", "Production", "demo", "prod"])
def test_the_console_backend_refuses_to_start_anywhere_deployed(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    # It prints a phone number and a live code, which must never reach a deployed log. A name
    # nobody planned for counts as deployed.
    monkeypatch.setenv("FINVEST_SMS_BACKEND", "local-console")
    monkeypatch.setenv("FINVEST_ENVIRONMENT", environment)
    with pytest.raises(ValidationError, match="developer's machine"):
        SmsSettings()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_sending_nothing_is_allowed_everywhere(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("FINVEST_SMS_BACKEND", "none")
    monkeypatch.setenv("FINVEST_ENVIRONMENT", environment)
    assert isinstance(SmsSettings().transport(), DisabledSms)


def test_the_iotec_backend_needs_its_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_SMS_BACKEND", "iotec")
    monkeypatch.delenv("FINVEST_SMS_IOTEC_CLIENT_ID", raising=False)
    monkeypatch.delenv("FINVEST_SMS_IOTEC_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="IOTEC_CLIENT_ID"):
        SmsSettings()


def test_the_iotec_backend_sends_through_ioTec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_SMS_BACKEND", "iotec")
    monkeypatch.setenv("FINVEST_ENVIRONMENT", "production")
    monkeypatch.setenv("FINVEST_SMS_IOTEC_CLIENT_ID", "test-client")
    # Made up for this test. The real key lives only in the worker's environment.
    monkeypatch.setenv("FINVEST_SMS_IOTEC_API_KEY", "IO-made-up")
    settings = SmsSettings()
    assert isinstance(settings.transport(), IotecSms)
    assert "IO-made-up" not in repr(settings)


def test_the_iotec_address_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_SMS_BACKEND", "iotec")
    monkeypatch.setenv("FINVEST_SMS_IOTEC_CLIENT_ID", "test-client")
    monkeypatch.setenv("FINVEST_SMS_IOTEC_API_KEY", "IO-made-up")
    monkeypatch.setenv("FINVEST_SMS_IOTEC_BASE_URL", "http://messaging-api.iotec.io")
    with pytest.raises(ValidationError, match="https"):
        SmsSettings()
