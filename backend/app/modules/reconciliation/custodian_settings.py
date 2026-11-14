"""Which reader turns custodian statements into rows. Read by the worker and the API."""

from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.modules.reconciliation.claude_reader import ClaudeStatementReader
from app.modules.reconciliation.custodian_reading import DevelopmentStubReader
from app.modules.reconciliation.domain.custodian import StatementReader


class CustodianReaderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # `none` turns uploads off: the console says reading is not set up. `claude` reads any
    # layout with the Claude API. `development-stub` reads one CSV layout, for a developer's
    # machine and demos.
    custodian_reader: Literal["none", "claude", "development-stub"] = "none"
    anthropic_api_key: SecretStr | None = None
    # Which Claude model reads, set where deployed. Required with `claude`, so no model id
    # lives in the code.
    custodian_reader_model: str | None = None
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None

    @model_validator(mode="after")
    def keep_the_stub_harmless(self) -> Self:
        if self.custodian_reader == "development-stub" and self.environment == "production":
            # It reads one made-up layout, never a real statement.
            raise ValueError("The development stub reads no real statement and may not run here")
        if self.custodian_reader == "claude" and (
            self.anthropic_api_key is None or not self.anthropic_api_key.get_secret_value()
        ):
            raise ValueError("The Claude reader needs FINVEST_ANTHROPIC_API_KEY")
        if self.custodian_reader == "claude" and not self.custodian_reader_model:
            raise ValueError("The Claude reader needs FINVEST_CUSTODIAN_READER_MODEL")
        return self

    @property
    def reading_on(self) -> bool:
        return self.custodian_reader != "none"

    def reader(self) -> StatementReader | None:
        if self.custodian_reader == "claude":
            assert self.anthropic_api_key is not None  # checked above
            assert self.custodian_reader_model  # checked above
            return ClaudeStatementReader(
                api_key=self.anthropic_api_key, model=self.custodian_reader_model
            )
        if self.custodian_reader == "development-stub":
            return DevelopmentStubReader()
        return None
