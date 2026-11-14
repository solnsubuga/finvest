"""Email settings, read from `FINVEST_EMAIL_*` by the worker.

Only the worker sends email, so only the worker is given the provider's key. `none` fails
closed: messages stay queued and retry until their delivery window ends.
"""

from dataclasses import replace
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.email.message import InvalidEmail, Sender
from app.infrastructure.email.transports import DisabledTransport, EmailTransport, ResendTransport


class EmailSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_EMAIL_",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    backend: Literal["none", "resend"] = "none"
    # `Finvest <no-reply@finvestug.com>`. Required to send. The domain must be verified in Resend.
    sender: str | None = Field(default=None, alias="FINVEST_EMAIL_FROM")
    resend_api_key: SecretStr | None = None
    resend_api_url: str = "https://api.resend.com"
    timeout_seconds: float = Field(default=30, gt=0, le=120)

    @field_validator("sender", "resend_api_key", mode="before")
    @classmethod
    def blank_is_unset(cls, value: object) -> object:
        # Compose passes unset variables as empty strings.
        return None if value == "" else value

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.backend == "none":
            return self
        if not self.sender:
            raise ValueError("FINVEST_EMAIL_FROM is required to send email")
        try:
            Sender.parse(self.sender)
        except InvalidEmail:
            raise ValueError("FINVEST_EMAIL_FROM is not a valid sender") from None
        if self.resend_api_key is None or not self.resend_api_key.get_secret_value():
            raise ValueError("FINVEST_EMAIL_RESEND_API_KEY is required for Resend")
        if not self.resend_api_url.startswith("https://"):
            raise ValueError("FINVEST_EMAIL_RESEND_API_URL must use https")
        return self

    def transport(self) -> EmailTransport:
        if self.backend == "none" or self.sender is None or self.resend_api_key is None:
            return DisabledTransport()
        sender = Sender.parse(self.sender)
        if not sender.name:
            # Inboxes list a bare address by its local part ("no-reply"). People look for Finvest.
            sender = replace(sender, name="Finvest")
        return ResendTransport(
            api_key=self.resend_api_key,
            sender=sender,
            base_url=self.resend_api_url,
            timeout=self.timeout_seconds,
        )
