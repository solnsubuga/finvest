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
