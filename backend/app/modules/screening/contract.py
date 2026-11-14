"""What other modules may call in screening. Everything else in this module is its own.

* `request_screening` asks for a screen in the caller's transaction. The identity check calls
  it when a client hands in their check (`Reason.ONBOARDING`), and payouts before money leaves.
* `latest_outcome` says where a client stands: anything but `clear` is a reason for a person to
  look before approving an identity check.
"""

from app.modules.screening.domain.standing import Outcome, Reason
from app.modules.screening.service import ScreeningStanding, latest_outcome, request_screening

__all__ = ["Outcome", "Reason", "ScreeningStanding", "latest_outcome", "request_screening"]
