"""What the journeys found missing on main, pinned so the suite says exactly what is there.

Each owner has been told. When a gap closes, the journey that pins it fails until its entry
here is removed and the journey asserts the new behaviour, so a gap cannot close or reopen
unnoticed.

Steps of the client journey with no code to test yet, and so no journey yet:

- Money notifications: payments, investing and payouts never call `notifications.notify`, so
  no payment, interest, maturity or payout message is sent (notifications thread, and each
  module).
- The monthly statement and receipt emails: documents builds and renders them, and no job
  sends them yet (statements thread).
- Automatic investing asking for each run's investment and payment (automatic investing
  thread; documented as the next step in docs/automatic-investing.md).
"""

# Client and system steps that move or commit money and record no audit event. The staff
# steps around them are audited; these are the client's own commands and the worker's
# confirmations, which the trail cannot show today. None are known now.
UNAUDITED: set[str] = set()

# Movements on the trust bank account that the day close cannot pair with the statement by
# itself, so staff pair them by hand with a reason. None now: a confirmed bank transfer pairs
# with the line its approved review names, and a dealing settlement with the bank reference
# `dealing.holdings.expected_bank_movements` gives.
HAND_MATCHED: tuple[str, ...] = ()


def expected_missing(*actions: str) -> list[str]:
    """Of `actions`, the ones known to be missing, in the order given."""
    return [action for action in actions if action in UNAUDITED]
