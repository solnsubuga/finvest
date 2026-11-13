"""Identity check configuration. Read by the API and the worker, the two processes that open
identity details."""

from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.sealing import Sealer, key_from_text
from app.modules.kyc.domain.provider import IdentityProvider
from app.modules.kyc.providers import DevelopmentStubProvider, ManualProvider


class KycSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # 32 random bytes in base64url, one per environment. It seals document numbers, dates of
    # birth and TINs. Losing it loses them, and changing it makes every saved check unreadable.
    identity_data_key: SecretStr | None = None
    # No provider is chosen yet. `manual` sends every client to a person and runs no jobs.
    # `development-stub` is for a developer's own machine.
    identity_provider: Literal["manual", "development-stub"] = "manual"
    # The owner's decision (docs/decisions.md). Off: a person approves every client.
    identity_automatic_approval: bool = False
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None

    @field_validator("identity_data_key")
    @classmethod
    def require_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            # key_from_text never puts the value in its error.
            key_from_text(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def keep_the_stub_harmless(self) -> Self:
        if self.identity_provider != "manual" and self.identity_data_key is None:
            raise ValueError("An identity provider needs FINVEST_IDENTITY_DATA_KEY")
        if self.identity_provider == "development-stub" and self.environment == "production":
            # Staff would see passes for checks nobody ran, and approve on them.
            raise ValueError("The development stub checks nothing and may not run in production")
        if self.identity_provider == "development-stub" and self.identity_automatic_approval:
            raise ValueError("The development stub checks nothing and may not approve clients")
        return self

    def sealer(self) -> Sealer:
        if self.identity_data_key is None:
            raise ValueError("FINVEST_IDENTITY_DATA_KEY is not set")
        return Sealer(key_from_text(self.identity_data_key.get_secret_value()), purpose="identity")

    def provider(self) -> IdentityProvider:
        return DevelopmentStubProvider() if self.identity_provider != "manual" else ManualProvider()
