import ssl
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.infrastructure.settings import Settings

SECRET = "s3cret-value"


def settings(url: str) -> Settings:
    return Settings(database_url=url)


def test_plain_postgres_urls_use_the_async_driver() -> None:
    value = settings(f"postgresql://finvest_app:{SECRET}@db:5432/finvest")
    assert value.database_url.get_secret_value().startswith("postgresql+asyncpg://")


def test_the_password_is_not_in_the_representation() -> None:
    assert SECRET not in repr(settings(f"postgresql://finvest_app:{SECRET}@db/finvest"))


@pytest.mark.parametrize(
    "url",
    [
        f"mysql://u:{SECRET}@db/finvest",
        f"postgresql://u:{SECRET}@db",
        f"postgresql://u:{SECRET}@db/finvest?sslmode=disable",
        f"not a url {SECRET}",
    ],
)
def test_bad_urls_are_refused_without_echoing_them(url: str) -> None:
    with pytest.raises(ValidationError) as raised:
        settings(url)
    assert SECRET not in str(raised.value)


def test_the_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


URL = f"postgresql://finvest_app:{SECRET}@db/finvest"


def test_tls_is_verified_unless_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_DATABASE_SSL_MODE", raising=False)
    context = settings(URL).database_connect_args()["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname
    disabled = Settings(database_url=URL, database_ssl_mode="disable")
    assert disabled.database_connect_args() == {"ssl": False}


def test_a_root_certificate_needs_verified_tls(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="only with verify-full"):
        Settings(
            database_url=URL,
            database_ssl_mode="disable",
            database_ssl_root_cert=tmp_path / "ca.crt",
        )


def test_a_missing_root_certificate_fails_instead_of_falling_back(tmp_path: Path) -> None:
    value = Settings(
        database_url=URL,
        database_ssl_mode="verify-full",
        database_ssl_root_cert=tmp_path / "missing.crt",
    )
    with pytest.raises(FileNotFoundError):
        value.database_connect_args()


URL = "postgresql://finvest_app:unused@db/finvest"


def test_allowed_origins_are_exact() -> None:
    value = Settings(
        database_url=URL,
        client_allowed_origins=["https://app.finvestug.com", "http://localhost:8081"],
    )
    assert value.client_allowed_origins[0] == "https://app.finvestug.com"
    for bad in ["*", "app.finvestug.com", "https://app.finvestug.com/", "ftp://x"]:
        with pytest.raises(ValidationError):
            Settings(database_url=URL, staff_allowed_origins=[bad])


def test_trusted_proxies_are_addresses_and_never_everyone() -> None:
    assert Settings(database_url=URL, trusted_proxies=["10.0.0.5", "172.16.0.0/12"])
    for bad in ["caddy", "0.0.0.0/0", "::/0"]:
        with pytest.raises(ValidationError):
            Settings(database_url=URL, trusted_proxies=[bad])


def test_origins_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_DATABASE_URL", URL)
    monkeypatch.setenv("FINVEST_STAFF_ALLOWED_ORIGINS", '["https://admin.finvestug.com"]')
    assert Settings().staff_allowed_origins == ["https://admin.finvestug.com"]


def test_the_authenticator_key_must_be_32_bytes_and_is_never_shown() -> None:
    import secrets

    key = secrets.token_urlsafe(32)
    value = Settings(database_url=URL, authenticator_key=key)
    assert value.authenticator_key is not None
    assert key not in repr(value)
    assert Settings(database_url=URL, authenticator_key="").authenticator_key is None
    for bad in ["short", secrets.token_urlsafe(16), "not base64 !!" * 4]:
        with pytest.raises(ValidationError) as raised:
            Settings(database_url=URL, authenticator_key=bad)
        assert bad not in str(raised.value)
