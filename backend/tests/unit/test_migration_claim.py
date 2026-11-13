import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.migration.domain.claim import (
    CLAIMED,
    LINK_LIFETIME,
    MAX_REMINDERS,
    Channel,
    ClaimStatus,
    IdentityStep,
    Link,
    LinkProblem,
    can_move,
    can_see_investments,
    can_transact,
    hash_token,
    identity_step,
    invite_channels,
    link_problem,
    mask_phone,
    problem_text,
    progress,
    reminder_due,
)

S = ClaimStatus
NOW = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)
TOKEN = "a" * 43
LINK = Link(hash_token(TOKEN), sent_at=NOW)

ALLOWED = {
    ("staff", S.NOT_INVITED, S.INVITED),
    ("system", S.NOT_INVITED, S.INVITED),
    ("client", S.INVITED, S.SIGNED_IN),
    ("client", S.SIGNED_IN, S.IDENTITY_SUBMITTED),
    ("system", S.IDENTITY_SUBMITTED, S.IDENTITY_CONFIRMED),
    ("system", S.IDENTITY_SUBMITTED, S.SIGNED_IN),
    ("client", S.IDENTITY_CONFIRMED, S.CONFIRMED),
    ("client", S.IDENTITY_CONFIRMED, S.PROBLEM_REPORTED),
    ("staff", S.PROBLEM_REPORTED, S.IDENTITY_CONFIRMED),
}


def test_only_the_listed_moves_are_allowed() -> None:
    for actor, current, target in itertools.product(("client", "staff", "system"), S, S):
        assert can_move(actor, current, target) == ((actor, current, target) in ALLOWED)  # type: ignore[arg-type]


def test_nobody_but_the_client_confirms_and_nobody_skips_identity() -> None:
    for actor in ("staff", "system"):
        assert not any(can_move(actor, status, S.CONFIRMED) for status in S)
    assert not can_move("client", S.SIGNED_IN, S.CONFIRMED)
    assert not can_move("client", S.IDENTITY_SUBMITTED, S.IDENTITY_CONFIRMED)
    assert not any(can_move("client", S.CONFIRMED, status) for status in S)


def test_a_signed_in_client_sees_investments_but_transacts_only_once_identified() -> None:
    assert [s for s in S if can_see_investments(s)] == [s for s in S if s in CLAIMED]
    assert not can_see_investments(S.INVITED)
    assert [s for s in S if can_transact(s)] == [
        S.IDENTITY_CONFIRMED,
        S.CONFIRMED,
        S.PROBLEM_REPORTED,
    ]


def test_the_identity_step_is_a_selfie_unless_the_id_must_be_redone() -> None:
    assert identity_step(needs_id_check=False) is IdentityStep.SELFIE
    assert identity_step(needs_id_check=True) is IdentityStep.FULL_ID_CHECK


def test_a_fresh_link_opens_the_claim_until_72_hours_pass() -> None:
    assert timedelta(hours=72) == LINK_LIFETIME
    assert link_problem(LINK, TOKEN, S.INVITED, NOW) is None
    last = NOW + LINK_LIFETIME - timedelta(seconds=1)
    assert link_problem(LINK, TOKEN, S.INVITED, last) is None
    assert link_problem(LINK, TOKEN, S.INVITED, NOW + LINK_LIFETIME) is LinkProblem.EXPIRED


@pytest.mark.parametrize("token", ["b" * 43, "a" * 42, "", "a" * 42 + "!", "a" * 44])
def test_a_wrong_or_malformed_token_is_not_found(token: str) -> None:
    assert link_problem(LINK, token, S.INVITED, NOW) is LinkProblem.NOT_FOUND


def test_a_link_works_once_and_a_fresh_link_cancels_the_old_one() -> None:
    used = Link(LINK.token_hash, NOW, used_at=NOW)
    cancelled = Link(LINK.token_hash, NOW, cancelled_at=NOW)
    assert link_problem(None, TOKEN, S.INVITED, NOW) is LinkProblem.NOT_FOUND
    assert link_problem(used, TOKEN, S.SIGNED_IN, NOW) is LinkProblem.USED
    assert link_problem(cancelled, TOKEN, S.INVITED, NOW) is LinkProblem.CANCELLED
    for status in CLAIMED:
        assert link_problem(LINK, TOKEN, status, NOW) is LinkProblem.ALREADY_CLAIMED


def test_only_the_hash_of_a_token_is_kept() -> None:
    assert TOKEN not in repr(LINK)
    assert len(hash_token(TOKEN)) == 64
    assert hash_token(TOKEN) != hash_token("b" * 43)


def test_where_a_link_is_sent() -> None:
    assert invite_channels(phone="+256772123456", email="a@example.com") == (
        Channel.SMS,
        Channel.EMAIL,
    )
    assert invite_channels(phone="+256772123456", email=None) == (Channel.SMS,)
    # The claim confirms the phone by code, so an email alone cannot start one.
    assert invite_channels(phone=None, email="a@example.com") == ()
    assert invite_channels(phone=None, email=None) == ()


def test_reminders_go_weekly_to_clients_who_have_not_used_a_link_and_then_stop() -> None:
    week = NOW + timedelta(days=7)
    assert reminder_due(S.INVITED, last_sent_at=NOW, reminders_sent=0, now=week)
    assert not reminder_due(
        S.INVITED, last_sent_at=NOW, reminders_sent=0, now=week - timedelta(seconds=1)
    )
    assert reminder_due(S.INVITED, last_sent_at=NOW, reminders_sent=MAX_REMINDERS - 1, now=week)
    assert not reminder_due(S.INVITED, last_sent_at=NOW, reminders_sent=MAX_REMINDERS, now=week)
    for status in set(S) - {S.INVITED}:
        assert not reminder_due(status, last_sent_at=NOW, reminders_sent=0, now=week)


def test_the_phone_is_shown_masked() -> None:
    assert mask_phone("+256772123456") == "+256 772 *** 456"
    assert mask_phone("+447911123456") == "+447 911 *** 456"
    assert "123" not in mask_phone("+256772123456")
    for bad in ("0772123456", "+25677", "+256abc123456"):
        with pytest.raises(ValueError, match="phone"):
            mask_phone(bad)


def test_what_the_client_wrote_is_tidied_and_bounded() -> None:
    assert problem_text("  One investment \n is missing ") == "One investment is missing"
    for bad in ("", "  \n ", "x" * 1001):
        with pytest.raises(ValueError, match="problem_text"):
            problem_text(bad)


def test_progress_counts_for_the_console() -> None:
    result = progress(
        {
            S.NOT_INVITED: 5,
            S.INVITED: 600,
            S.SIGNED_IN: 50,
            S.IDENTITY_SUBMITTED: 30,
            S.IDENTITY_CONFIRMED: 19,
            S.CONFIRMED: 1204,
            S.PROBLEM_REPORTED: 9,
        }
    )
    assert result.invited == 1912
    assert result.claimed_and_confirmed == 1204
    assert result.said_something_is_wrong == 9
    assert result.not_yet_claimed == 699
    assert result.cannot_be_invited == 5
    assert (
        result.claimed_and_confirmed + result.said_something_is_wrong + result.not_yet_claimed
        == result.invited
    )
    assert progress({}).invited == 0
