"""Which message tells a client where their payout has got to. Pure: no I/O.

A withdrawal is told as a withdrawal at each step. Money from a bond (a coupon, a sale or the
repayment at maturity) is the client's earnings, not something they asked to take out, so it is
told by what it is, once it has arrived, and only "being processed" is left out: the bond's own
screen already says it is on its way.
"""

from app.modules.payouts.domain.payouts import PayoutPurpose, PayoutStatus

_WITHDRAWAL = {
    PayoutStatus.SENT: "payout_processing",
    PayoutStatus.UNCERTAIN: "payout_processing",
    PayoutStatus.PAID: "payout_sent",
    PayoutStatus.FAILED: "payout_failed",
}

_BOND_PAID = {
    PayoutPurpose.BOND_COUPON: "bond_coupon_paid",
    PayoutPurpose.BOND_SALE: "bond_sale_paid",
    PayoutPurpose.BOND_REPAYMENT: "bond_repayment_paid",
}


def step_event(purpose: PayoutPurpose, status: PayoutStatus) -> str | None:
    """The message for a payout that reached `status`, or None when there is nothing to say."""
    if purpose is PayoutPurpose.WITHDRAWAL:
        return _WITHDRAWAL.get(status)
    if status is PayoutStatus.PAID:
        return _BOND_PAID[purpose]
    if status is PayoutStatus.FAILED:
        return "bond_payment_failed"
    return None


def declined_event(purpose: PayoutPurpose) -> str:
    """Staff declined the payout after review."""
    return "payout_declined" if purpose is PayoutPurpose.WITHDRAWAL else "bond_payment_declined"


def returned_event(purpose: PayoutPurpose) -> str:
    """A paid payout came back from the bank or network."""
    return "payout_returned" if purpose is PayoutPurpose.WITHDRAWAL else "bond_payment_returned"
