"""Outgoing text messages behind one small interface, so the provider can change.

ioTec Messaging is the provider (`iotec`, in `iotec.py`). `none` fails closed, and
`local-console` prints messages for local development.
"""

import re
from dataclasses import dataclass, field
from typing import Literal, Protocol, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")
MAX_TEXT = 320
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:@-]{1,200}$")


class InvalidSms(ValueError):
    """The message cannot be sent as given. Retrying the same message cannot succeed."""


class SmsFailure(Exception):
    code = "sms_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = message
        if code is not None:
            self.code = code


class SmsDeferred(SmsFailure):
    """Nothing was accepted. Safe to try again."""

    code = "sms_deferred"


class SmsRejected(SmsFailure):
    """The provider refused the message or the credentials."""

    code = "sms_rejected"


class SmsUncertain(SmsFailure):
    """The provider may have accepted the message. Never sent again blindly."""

    code = "sms_uncertain"


def check_phone(value: str) -> str:
    if not PHONE.fullmatch(value):
        raise InvalidSms("A phone number is not valid")
    return value


@dataclass(frozen=True)
class SmsMessage:
    to: str = field(repr=False)
    text: str = field(repr=False)
    idempotency_key: str

    def __post_init__(self) -> None:
        check_phone(self.to)
        if not self.text.strip() or len(self.text) > MAX_TEXT:
            raise InvalidSms(f"A text message needs 1 to {MAX_TEXT} characters")
        if not IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise InvalidSms("The idempotency key is not valid")


@dataclass(frozen=True)
class SmsResult:
    provider: str
    provider_message_id: str | None


class SmsTransport(Protocol):
    name: str

    async def send(self, message: SmsMessage) -> SmsResult: ...


class DisabledSms:
    """No SMS provider is configured: every send fails, and queued messages wait to retry."""

    name = "none"

    async def send(self, message: SmsMessage) -> SmsResult:
        raise SmsDeferred("SMS delivery is not configured", code="sms_not_configured")


class ConsoleSms:
    """Local development only: print the message where the developer can read the code.

    It writes a phone number and a one-time code to standard output, which a deployed system
    must never do. A deployment uses a real provider.
    """

    name = "local-console"

    async def send(self, message: SmsMessage) -> SmsResult:
        print(f"[local SMS to {message.to}] {message.text}", flush=True)  # noqa: T201
        return SmsResult(provider=self.name, provider_message_id=None)


class SmsSettings(BaseSettings):
    """Read from `FINVEST_SMS_*` by the worker."""

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_SMS_", extra="ignore", hide_input_in_errors=True
    )

    backend: Literal["none", "local-console", "iotec"] = "none"
    # From the ioTec Messaging portal: Management, Settings, Configuration.
    iotec_client_id: str | None = None
    iotec_api_key: SecretStr | None = None
    iotec_base_url: str = "https://messaging-api.iotec.io"
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = Field(default=None, validation_alias="FINVEST_ENVIRONMENT")

    @model_validator(mode="after")
    def keep_the_console_local(self) -> Self:
        # The console backend prints a phone number and a live code. Anywhere deployed, that
        # is personal data in a log. A name this code does not know counts as deployed.
        if self.backend == "local-console" and self.environment not in (None, "", "local"):
            raise ValueError("The local-console SMS backend may only run on a developer's machine")
        if self.backend == "iotec" and not (
            self.iotec_client_id and self.iotec_api_key and self.iotec_api_key.get_secret_value()
        ):
            raise ValueError("The iotec SMS backend needs FINVEST_SMS_IOTEC_CLIENT_ID and _API_KEY")
        if not self.iotec_base_url.startswith("https://"):
            raise ValueError("The ioTec address must use https")
        return self

    def transport(self) -> SmsTransport:
        if self.backend == "iotec":
            from app.infrastructure.sms.iotec import IotecSms

            assert self.iotec_client_id is not None and self.iotec_api_key is not None
            return IotecSms(
                client_id=self.iotec_client_id,
                api_key=self.iotec_api_key,
                base_url=self.iotec_base_url,
            )
        return ConsoleSms() if self.backend == "local-console" else DisabledSms()


__all__ = [
    "ConsoleSms",
    "DisabledSms",
    "InvalidSms",
    "SmsDeferred",
    "SmsFailure",
    "SmsMessage",
    "SmsRejected",
    "SmsResult",
    "SmsSettings",
    "SmsTransport",
    "SmsUncertain",
    "check_phone",
]
