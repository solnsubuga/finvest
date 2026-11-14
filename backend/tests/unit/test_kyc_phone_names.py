"""The rules for looking up the name the network holds for a client's phone number."""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.kyc.domain.phone_names import (
    MAX_LOOKUPS,
    PhoneName,
    ask_again,
    msisdn,
    phone_name,
)
from app.modules.payouts.domain.destinations import NameMatch
from app.modules.payouts.domain.name_lookups import STALE_AFTER, LookupStatus

NOW = datetime(2026, 11, 14, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("+256772123456", "256772123456"),
        ("+256700000000", "256700000000"),
        ("+254712345678", None),  # Kenya: no Ugandan network to ask
        ("+25677212345", None),
        ("+2567721234567", None),
        ("256772123456", None),
        ("+256 77212345", None),
    ],
)
def test_only_a_ugandan_number_is_looked_up(phone: str, expected: str | None) -> None:
    assert msisdn(phone) == expected


def test_the_first_hand_in_asks() -> None:
    assert ask_again(None, None, 0, NOW)


@pytest.mark.parametrize("status", [LookupStatus.FOUND, LookupStatus.NO_NAME])
def test_an_answer_is_never_asked_again(status: LookupStatus) -> None:
    assert not ask_again(status, NOW - timedelta(days=30), 1, NOW)


def test_no_answer_is_asked_again_up_to_the_limit() -> None:
    for count in range(1, MAX_LOOKUPS):
        assert ask_again(LookupStatus.UNANSWERED, NOW, count, NOW)
    assert not ask_again(LookupStatus.UNANSWERED, NOW, MAX_LOOKUPS, NOW)
    assert not ask_again(None, None, MAX_LOOKUPS, NOW)


@pytest.mark.parametrize("status", [LookupStatus.QUEUED, LookupStatus.SENT])
def test_one_on_its_way_is_not_asked_again_until_its_job_has_stopped(
    status: LookupStatus,
) -> None:
    assert not ask_again(status, NOW - STALE_AFTER + timedelta(seconds=1), 1, NOW)
    assert not ask_again(status, None, 1, NOW)
    assert ask_again(status, NOW - STALE_AFTER, 1, NOW)
    assert not ask_again(status, NOW - STALE_AFTER, MAX_LOOKUPS, NOW)


@pytest.mark.parametrize(
    ("status", "match", "expected"),
    [
        (LookupStatus.QUEUED, None, PhoneName.CHECKING),
        (LookupStatus.SENT, None, PhoneName.CHECKING),
        (LookupStatus.FOUND, NameMatch.EXACT, PhoneName.MATCHES),
        (LookupStatus.FOUND, NameMatch.PARTIAL, PhoneName.PARTLY_MATCHES),
        (LookupStatus.FOUND, NameMatch.DIFFERENT, PhoneName.DIFFERENT),
        (LookupStatus.FOUND, None, PhoneName.NOT_ANSWERED),
        (LookupStatus.NO_NAME, None, PhoneName.NO_NAME),
        (LookupStatus.UNANSWERED, None, PhoneName.NOT_ANSWERED),
    ],
)
def test_what_staff_see(status: LookupStatus, match: NameMatch | None, expected: PhoneName) -> None:
    assert phone_name(status, match) is expected
