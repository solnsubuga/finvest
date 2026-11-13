from datetime import timedelta

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class JobProcessSettings(BaseSettings):
    """Tuning for the worker and scheduler processes, from `FINVEST_JOBS_*`."""

    model_config = SettingsConfigDict(env_prefix="FINVEST_JOBS_", extra="ignore")

    queue: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{0,63}$")
    concurrency: int = Field(default=4, ge=1, le=100)
    lease_seconds: float = Field(default=30, ge=5, le=3600)
    poll_seconds: float = Field(default=1, gt=0, le=60)
    leader_ttl_seconds: float = Field(default=30, ge=5, le=300)
    leader_renew_seconds: float = Field(default=10, ge=1, le=100)

    @property
    def lease(self) -> timedelta:
        return timedelta(seconds=self.lease_seconds)
