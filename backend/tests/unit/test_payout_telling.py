"""Which message tells a client about each payout, for every purpose and status."""

import pytest

from app.modules.notifications.domain.events import EVENTS
from app.modules.payouts.domain.payouts import PayoutPurpose, PayoutStatus
from app.modules.payouts.domain.telling import declined_event, returned_event, step_event

BOND = (PayoutPurpose.BOND_COUPON, PayoutPurpose.BOND_SALE, PayoutPurpose.BOND_REPAYMENT)


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (PayoutStatus.SCREENING, None),
        (PayoutStatus.HELD, None),
        (PayoutStatus.QUEUED, None),
        (PayoutStatus.SENT, "payout_processing"),
        (PayoutStatus.UNCERTAIN, "payout_processing"),
        (PayoutStatus.PAID, "payout_sent"),
        (PayoutStatus.FAILED, "payout_failed"),
        (PayoutStatus.CANCELLED, None),
    ],
)
def test_a_withdrawal_is_told_as_a_withdrawal(status: PayoutStatus, event: str | None) -> None:
    assert step_event(PayoutPurpose.WITHDRAWAL, status) == event


@pytest.mark.parametrize("purpose", BOND)
@pytest.mark.parametrize("status", list(PayoutStatus))
def test_bond_money_is_never_called_a_withdrawal(
    purpose: PayoutPurpose, status: PayoutStatus
) -> None:
    paid = {
        PayoutPurpose.BOND_COUPON: "bond_coupon_paid",
        PayoutPurpose.BOND_SALE: "bond_sale_paid",
        PayoutPurpose.BOND_REPAYMENT: "bond_repayment_paid",
    }[purpose]
    expected = {PayoutStatus.PAID: paid, PayoutStatus.FAILED: "bond_payment_failed"}.get(status)
    assert step_event(purpose, status) == expected
    assert declined_event(purpose) == "bond_payment_declined"
    assert returned_event(purpose) == "bond_payment_returned"


def test_a_withdrawal_declined_or_returned_keeps_its_words() -> None:
    assert declined_event(PayoutPurpose.WITHDRAWAL) == "payout_declined"
    assert returned_event(PayoutPurpose.WITHDRAWAL) == "payout_returned"


def test_every_event_named_is_in_the_catalogue() -> None:
    named = {
        step_event(purpose, status) for purpose in PayoutPurpose for status in PayoutStatus
    } | {f(purpose) for purpose in PayoutPurpose for f in (declined_event, returned_event)}
    assert named - {None} <= set(EVENTS)
