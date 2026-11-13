"""Every state a deposit can be in, against everything a provider can say."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from app.modules.payments.domain.deposits import (
    APPROVAL_WINDOW,
    TRANSITIONS,
    Code,
    DepositStatus,
    InvalidDeposit,
    Move,
    after_send,
    after_status,
    can_move,
    check_mobile_money_amount,
    mask_msisdn,
    normalise_msisdn,
)
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    InvalidOutcome,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    SendResult,
    StatusReading,
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.shared.money import Currency, Money

S = DepositStatus
SENT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
SOON = SENT + timedelta(seconds=30)
LATE = SENT + APPROVAL_WINDOW
AMOUNT = Money(50_000, Currency.UGX)
REF = "prov-1"

READINGS: dict[str, StatusReading] = {
    "pending": Pending(REF),
    "confirmed": Confirmed(REF, AMOUNT),
    "confirmed_less": Confirmed(REF, Money(49_999, Currency.UGX)),
    "confirmed_more": Confirmed(REF, Money(50_001, Currency.UGX)),
    "confirmed_usd": Confirmed(REF, Money(50_000, Currency.USD)),
    "rejected": Rejected(RejectionCode.DECLINED, REF),
    "not_found": NotFound(),
    "unavailable": Unavailable(),
}


def read(current: S, reading: str, now: datetime = SOON) -> Move | None:
    return after_status(current, READINGS[reading], expected=AMOUNT, sent_at=SENT, now=now)


# Sending


@pytest.mark.parametrize("last_attempt", [False, True])
def test_an_accepted_request_waits_for_the_payer(last_attempt: bool) -> None:
    assert after_send(Accepted(REF), last_attempt=last_attempt) == Move(
        S.AWAITING_APPROVAL, provider_reference=REF
    )


@pytest.mark.parametrize("code", list(RejectionCode))
def test_a_refused_request_fails_with_the_providers_reason(code: RejectionCode) -> None:
    assert after_send(Rejected(code), last_attempt=False) == Move(S.FAILED, code=code.value)


def test_a_deferred_request_goes_back_to_be_sent_later() -> None:
    assert after_send(Deferred(), last_attempt=False) == Move(S.CREATED)


def test_a_request_deferred_on_its_last_attempt_fails_with_no_money_moved() -> None:
    assert after_send(Deferred(), last_attempt=True) == Move(
        S.FAILED, code=Code.PROVIDER_UNAVAILABLE.value
    )


@pytest.mark.parametrize("reason", list(UncertainReason))
@pytest.mark.parametrize("last_attempt", [False, True])
def test_an_unclear_answer_is_never_sent_again(reason: UncertainReason, last_attempt: bool) -> None:
    move = after_send(Uncertain(reason), last_attempt=last_attempt)
    assert move == Move(S.UNCERTAIN, code=Code.SEND_UNCERTAIN.value)


def test_every_send_result_is_a_move_a_sending_deposit_may_make() -> None:
    results: list[SendResult] = [
        Accepted(REF),
        Rejected(RejectionCode.OTHER),
        Deferred(),
        Uncertain(UncertainReason.TIMED_OUT),
    ]
    for result, last in product(results, [False, True]):
        assert can_move(S.SENDING, after_send(result, last_attempt=last).status)


# Reading the status back


def test_a_confirmation_for_the_exact_amount_posts_it() -> None:
    for current in (S.AWAITING_APPROVAL, S.UNCERTAIN):
        assert read(current, "confirmed") == Move(S.CONFIRMED, provider_reference=REF, post=AMOUNT)


@pytest.mark.parametrize("reading", ["confirmed_less", "confirmed_more", "confirmed_usd"])
def test_a_confirmation_for_any_other_amount_posts_nothing(reading: str) -> None:
    assert read(S.AWAITING_APPROVAL, reading) == Move(
        S.UNCERTAIN, code=Code.AMOUNT_MISMATCH.value, provider_reference=REF
    )
    assert read(S.UNCERTAIN, reading) is None


def test_a_rejection_fails_the_deposit() -> None:
    for current in (S.AWAITING_APPROVAL, S.UNCERTAIN):
        assert read(current, "rejected") == Move(S.FAILED, code="declined", provider_reference=REF)


def test_a_pending_prompt_is_left_alone_until_the_window_closes() -> None:
    assert read(S.AWAITING_APPROVAL, "pending") is None
    assert read(S.AWAITING_APPROVAL, "pending", LATE - timedelta(seconds=1)) is None
    assert read(S.AWAITING_APPROVAL, "pending", LATE) == Move(
        S.UNCERTAIN, code=Code.APPROVAL_OVERDUE.value, provider_reference=REF
    )


def test_an_uncertain_deposit_the_provider_is_prompting_for_waits_for_the_payer() -> None:
    assert read(S.UNCERTAIN, "pending") == Move(S.AWAITING_APPROVAL, provider_reference=REF)
    # After the window it stays with reconciliation, so the two states cannot ping-pong.
    assert read(S.UNCERTAIN, "pending", LATE) is None


def test_a_deposit_the_provider_does_not_know_becomes_uncertain_never_failed() -> None:
    assert read(S.AWAITING_APPROVAL, "not_found") == Move(
        S.UNCERTAIN, code=Code.UNKNOWN_TO_PROVIDER.value
    )
    assert read(S.UNCERTAIN, "not_found") is None


def test_a_failed_read_changes_nothing() -> None:
    assert read(S.AWAITING_APPROVAL, "unavailable") is None
    assert read(S.AWAITING_APPROVAL, "unavailable", LATE) is None
    assert read(S.UNCERTAIN, "unavailable") is None


@pytest.mark.parametrize("current", [S.CREATED, S.SENDING, S.CONFIRMED, S.FAILED])
@pytest.mark.parametrize("reading", list(READINGS))
def test_a_read_never_moves_an_unsent_half_sent_or_final_deposit(current: S, reading: str) -> None:
    assert read(current, reading) is None
    assert read(current, reading, LATE) is None


@pytest.mark.parametrize("current", list(S))
@pytest.mark.parametrize("reading", list(READINGS))
@pytest.mark.parametrize("now", [SOON, LATE])
def test_every_decision_is_an_allowed_move(current: S, reading: str, now: datetime) -> None:
    move = read(current, reading, now)
    if move is not None:
        assert can_move(current, move.status)
        # Only an exact confirmation ever posts.
        assert (move.post is not None) == (reading == "confirmed" and move.status is S.CONFIRMED)


def test_final_states_allow_no_move_and_nothing_returns_to_created_but_a_deferral() -> None:
    assert TRANSITIONS[S.CONFIRMED] == TRANSITIONS[S.FAILED] == frozenset()
    assert [status for status in S if S.CREATED in TRANSITIONS[status]] == [S.SENDING]
    assert set(TRANSITIONS) == set(S)


def test_a_move_cannot_confirm_without_posting_or_fail_without_a_reason() -> None:
    builds: list[Callable[[], object]] = [
        lambda: Move(S.CONFIRMED),
        lambda: Move(S.FAILED),
        lambda: Move(S.UNCERTAIN),
        lambda: Move(S.AWAITING_APPROVAL, post=AMOUNT),
        lambda: Move(S.CREATED, code="declined"),
    ]
    for build in builds:
        with pytest.raises(ValueError):
            build()


# Requests


@pytest.mark.parametrize(
    "given", ["0772123456", "256772123456", "+256772123456", "+256 772 123 456", "0772 123456"]
)
def test_ugandan_numbers_are_stored_in_one_form(given: str) -> None:
    assert normalise_msisdn(given) == "+256772123456"


@pytest.mark.parametrize(
    "given",
    ["", "772123456", "07721234567", "077212345", "+254772123456", "0772-123456", "07721O3456"],
)
def test_other_numbers_are_refused(given: str) -> None:
    with pytest.raises(InvalidDeposit) as refused:
        normalise_msisdn(given)
    assert refused.value.code == "invalid_phone"
    assert given not in str(refused.value) or not given


def test_a_masked_number_shows_only_its_ends() -> None:
    assert mask_msisdn("+256772123456") == "+256 77• ••• •56"


def test_mobile_money_takes_positive_shillings_only() -> None:
    check_mobile_money_amount(Money(1, Currency.UGX))
    for amount, code in [
        (Money(0, Currency.UGX), "invalid_amount"),
        (Money(-5, Currency.UGX), "invalid_amount"),
        (Money(500, Currency.USD), "unsupported_currency"),
    ]:
        with pytest.raises(InvalidDeposit) as refused:
            check_mobile_money_amount(amount)
        assert refused.value.code == code


# What an adapter may hand back


def test_outcomes_refuse_values_an_adapter_must_not_pass_on() -> None:
    builds: list[Callable[[], object]] = [
        lambda: Accepted(""),
        lambda: Accepted("has space"),
        lambda: Accepted("x" * 129),
        lambda: Pending("+256772123456"),
        lambda: Rejected("declined"),  # type: ignore[arg-type]
        lambda: Uncertain("timeout"),  # type: ignore[arg-type]
        lambda: Deferred(retry_after_seconds=-1),
        lambda: Deferred(retry_after_seconds=1.5),  # type: ignore[arg-type]
        lambda: Confirmed(REF, Money(0, Currency.UGX)),
        lambda: Confirmed(REF, 50_000),  # type: ignore[arg-type]
    ]
    for build in builds:
        with pytest.raises(InvalidOutcome):
            build()
