"""Identity check configuration. Read by the API and the worker, the two processes that open
identity details."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.sealing import Sealer, key_from_text


class KycSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # 32 random bytes in base64url, one per environment. It seals document numbers, dates of
    # birth and TINs. Losing it loses them, and changing it makes every saved check unreadable.
    identity_data_key: SecretStr

    @field_validator("identity_data_key")
    @classmethod
    def require_key(cls, value: SecretStr) -> SecretStr:
        # key_from_text never puts the value in its error.
        key_from_text(value.get_secret_value())
        return value

    def sealer(self) -> Sealer:
        return Sealer(key_from_text(self.identity_data_key.get_secret_value()), purpose="identity")
