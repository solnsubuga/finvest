from datetime import date, timedelta

import pytest

from app.modules.dealing.domain.buffer_changes import (
    MAX_REASON_LENGTH,
    ProposalStatus,
    Refusal,
    RulesProposal,
    check_approval,
    check_proposal,
    check_send_back,
    check_withdrawal,
    valid_reason,
)
from app.modules.dealing.domain.liquidity import BufferRules
from app.shared.money import Currency, Money

TODAY = date(2026, 11, 19)
M = 1_000_000


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def rules(effective_from: date, target: int = 150 * M, *, days: int = 2) -> BufferRules:
    return BufferRules(effective_from, ugx(target), ugx(100 * M), ugx(5 * M), days)


SEEDED = rules(date(2026, 1, 1))
TOMORROW = TODAY + timedelta(days=1)


def proposal(new: BufferRules, status: ProposalStatus = ProposalStatus.PROPOSED) -> RulesProposal:
    return RulesProposal(new, "daniel", "Withdrawals are rising", status)


@pytest.mark.parametrize(
    ("reason", "valid"),
    [
        ("Rising", True),
        ("", False),
        ("   ", False),
        ("x" * MAX_REASON_LENGTH, True),
        ("x" * (MAX_REASON_LENGTH + 1), False),
    ],
)
def test_a_reason_is_needed_and_has_a_limit(reason: str, valid: bool) -> None:
    assert valid_reason(reason) is valid


def test_a_new_version_from_tomorrow_may_be_proposed() -> None:
    assert check_proposal(rules(TOMORROW, 160 * M), "Rising", [SEEDED], TODAY) is None


@pytest.mark.parametrize("effective_from", [TODAY, TODAY - timedelta(days=1)])
def test_a_change_never_starts_today_or_earlier(effective_from: date) -> None:
    refusal = check_proposal(rules(effective_from, 160 * M), "Rising", [SEEDED], TODAY)
    assert refusal is Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE


def test_the_reason_is_checked_first() -> None:
    assert check_proposal(rules(TODAY), " ", [SEEDED], TODAY) is Refusal.REASON_REQUIRED


def test_two_versions_never_start_on_the_same_day() -> None:
    taken = rules(TOMORROW, 170 * M)
    refusal = check_proposal(rules(TOMORROW, 160 * M), "Rising", [SEEDED, taken], TODAY)
    assert refusal is Refusal.DATE_ALREADY_TAKEN


def test_a_version_the_same_as_the_one_before_it_changes_nothing() -> None:
    assert check_proposal(rules(TOMORROW), "Rising", [SEEDED], TODAY) is Refusal.NOTHING_CHANGES


def test_the_version_compared_is_the_latest_before_the_new_date() -> None:
    later = rules(TOMORROW + timedelta(days=30), 170 * M)
    # Between the seed and the later version, the seed's values change nothing.
    middle = rules(TOMORROW + timedelta(days=10))
    assert check_proposal(middle, "Rising", [later, SEEDED], TODAY) is Refusal.NOTHING_CHANGES
    # After the later version, going back to the seed's values is a change.
    after = rules(TOMORROW + timedelta(days=40))
    assert check_proposal(after, "Rising", [SEEDED, later], TODAY) is None


def test_a_change_to_the_days_alone_is_a_change() -> None:
    assert check_proposal(rules(TOMORROW, days=3), "Rising", [SEEDED], TODAY) is None


def test_the_first_version_of_a_currency_has_nothing_to_compare() -> None:
    assert check_proposal(rules(TOMORROW), "First", [], TODAY) is None


def test_a_second_person_approves() -> None:
    assert check_approval(proposal(rules(TOMORROW, 160 * M)), "grace", [SEEDED], TODAY) is None


def test_nobody_approves_their_own_change() -> None:
    refusal = check_approval(proposal(rules(TOMORROW, 160 * M)), "daniel", [SEEDED], TODAY)
    assert refusal is Refusal.SAME_PERSON


@pytest.mark.parametrize(
    "status", [ProposalStatus.APPROVED, ProposalStatus.SENT_BACK, ProposalStatus.WITHDRAWN]
)
def test_a_decided_change_is_never_decided_again(status: ProposalStatus) -> None:
    decided = proposal(rules(TOMORROW, 160 * M), status)
    assert check_approval(decided, "grace", [SEEDED], TODAY) is Refusal.ALREADY_DECIDED
    assert check_send_back(decided, "grace", "No") is Refusal.ALREADY_DECIDED
    assert check_withdrawal(decided, "daniel") is Refusal.ALREADY_DECIDED


def test_approval_on_or_after_the_effective_date_is_too_late() -> None:
    waiting = proposal(rules(TOMORROW, 160 * M))
    refusal = check_approval(waiting, "grace", [SEEDED], TOMORROW)
    assert refusal is Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE


def test_approval_checks_clashes_again() -> None:
    waiting = proposal(rules(TOMORROW + timedelta(days=5), 160 * M))
    approved_since = rules(TOMORROW + timedelta(days=5), 170 * M)
    refusal = check_approval(waiting, "grace", [SEEDED, approved_since], TODAY)
    assert refusal is Refusal.DATE_ALREADY_TAKEN


def test_sending_back_needs_a_second_person_and_a_note() -> None:
    waiting = proposal(rules(TOMORROW, 160 * M))
    assert check_send_back(waiting, "grace", "Too high") is None
    assert check_send_back(waiting, "daniel", "Too high") is Refusal.SAME_PERSON
    assert check_send_back(waiting, "grace", "  ") is Refusal.REASON_REQUIRED


def test_only_the_proposer_withdraws() -> None:
    waiting = proposal(rules(TOMORROW, 160 * M))
    assert check_withdrawal(waiting, "daniel") is None
    assert check_withdrawal(waiting, "grace") is Refusal.NOT_THE_PROPOSER


def test_only_a_waiting_change_is_not_final() -> None:
    assert [status for status in ProposalStatus if not status.is_final] == [ProposalStatus.PROPOSED]
