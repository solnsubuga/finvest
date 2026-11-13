import itertools

import pytest

from app.modules.payouts.domain.payouts import (
    COUNTS_TOWARDS_LIMIT,
    FINAL,
    IllegalTransition,
    LedgerEffect,
    PayoutEvent,
    PayoutStatus,
    apply,
    initial_status,
    may_send,
    needs_status_read,
)

S, E, L = PayoutStatus, PayoutEvent, LedgerEffect

# The whole machine, written out a second time on purpose. A change to the rules has to be made
# in both places, so it cannot slip in unnoticed.
EXPECTED: dict[tuple[PayoutStatus, PayoutEvent], tuple[PayoutStatus, LedgerEffect]] = {
    (S.HELD, E.REVIEW_APPROVED): (S.QUEUED, L.NONE),
    (S.HELD, E.REVIEW_DECLINED): (S.CANCELLED, L.RELEASE),
    (S.HELD, E.CLIENT_CANCELLED): (S.CANCELLED, L.RELEASE),
    (S.QUEUED, E.SEND_ACCEPTED): (S.SENT, L.NONE),
    (S.QUEUED, E.SEND_CONFIRMED): (S.PAID, L.SETTLE),
    (S.QUEUED, E.SEND_REJECTED): (S.FAILED, L.PARK),
    (S.QUEUED, E.SEND_DEFERRED): (S.QUEUED, L.NONE),
    (S.QUEUED, E.SEND_UNCERTAIN): (S.UNCERTAIN, L.NONE),
    (S.SENT, E.STATUS_PAID): (S.PAID, L.SETTLE),
    (S.SENT, E.STATUS_FAILED): (S.FAILED, L.PARK),
    (S.SENT, E.STATUS_PENDING): (S.SENT, L.NONE),
    (S.SENT, E.STATUS_UNKNOWN): (S.SENT, L.NONE),
    (S.UNCERTAIN, E.STATUS_PAID): (S.PAID, L.SETTLE),
    (S.UNCERTAIN, E.STATUS_FAILED): (S.FAILED, L.PARK),
    (S.UNCERTAIN, E.STATUS_PENDING): (S.SENT, L.NONE),
    (S.UNCERTAIN, E.STATUS_UNKNOWN): (S.UNCERTAIN, L.NONE),
}
EVERY_PAIR = list(itertools.product(PayoutStatus, PayoutEvent))
SEND_RESULTS = [event for event in PayoutEvent if event.value.startswith("send_")]


@pytest.mark.parametrize(("status", "event"), EVERY_PAIR)
def test_every_status_and_event_does_exactly_what_the_table_says(
    status: PayoutStatus, event: PayoutEvent
) -> None:
    if (status, event) in EXPECTED:
        transition = apply(status, event)
        assert (transition.status, transition.effect) == EXPECTED[(status, event)]
    else:
        with pytest.raises(IllegalTransition) as refused:
            apply(status, event)
        assert (refused.value.status, refused.value.event) == (status, event)


@pytest.mark.parametrize("status", sorted(FINAL))
@pytest.mark.parametrize("event", PayoutEvent)
def test_a_finished_payout_never_changes(status: PayoutStatus, event: PayoutEvent) -> None:
    with pytest.raises(IllegalTransition):
        apply(status, event)


@pytest.mark.parametrize("event", SEND_RESULTS)
@pytest.mark.parametrize("status", [S.UNCERTAIN, S.SENT])
def test_a_payout_that_may_have_left_is_never_sent_again(
    status: PayoutStatus, event: PayoutEvent
) -> None:
    assert not may_send(status)
    with pytest.raises(IllegalTransition):
        apply(status, event)


def test_only_a_queued_payout_may_be_sent() -> None:
    assert [status for status in PayoutStatus if may_send(status)] == [S.QUEUED]


def test_a_deferred_send_stays_queued_and_may_be_sent_again() -> None:
    assert may_send(apply(S.QUEUED, E.SEND_DEFERRED).status)


def test_an_uncertain_payout_is_settled_only_by_reading_the_provider() -> None:
    leaving = {event for (status, event) in EXPECTED if status is S.UNCERTAIN}
    assert leaving == {E.STATUS_PAID, E.STATUS_FAILED, E.STATUS_PENDING, E.STATUS_UNKNOWN}
    assert needs_status_read(S.UNCERTAIN)
    assert needs_status_read(S.SENT)
    assert [s for s in PayoutStatus if needs_status_read(s)] == [S.SENT, S.UNCERTAIN]


@pytest.mark.parametrize(("status", "event"), EVERY_PAIR)
def test_money_moves_exactly_when_a_payout_finishes(
    status: PayoutStatus, event: PayoutEvent
) -> None:
    """In-flight money is moved once, on the step into a final status, and at no other time."""
    if (status, event) not in EXPECTED:
        return
    transition = apply(status, event)
    assert (transition.effect is not L.NONE) is (transition.status in FINAL)
    if transition.status in FINAL:
        assert (
            transition.effect
            is {S.PAID: L.SETTLE, S.FAILED: L.PARK, S.CANCELLED: L.RELEASE}[transition.status]
        )


def test_a_payout_is_cancelled_only_before_anything_is_sent() -> None:
    into_cancelled = {
        status for (status, _), (after, _) in EXPECTED.items() if after is S.CANCELLED
    }
    assert into_cancelled == {S.HELD}


def test_a_held_payout_reaches_the_provider_only_through_approval() -> None:
    assert initial_status(needs_review=True) is S.HELD
    assert initial_status(needs_review=False) is S.QUEUED
    assert not may_send(S.HELD)
    for event in SEND_RESULTS:
        with pytest.raises(IllegalTransition):
            apply(S.HELD, event)


def test_every_status_can_be_reached_and_every_open_one_can_finish() -> None:
    reachable = {S.HELD, S.QUEUED}
    frontier = set(reachable)
    while frontier:
        status = frontier.pop()
        for (before, _), (after, _) in EXPECTED.items():
            if before is status and after not in reachable:
                reachable.add(after)
                frontier.add(after)
    assert reachable == set(PayoutStatus)
    for status in set(PayoutStatus) - FINAL - {S.HELD}:
        assert any(
            before is status and after in FINAL for (before, _), (after, _) in EXPECTED.items()
        )


def test_failed_and_cancelled_payouts_give_the_daily_allowance_back() -> None:
    assert frozenset({S.HELD, S.QUEUED, S.SENT, S.UNCERTAIN, S.PAID}) == COUNTS_TOWARDS_LIMIT
