"""Process configuration from the environment. Secrets arrive here and nowhere else."""

import base64
import ipaddress
import ssl
from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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
    # Verified TLS unless a deployment says otherwise. Only a database on the same private
    # network, such as the local Compose stack, turns it off, and it must say so.
    database_ssl_mode: Literal["disable", "verify-full"] = "verify-full"
    # PEM bundle of the CA that signs the database certificate, for a managed provider with its
    # own CA. Without it, verification uses the system trust store.
    database_ssl_root_cert: Path | None = None
    database_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    database_pool_size: int = Field(default=5, ge=1, le=20)

    # Sign-in. Browsers may call the API only from these exact origins, and a cookie session is
    # honoured for an unsafe request only when its Origin is one of them.
    client_allowed_origins: list[str] = Field(default_factory=list)
    staff_allowed_origins: list[str] = Field(default_factory=list)
    # Passkeys (WebAuthn). The site a passkey belongs to: `finvestug.com` for clients, so one
    # passkey works on the web and in the phone apps, and the console's own host for staff.
    # Unset turns passkeys off for that audience.
    client_passkey_site: str | None = None
    staff_passkey_site: str | None = None
    # What the phone apps report as their origin, besides the allowed browser origins above:
    # `android:apk-key-hash:...` for Android, `https://<site>` for iPhone.
    client_passkey_app_origins: list[str] = Field(default_factory=list)
    # Encrypts the secrets of staff authenticator apps: 32 random bytes, base64url. Generate
    # with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Unset turns the
    # authenticator app option off. Changing it locks out everyone who uses an app.
    authenticator_key: SecretStr | None = None
    # Off only for local development over plain http.
    cookie_secure: bool = True
    # Client on the web, client on a phone (the app's PIN guards it between uses), and staff.
    client_web_session_idle_minutes: int = Field(default=15, ge=5, le=120)
    client_web_session_absolute_hours: int = Field(default=12, ge=1, le=24)
    client_app_session_idle_days: int = Field(default=30, ge=1, le=90)
    client_app_session_absolute_days: int = Field(default=90, ge=1, le=365)
    staff_session_idle_minutes: int = Field(default=30, ge=5, le=240)
    staff_session_absolute_hours: int = Field(default=12, ge=1, le=24)
    password_memory_kib: int = Field(default=65_536, ge=19_456, le=1_048_576)
    password_time_cost: int = Field(default=3, ge=2, le=10)
    # Reverse proxies whose X-Forwarded-For names the caller, as IP addresses or networks.
    # Attempt limits key on that address, so the header is believed from nobody else.
    trusted_proxies: list[str] = Field(default_factory=list)

    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None
    # Whether anyone may open a new client account. Unset, sign-up is open on a developer's
    # machine and closed anywhere deployed, so production takes no sign-ups before switch day
    # (docs/launch.md). Sign-in, the code fallback, password reset and claims stay open.
    client_signup_open: bool | None = None
    # Whether a client may withdraw a running investment early. Off everywhere until the
    # dealing desk's funding and the payout that follows are both wired, so no withdrawal is
    # ever made that nothing would pay (docs/investing.md).
    early_withdrawal_open: bool = False

    # `/docs` and `/openapi.json`. Off unless turned on: a deployed API hands no route catalogue
    # to anonymous visitors.
    api_docs: bool = False
    log_format: Literal["json", "console"] = "json"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("client_allowed_origins", "staff_allowed_origins")
    @classmethod
    def exact_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            scheme, _, rest = origin.partition("://")
            if (
                scheme not in ("http", "https")
                or not rest
                or "/" in rest
                or origin != origin.strip()
            ):
                raise ValueError("Allowed origins are exact scheme://host[:port] values")
        return value

    @field_validator("authenticator_key", mode="before")
    @classmethod
    def authenticator_key_bytes(cls, value: object) -> object:
        if value is None or value == "":
            return None
        text = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        try:
            # Accept the key with or without its base64 padding.
            padded = text + "=" * (-len(text) % 4)
            size = len(base64.b64decode(padded, altchars=b"-_", validate=True))
        except ValueError:
            size = 0
        if size != 32:
            raise ValueError("The authenticator key is 32 random bytes in base64url")
        return padded

    @field_validator("trusted_proxies")
    @classmethod
    def proxy_addresses(cls, value: list[str]) -> list[str]:
        for proxy in value:
            try:
                network = ipaddress.ip_network(proxy, strict=False)
            except ValueError:
                raise ValueError("Trusted proxies are IP addresses or networks") from None
            if network.prefixlen == 0:
                raise ValueError("Trusting every address as a proxy is not allowed")
        return value

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

    @model_validator(mode="after")
    def root_cert_needs_tls(self) -> Self:
        if self.database_ssl_root_cert is not None and self.database_ssl_mode != "verify-full":
            raise ValueError("A database root certificate is used only with verify-full TLS")
        return self

    def signup_open(self) -> bool:
        if self.client_signup_open is not None:
            return self.client_signup_open
        # A name this code does not know counts as deployed.
        return self.environment in (None, "", "local")

    def database_connect_args(self) -> dict[str, object]:
        """The same certificate and host name verification in every process and in migrations."""
        if self.database_ssl_mode == "disable":
            return {"ssl": False}
        return {"ssl": ssl.create_default_context(cafile=self.database_ssl_root_cert)}
