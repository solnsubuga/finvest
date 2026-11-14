"""What other modules may call in screening. Everything else in this module is its own.

* `request_screening` asks for a screen in the caller's transaction. The identity check calls
  it when a client hands in their check (`Reason.ONBOARDING`), and payouts before money leaves.
* `payout_clearance` says whether money may leave for a client now. Payouts asks it when a
  payout is created, and waits for a screen when it is not clear.
* `watch_payout` asks, in the caller's transaction, for a new payout to be read by transaction
  monitoring. Payouts calls it when a payout is created.
* `latest_outcome` says where a client stands: anything but `clear` is a reason for a person to
  look before approving an identity check.
"""

from app.modules.screening.domain.standing import Clearance, Outcome, Reason
from app.modules.screening.monitoring import watch_payout
from app.modules.screening.service import (
    ScreeningStanding,
    latest_outcome,
    payout_clearance,
    request_screening,
)

__all__ = [
    "Clearance",
    "Outcome",
    "Reason",
    "ScreeningStanding",
    "latest_outcome",
    "payout_clearance",
    "request_screening",
    "watch_payout",
]
