"""Which reader turns custodian statements into rows. Read by the worker and the API."""

from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.modules.reconciliation.claude_reader import ClaudeStatementReader
from app.modules.reconciliation.custodian_reading import DevelopmentStubReader
from app.modules.reconciliation.domain.custodian import StatementReader
from app.modules.reconciliation.gemini_reader import GeminiStatementReader
from app.modules.reconciliation.openai_reader import OpenAIStatementReader

# The readers that send the statement to an AI provider, and the setting holding each one's key.
_KEYS = {
    "claude": "anthropic_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
}


class CustodianReaderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # `none` turns uploads off: the console says reading is not set up. `claude`, `openai`
    # and `gemini` read any layout with that provider's API. `development-stub` reads one CSV
    # layout, for a developer's machine and demos.
    custodian_reader: Literal["none", "claude", "openai", "gemini", "development-stub"] = "none"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    # Which of the provider's models reads, set where deployed. Required with an AI reader, so
    # no model id lives in the code.
    custodian_reader_model: str | None = None
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None

    @model_validator(mode="after")
    def keep_the_stub_harmless(self) -> Self:
        if self.custodian_reader == "development-stub" and self.environment == "production":
            # It reads one made-up layout, never a real statement.
            raise ValueError("The development stub reads no real statement and may not run here")
        key_setting = _KEYS.get(self.custodian_reader)
        if key_setting is not None:
            secret: SecretStr | None = getattr(self, key_setting)
            if secret is None or not secret.get_secret_value():
                raise ValueError(
                    f"The {self.custodian_reader} reader needs FINVEST_{key_setting.upper()}"
                )
            if not self.custodian_reader_model:
                raise ValueError(
                    f"The {self.custodian_reader} reader needs FINVEST_CUSTODIAN_READER_MODEL"
                )
        return self

    @property
    def reading_on(self) -> bool:
        return self.custodian_reader != "none"

    def reader(self) -> StatementReader | None:
        model = self.custodian_reader_model or ""
        if self.custodian_reader == "claude":
            assert self.anthropic_api_key is not None and model  # checked above
            return ClaudeStatementReader(api_key=self.anthropic_api_key, model=model)
        if self.custodian_reader == "openai":
            assert self.openai_api_key is not None and model  # checked above
            return OpenAIStatementReader(api_key=self.openai_api_key, model=model)
        if self.custodian_reader == "gemini":
            assert self.gemini_api_key is not None and model  # checked above
            return GeminiStatementReader(api_key=self.gemini_api_key, model=model)
        if self.custodian_reader == "development-stub":
            return DevelopmentStubReader()
        return None
