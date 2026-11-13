"""What the journeys found missing on main, pinned so the suite says exactly what is there.

Each owner has been told. When a gap closes, the journey that pins it fails until its entry
here is removed and the journey asserts the new behaviour, so a gap cannot close or reopen
unnoticed.

Steps of the client journey with no code to test yet, and so no journey yet:

- Sending a payout to ioTec: payouts are created at maturity but no worker job disburses them
  (payouts and reconciliation thread).
- Money notifications: payments, investing and payouts never call `notifications.notify`, so
  no payment, interest, maturity or payout message is sent (notifications thread, and each
  module).
- The monthly statement and receipt emails: documents builds and renders them, and no job
  sends them yet (statements thread).
- A Finvest 1.0 client claiming their account (sign-in thread, PR #97).
- Automatic investing asking for each run's investment and payment (automatic investing
  thread; documented as the next step in docs/automatic-investing.md).
"""

# Client and system steps that move or commit money and record no audit event. The staff
# steps around them are audited; these are the client's own commands and the worker's
# confirmations, which the trail cannot show today. None are known now.
UNAUDITED: set[str] = set()

# Movements on the trust bank account that the day close cannot pair with the statement by
# reference, so staff pair them by hand with a reason:
# - a confirmed bank transfer: the ledger movement carries the deposit's ID and is dated the
#   day staff approved it, the statement line carries the bank's reference and the day the
#   money arrived, although the payment review already recorded which line it is;
# - a dealing settlement: the movement carries the order's ID, the bank shows the custodian's
#   reference, which `dealing.holdings.expected_bank_movements` gives but the day close does
#   not read.
HAND_MATCHED = ("bank transfer", "dealing settlement")


def expected_missing(*actions: str) -> list[str]:
    """Of `actions`, the ones known to be missing, in the order given."""
    return [action for action in actions if action in UNAUDITED]
