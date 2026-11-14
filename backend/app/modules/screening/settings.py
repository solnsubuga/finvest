"""Screening configuration, read by the worker."""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.modules.screening.domain.provider import ScreeningProvider
from app.modules.screening.providers import DevelopmentStubProvider


class ScreeningSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # `none` until the owner's provider account exists: every screen is recorded as not run
    # and the console says screening is off. `development-stub` is for a developer's machine.
    screening_provider: Literal["none", "development-stub"] = "none"
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None

    @model_validator(mode="after")
    def keep_the_stub_harmless(self) -> Self:
        if self.screening_provider == "development-stub" and self.environment == "production":
            # Every client would look screened and clear when nobody was screened.
            raise ValueError("The development stub screens nobody and may not run in production")
        return self

    def provider(self) -> ScreeningProvider | None:
        return DevelopmentStubProvider() if self.screening_provider == "development-stub" else None
