import pytest
from pydantic import ValidationError

from app.infrastructure.sms import ConsoleSms, DisabledSms, SmsSettings
from app.infrastructure.sms.africastalking import AfricasTalkingSms
from app.infrastructure.sms.fallback import FallbackSms
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


def africas_talking(monkeypatch: pytest.MonkeyPatch, *, fallback: str | None = "iotec") -> None:
    monkeypatch.setenv("FINVEST_SMS_BACKEND", "africastalking")
    monkeypatch.setenv("FINVEST_SMS_AFRICASTALKING_USERNAME", "finvest")
    # Made up for this test. The real keys live only in the worker's environment.
    monkeypatch.setenv("FINVEST_SMS_AFRICASTALKING_API_KEY", "atsk_made-up")
    if fallback is not None:
        monkeypatch.setenv("FINVEST_SMS_FALLBACK", fallback)
    monkeypatch.setenv("FINVEST_SMS_IOTEC_CLIENT_ID", "test-client")
    monkeypatch.setenv("FINVEST_SMS_IOTEC_API_KEY", "IO-made-up")


def test_africas_talking_sends_first_and_ioTec_takes_over(monkeypatch: pytest.MonkeyPatch) -> None:
    africas_talking(monkeypatch)
    monkeypatch.setenv("FINVEST_ENVIRONMENT", "production")
    monkeypatch.setenv("FINVEST_SMS_AFRICASTALKING_SENDER_ID", "FINVEST")
    settings = SmsSettings()
    sms = settings.transport()
    assert isinstance(sms, FallbackSms)
    assert isinstance(sms.primary, AfricasTalkingSms)
    assert isinstance(sms.secondary, IotecSms)
    assert sms.primary.sender_id == "FINVEST"
    assert sms.primary.base_url == "https://api.africastalking.com"
    assert "atsk_made-up" not in repr(settings)


def test_africas_talking_can_run_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    africas_talking(monkeypatch, fallback=None)
    monkeypatch.delenv("FINVEST_SMS_FALLBACK", raising=False)
    assert isinstance(SmsSettings().transport(), AfricasTalkingSms)


def test_africas_talking_needs_its_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    africas_talking(monkeypatch)
    monkeypatch.delenv("FINVEST_SMS_AFRICASTALKING_API_KEY")
    with pytest.raises(ValidationError, match="AFRICASTALKING_USERNAME"):
        SmsSettings()


def test_the_ioTec_fallback_needs_its_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    africas_talking(monkeypatch)
    monkeypatch.delenv("FINVEST_SMS_IOTEC_API_KEY")
    with pytest.raises(ValidationError, match="IOTEC_CLIENT_ID"):
        SmsSettings()


@pytest.mark.parametrize("backend", ["none", "local-console", "iotec"])
def test_a_fallback_needs_a_different_real_first_provider(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    africas_talking(monkeypatch)
    monkeypatch.setenv("FINVEST_SMS_BACKEND", backend)
    monkeypatch.delenv("FINVEST_ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError, match="fallback"):
        SmsSettings()


def test_the_sandbox_username_goes_to_the_sandbox_host(monkeypatch: pytest.MonkeyPatch) -> None:
    africas_talking(monkeypatch, fallback=None)
    monkeypatch.setenv("FINVEST_ENVIRONMENT", "staging")
    monkeypatch.setenv("FINVEST_SMS_AFRICASTALKING_USERNAME", "sandbox")
    sms = SmsSettings().transport()
    assert isinstance(sms, AfricasTalkingSms)
    assert sms.base_url == "https://api.sandbox.africastalking.com"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FINVEST_SMS_AFRICASTALKING_USERNAME", "sandbox"),
        ("FINVEST_SMS_AFRICASTALKING_BASE_URL", "https://api.sandbox.africastalking.com"),
    ],
)
def test_production_refuses_the_sandbox(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    # The sandbox delivers nothing to a real phone, so nobody could sign up.
    africas_talking(monkeypatch)
    monkeypatch.setenv("FINVEST_ENVIRONMENT", "production")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match="sandbox"):
        SmsSettings()


def test_the_africas_talking_address_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    africas_talking(monkeypatch)
    monkeypatch.setenv("FINVEST_SMS_AFRICASTALKING_BASE_URL", "http://api.africastalking.com")
    with pytest.raises(ValidationError, match="https"):
        SmsSettings()
