"""The email hash key: required in production, never shown, and the keyed hash it gives."""

import base64
import secrets

import pytest
from pydantic import SecretStr, ValidationError

from app.modules.notifications.domain.email_events import address_hash, keyed_hash, keyed_hashes
from app.modules.notifications.settings import EmailHashSettings


def a_key() -> tuple[bytes, str]:
    # Made for this run. The real key comes only from the environment.
    raw = secrets.token_bytes(32)
    return raw, base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_production_refuses_to_start_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_EMAIL_HASH_KEY", raising=False)
    with pytest.raises(ValidationError, match="FINVEST_EMAIL_HASH_KEY is required"):
        EmailHashSettings(environment="production")
    with pytest.raises(ValidationError, match="FINVEST_EMAIL_HASH_KEY is required"):
        EmailHashSettings(environment="production", email_hash_key=SecretStr(""))


def test_elsewhere_no_key_means_only_the_plain_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_EMAIL_HASH_KEY", raising=False)
    assert EmailHashSettings(environment="staging").key() is None
    assert EmailHashSettings().key() is None
    assert keyed_hashes(["a" * 64], None) is None


def test_the_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    raw, text = a_key()
    monkeypatch.setenv("FINVEST_EMAIL_HASH_KEY", text)
    monkeypatch.setenv("FINVEST_ENVIRONMENT", "production")
    assert EmailHashSettings().key() == raw


def test_a_bad_key_is_refused_without_showing_it() -> None:
    for bad in ("short", base64.urlsafe_b64encode(secrets.token_bytes(16)).decode()):
        with pytest.raises(ValidationError) as refused:
            EmailHashSettings(email_hash_key=SecretStr(bad))
        assert bad not in str(refused.value)


def test_the_keyed_hash_is_fixed_by_the_key_and_the_plain_hash() -> None:
    first, _ = a_key()
    second, _ = a_key()
    plain = address_hash(" Reader@Example.com ")
    assert plain == address_hash("reader@example.com")
    keyed = keyed_hash(plain, first)
    assert len(keyed) == 64 and int(keyed, 16) >= 0
    assert keyed == keyed_hash(plain, first)
    assert keyed not in (plain, keyed_hash(plain, second))
    assert keyed_hash(address_hash("other@example.com"), first) != keyed
    assert keyed_hashes((plain,), first) == [keyed]
