"""The key email addresses are hashed under. Read by the API and the worker.

An address is never stored. The suppression list and Resend's delivery events keep a hash of
it, so an address can be matched but not read. A plain sha256 of an address can be undone by
hashing a list of likely addresses, so the hash is keyed: HMAC-SHA256 under this key, over the
plain hash (`domain/email_events.keyed_hash`). Moving to it is spread over three releases
(docs/notifications.md#how-addresses-are-hashed).
"""

from typing import Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.sealing import key_from_text


class EmailHashSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # 32 random bytes in base64url, one per environment, the same for the API and the worker.
    # Changing it orphans every stored hash: a suppressed address would be emailed again.
    email_hash_key: SecretStr | None = None
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None

    @field_validator("email_hash_key", mode="before")
    @classmethod
    def blank_is_unset(cls, value: object) -> object:
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if text == "" else value

    @field_validator("email_hash_key")
    @classmethod
    def usable(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            # key_from_text never puts the value in its error.
            key_from_text(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def required_in_production(self) -> Self:
        if self.email_hash_key is None and self.environment == "production":
            raise ValueError("FINVEST_EMAIL_HASH_KEY is required in production")
        return self

    def key(self) -> bytes | None:
        """None on a developer's machine without one: only the plain hash is written there."""
        if self.email_hash_key is None:
            return None
        return key_from_text(self.email_hash_key.get_secret_value())
