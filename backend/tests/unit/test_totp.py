from datetime import UTC, datetime, timedelta

import pytest

from app.modules.identity.domain import totp

# RFC 6238 appendix B, SHA-1, truncated to six digits.
RFC_SECRET = b"12345678901234567890"
NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("seconds", "code"),
    [
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ],
)
def test_codes_match_the_standard(seconds: int, code: str) -> None:
    assert totp.code_at(RFC_SECRET, seconds // 30) == code


def test_the_current_code_and_its_neighbours_are_accepted() -> None:
    secret = totp.new_secret()
    step = totp.step_at(NOW)
    for drift in (-1, 0, 1):
        assert totp.matching_step(secret, totp.code_at(secret, step + drift), NOW, None) == (
            step + drift
        )
    for drift in (-2, 2):
        assert totp.matching_step(secret, totp.code_at(secret, step + drift), NOW, None) is None


def test_a_code_is_accepted_once_and_older_ones_never_again() -> None:
    secret = totp.new_secret()
    step = totp.step_at(NOW)
    code = totp.code_at(secret, step)
    assert totp.matching_step(secret, code, NOW, step) is None
    assert totp.matching_step(secret, totp.code_at(secret, step - 1), NOW, step) is None
    later = NOW + timedelta(seconds=30)
    assert totp.matching_step(secret, totp.code_at(secret, step + 1), later, step) == step + 1


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12345a", "١٢٣٤٥٦", "-12345"])
def test_anything_but_six_digits_is_refused(code: str) -> None:
    assert totp.matching_step(RFC_SECRET, code, NOW, None) is None


def test_a_space_in_the_middle_is_allowed() -> None:
    secret = totp.new_secret()
    code = totp.code_at(secret, totp.step_at(NOW))
    assert totp.matching_step(secret, f"{code[:3]} {code[3:]}", NOW, None) is not None


def test_the_setup_address_is_what_authenticator_apps_expect() -> None:
    assert totp.secret_text(RFC_SECRET) == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.setup_uri(RFC_SECRET, "ada@finvestug.com") == (
        "otpauth://totp/Finvest%3Aada%40finvestug.com?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        "&issuer=Finvest&algorithm=SHA1&digits=6&period=30"
    )
    assert len(totp.new_secret()) == 20
    assert totp.new_secret() != totp.new_secret()
