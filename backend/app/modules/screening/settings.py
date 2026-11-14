"""Screening configuration, read by the worker."""

from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.modules.screening.domain.provider import ScreeningProvider
from app.modules.screening.providers import DevelopmentStubProvider
from app.modules.screening.sigma360 import BASE_URL as SIGMA360_URL
from app.modules.screening.sigma360 import Sigma360Provider


class ScreeningSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # `none` until the owner's provider account exists: every screen is recorded as not run
    # and the console says screening is off. `development-stub` is for a developer's machine.
    # `sigma360` once its key and filter set exist.
    screening_provider: Literal["none", "sigma360", "development-stub"] = "none"
    # From Sigma360. The filter set (Settings > Filter Sets in its console) picks the lists
    # searched and how close a name must be. Both are needed for `sigma360`.
    sigma360_api_key: SecretStr | None = None
    sigma360_filter_set_id: int | None = None
    sigma360_base_url: str = SIGMA360_URL
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None

    @model_validator(mode="after")
    def keep_the_stub_harmless(self) -> Self:
        if self.screening_provider == "development-stub" and self.environment == "production":
            # Every client would look screened and clear when nobody was screened.
            raise ValueError("The development stub screens nobody and may not run in production")
        if self.screening_provider == "sigma360":
            if self.sigma360_api_key is None or not self.sigma360_api_key.get_secret_value():
                raise ValueError("Sigma360 needs FINVEST_SIGMA360_API_KEY")
            if self.sigma360_filter_set_id is None or self.sigma360_filter_set_id < 0:
                raise ValueError("Sigma360 needs FINVEST_SIGMA360_FILTER_SET_ID")
            if not self.sigma360_base_url.startswith("https://"):
                raise ValueError("FINVEST_SIGMA360_BASE_URL must use https")
        return self

    @property
    def screening_on(self) -> bool:
        return self.screening_provider != "none"

    def provider(self) -> ScreeningProvider | None:
        if self.screening_provider == "sigma360":
            assert self.sigma360_api_key is not None  # checked above
            assert self.sigma360_filter_set_id is not None
            return Sigma360Provider(
                api_key=self.sigma360_api_key,
                filter_set_id=self.sigma360_filter_set_id,
                base_url=self.sigma360_base_url,
            )
        if self.screening_provider == "development-stub":
            return DevelopmentStubProvider()
        return None
