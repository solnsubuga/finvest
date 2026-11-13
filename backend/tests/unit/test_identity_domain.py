from datetime import UTC, datetime, timedelta

import pytest

from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_email, normalize_phone
from app.modules.identity.domain.passwords import password_problem
from app.modules.identity.domain.permissions import (
    PERMISSIONS,
    ROLE_PERMISSIONS,
    ROLES,
    allows,
    permissions_for,
)
from app.modules.identity.domain.sessions import (
    RESTRICTION,
    SessionPolicy,
    new_token,
    restricted_until,
    token_digest,
)
from app.modules.identity.hashing import PasswordHasher

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "written",
    ["0772 123 456", "+256772123456", "256772123456", "00256772123456", "(0772) 123-456"],
)
def test_a_ugandan_number_is_stored_one_way(written: str) -> None:
    assert normalize_phone(written) == "+256772123456"


def test_a_foreign_number_keeps_its_country() -> None:
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"


@pytest.mark.parametrize("written", ["", "0772", "abc", "+0772123456", "0772123456789012345"])
def test_a_bad_number_is_refused(written: str) -> None:
    with pytest.raises(InvalidIdentifier):
        normalize_phone(written)


def test_email_is_lowercased_and_checked() -> None:
    assert normalize_email("  Ada@Example.COM ") == "ada@example.com"
    for bad in ["", "ada", "ada@", "a b@example.com", "a" * 250 + "@x.io"]:
        with pytest.raises(InvalidIdentifier):
            normalize_email(bad)


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short", "too_short"),
        ("nine-char", "too_short"),
        ("x" * 129, "too_long"),
        ("password123", "leaked_password"),
        ("P@ssw0rd2024!", "leaked_password"),
        ("1234567890", "leaked_password"),
        ("qwertyuiop", "leaked_password"),
        ("aaaaaaaaaaaa", "leaked_password"),
        ("welcomeadmin", "leaked_password"),
        ("Finvest2026!", "leaked_password"),
        ("0772123456ab", "leaked_password"),
        ("ada@example.com1", "leaked_password"),
        ("copper-harbour-violin", None),
        ("Tr4ctor!Mango#Lake", None),
    ],
)
def test_password_rules(password: str, problem: str | None) -> None:
    about = ("+256772123456", "ada@example.com")
    assert password_problem(password, about) == problem


def test_every_role_is_listed_and_admin_holds_everything() -> None:
    assert set(ROLE_PERMISSIONS) == set(ROLES)
    assert permissions_for("admin") == list(PERMISSIONS)
    for role in ROLES:
        assert ROLE_PERMISSIONS[role] <= set(PERMISSIONS)
        assert permissions_for(role) == [p for p in PERMISSIONS if allows(role, p)]


def test_support_cannot_see_money_movement_or_the_team() -> None:
    assert allows("support", "clients.view")
    assert not allows("support", "money_movement.view")
    assert not allows("support", "team.view")
    assert not allows("operations", "team.view")


def test_a_session_ends_when_idle_and_at_its_absolute_limit() -> None:
    policy = SessionPolicy(timedelta(minutes=15), timedelta(hours=12))
    idle, absolute = policy.expiry(NOW, NOW)
    assert (idle, absolute) == (NOW + timedelta(minutes=15), NOW + timedelta(hours=12))
    # Use near the end cannot push the session past its absolute limit.
    late = NOW + timedelta(hours=11, minutes=55)
    assert policy.expiry(NOW, late) == (absolute, absolute)
    with pytest.raises(ValueError, match="idle"):
        SessionPolicy(timedelta(hours=2), timedelta(hours=1))


def test_only_a_code_restricts_the_account() -> None:
    assert restricted_until("code", NOW) == NOW + RESTRICTION
    assert restricted_until("passkey", NOW) is None
    assert restricted_until("signup", NOW) is None


def test_tokens_are_long_random_and_stored_as_a_digest() -> None:
    first, second = new_token(), new_token()
    assert first != second
    assert len(first) >= 43
    assert len(token_digest(first)) == 32
    assert token_digest(first) != token_digest(second)


async def test_password_hashing() -> None:
    hasher = PasswordHasher(memory_kib=19_456, time_cost=2)
    hashed = await hasher.hash("copper-harbour-violin")
    assert hashed.startswith("$argon2id$")
    assert "copper" not in hashed
    assert await hasher.verify("copper-harbour-violin", hashed)
    assert not await hasher.verify("copper-harbour-viola", hashed)
    # No such account: the same work, and never a match.
    assert not await hasher.verify("copper-harbour-violin", None)
    assert not await hasher.verify("anything", "not-a-hash")
    assert not hasher.needs_rehash(hashed)
    assert PasswordHasher(memory_kib=32_768, time_cost=3).needs_rehash(hashed)


def test_weak_hash_parameters_are_refused() -> None:
    with pytest.raises(ValueError, match="minimum"):
        PasswordHasher(memory_kib=1024)
