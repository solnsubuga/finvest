"""Sending a payout: what each answer from the provider leads to, and the sandbox's script."""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.payments.domain.deposits import Network
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    DeferReason,
    Deferred,
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
from app.modules.payouts.domain import payouts as machine
from app.modules.payouts.domain.payouts import PayoutEvent, PayoutStatus
from app.modules.payouts.domain.provider import DisbursementRequest
from app.modules.payouts.domain.sending import (
    AMOUNT_MISMATCH,
    READ_WINDOW,
    AttemptOutcome,
    after_send,
    after_status,
    destination_label,
    next_read,
)
from app.modules.payouts.providers.sandbox import SEND_TIME, SandboxDisbursements
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock

E, OUT, S = PayoutEvent, AttemptOutcome, PayoutStatus
AMOUNT = Money(250_000, Currency.UGX)


@pytest.mark.parametrize(
    ("result", "event", "outcome", "reference", "code"),
    [
        (Accepted("R1"), E.SEND_ACCEPTED, OUT.ACCEPTED, "R1", None),
        (
            Rejected(RejectionCode.LIMIT_EXCEEDED, "R2"),
            E.SEND_REJECTED,
            OUT.REJECTED,
            "R2",
            "limit_exceeded",
        ),
        (Deferred(), E.SEND_DEFERRED, OUT.DEFERRED, None, None),
        (Uncertain(UncertainReason.CONNECTION_LOST), E.SEND_UNCERTAIN, OUT.UNCERTAIN, None, None),
    ],
)
def test_each_send_answer_names_its_event_and_outcome(
    result: SendResult,
    event: PayoutEvent,
    outcome: AttemptOutcome,
    reference: str | None,
    code: str | None,
) -> None:
    step = after_send(result)
    assert (step.event, step.outcome, step.provider_reference, step.failure_code) == (
        event,
        outcome,
        reference,
        code,
    )
    # Every event a send can lead to is a legal step for a queued payout.
    assert machine.apply(S.QUEUED, step.event).status in {S.QUEUED, S.SENT, S.FAILED, S.UNCERTAIN}


def test_only_a_deferral_leaves_the_payout_free_to_be_sent_again() -> None:
    for result in (
        Accepted("R"),
        Rejected(RejectionCode.OTHER),
        Uncertain(UncertainReason.TIMED_OUT),
    ):
        assert not machine.may_send(machine.apply(S.QUEUED, after_send(result).event).status)
    assert machine.may_send(machine.apply(S.QUEUED, after_send(Deferred()).event).status)


@pytest.mark.parametrize(
    ("reading", "event", "again", "code"),
    [
        (Pending("R"), E.STATUS_PENDING, True, None),
        (Confirmed("R", AMOUNT), E.STATUS_PAID, False, None),
        (
            Rejected(RejectionCode.ACCOUNT_NOT_FOUND, "R"),
            E.STATUS_FAILED,
            False,
            "account_not_found",
        ),
        (NotFound(), E.STATUS_UNKNOWN, True, None),
        (Unavailable(), E.STATUS_UNKNOWN, True, None),
    ],
)
def test_each_status_reading_names_its_event(
    reading: StatusReading, event: PayoutEvent, again: bool, code: str | None
) -> None:
    step = after_status(reading, AMOUNT)
    assert (step.event, step.read_again, step.failure_code) == (event, again, code)
    for status in (S.SENT, S.UNCERTAIN):
        machine.apply(status, event)  # legal from both


def test_a_confirmation_for_another_amount_moves_nothing_and_stops_reading() -> None:
    for other in (
        Money(249_999, Currency.UGX),
        Money(250_001, Currency.UGX),
        Money(250_000, Currency.USD),
    ):
        step = after_status(Confirmed("R", other), AMOUNT)
        assert (step.event, step.read_again, step.failure_code) == (None, False, AMOUNT_MISMATCH)


def test_reads_spread_out_and_stop_after_the_window() -> None:
    sent = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
    now, gaps = sent, []
    number = 0
    while (due := next_read(number, sent, now)) is not None:
        gaps.append(due - now)
        now, number = due, number + 1
    assert gaps == sorted(gaps)
    assert gaps[0] == timedelta(seconds=10) and gaps[-1] == timedelta(hours=1)
    assert now - sent <= READ_WINDOW < now - sent + timedelta(hours=1)
    with pytest.raises(ValueError):
        next_read(-1, sent, sent)


def test_a_message_names_the_network_never_the_number() -> None:
    assert destination_label("mtn") == "MTN Mobile Money"
    assert destination_label("airtel") == "Airtel Money"
    assert destination_label("other") == "your mobile money number"


def request(ending: str, payout_id: str = "-Nabc0000000000000P1") -> DisbursementRequest:
    return DisbursementRequest(payout_id, Network.MTN, f"2567712345{ending}", AMOUNT)


async def test_the_sandbox_answers_by_the_last_two_digits() -> None:
    clock = FakeClock(datetime(2026, 11, 13, 9, 0, tzinfo=UTC))
    sandbox = SandboxDisbursements(clock)
    assert await sandbox.send_disbursement(request("01")) == Rejected(
        RejectionCode.ACCOUNT_NOT_FOUND
    )
    assert isinstance(await sandbox.send_disbursement(request("04")), Uncertain)
    assert isinstance(await sandbox.send_disbursement(request("05")), Deferred)

    async def ends(ending: str) -> StatusReading:
        sent = await sandbox.send_disbursement(request(ending))
        assert isinstance(sent, Accepted)
        reference = sent.provider_reference
        early = await sandbox.disbursement_status(
            request_id=request(ending).request_id, provider_reference=reference
        )
        assert early == Pending(reference)
        clock.advance(seconds=SEND_TIME.total_seconds())
        return await sandbox.disbursement_status(
            request_id=request(ending).request_id, provider_reference=reference
        )

    paid = await ends("77")
    assert isinstance(paid, Confirmed) and paid.amount == AMOUNT
    assert isinstance(await ends("02"), Rejected)
    mismatch = await ends("06")
    assert isinstance(mismatch, Confirmed) and mismatch.amount != AMOUNT


async def test_the_sandbox_has_no_record_of_what_it_did_not_hand_out() -> None:
    sandbox = SandboxDisbursements(FakeClock(datetime(2026, 11, 13, 9, 0, tzinfo=UTC)))
    sent = await sandbox.send_disbursement(request("77"))
    assert isinstance(sent, Accepted)
    for request_id, reference in (
        ("-Nabc0000000000000P1", None),
        ("-Nabc0000000000000P2", sent.provider_reference),  # another payout's reference
        ("-Nabc0000000000000P1", "not:a:sandbox:reference"),
        ("-Nabc0000000000000P1", sent.provider_reference.replace("sbxp", "sbx", 1)),
    ):
        assert (
            await sandbox.disbursement_status(request_id=request_id, provider_reference=reference)
            == NotFound()
        )


def test_a_send_deferred_for_our_wallet_says_so_and_stays_sendable() -> None:
    step = after_send(Deferred(reason=DeferReason.WALLET_SHORT))
    assert step.outcome is OUT.DEFERRED
    assert step.deferred_for == "wallet_short"
    assert step.failure_code is None
    assert machine.may_send(machine.apply(S.QUEUED, step.event).status)
    assert after_send(Deferred()).deferred_for is None
