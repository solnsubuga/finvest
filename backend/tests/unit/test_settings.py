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


def test_tls_is_off_only_when_disabled() -> None:
    assert settings(URL).database_connect_args() == {"ssl": False}
    context = Settings(database_url=URL, database_ssl_mode="verify-full").database_connect_args()[
        "ssl"
    ]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname


def test_a_root_certificate_needs_verified_tls(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="requires verify-full"):
        Settings(database_url=URL, database_ssl_root_cert=tmp_path / "ca.crt")


def test_a_missing_root_certificate_fails_instead_of_falling_back(tmp_path: Path) -> None:
    value = Settings(
        database_url=URL,
        database_ssl_mode="verify-full",
        database_ssl_root_cert=tmp_path / "missing.crt",
    )
    with pytest.raises(FileNotFoundError):
        value.database_connect_args()
