"""Process configuration from the environment. Secrets arrive here and nowhere else."""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True, populate_by_name=True
    )

    # Each process is given the URL of its own role: finvest_app for the API, finvest_worker for
    # the worker and scheduler, finvest_migrator for migrations.
    database_url: SecretStr = Field(
        validation_alias=AliasChoices("FINVEST_DATABASE_URL", "DATABASE_URL")
    )
    database_ssl_mode: Literal["disable", "verify-full"] = "disable"
    database_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    database_pool_size: int = Field(default=5, ge=1, le=20)

    # `/docs` and `/openapi.json`. Off unless turned on: a deployed API hands no route catalogue
    # to anonymous visitors.
    api_docs: bool = False
    log_format: Literal["json", "console"] = "json"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def require_postgres(cls, value: SecretStr) -> SecretStr:
        # Errors here never include the URL: it carries a password.
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError("A PostgreSQL connection URL is required") from None
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+asyncpg")
        if url.drivername != "postgresql+asyncpg" or not url.database:
            raise ValueError("A PostgreSQL connection URL is required")
        if {"ssl", "sslmode", "sslrootcert"} & url.query.keys():
            raise ValueError("Configure TLS with FINVEST_DATABASE_SSL_MODE")
        return SecretStr(url.render_as_string(hide_password=False))

    def database_connect_args(self) -> dict[str, object]:
        if self.database_ssl_mode == "disable":
            return {"ssl": False}
        import ssl

        return {"ssl": ssl.create_default_context()}
