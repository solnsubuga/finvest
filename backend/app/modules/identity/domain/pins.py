"""The app PIN: five digits that open the Finvest app on one phone.

A PIN belongs to one session of the phone app, never to the account, so it works only on the
phone where it was made and goes when that session ends. Forgetting it means logging in again
with the password and a second step, then choosing a new one.
"""

from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal

PIN_LENGTH = 5
# Wrong PINs in a row before the session is ended and the client must log in again.
MAX_WRONG_PINS = 5
# A session with a PIN that has not been used for this long needs the PIN again.
LOCK_AFTER = timedelta(minutes=5)

# A right PIN given to confirm an action is good for this long, once, from the same session.
CONFIRMATION_LASTS = timedelta(minutes=3)

PinProblem = Literal["format", "too_simple"]
# The actions the phone app confirms with the PIN, on top of their own checks.
PinAction = Literal["withdrawal", "payout_account"]


def pin_problem(pin: str) -> PinProblem | None:
    """Why a new PIN is refused, or None. Every digit the same, or a straight run, is too simple."""
    if len(pin) != PIN_LENGTH or not pin.isascii() or not pin.isdigit():
        return "format"
    digits = [int(character) for character in pin]
    steps = {later - earlier for earlier, later in pairwise(digits)}
    if steps in ({0}, {1}, {-1}):
        return "too_simple"
    return None


def is_locked(*, pin_set: bool, last_seen_at: datetime, now: datetime) -> bool:
    """A session with a PIN locks once it has been unused for `LOCK_AFTER`."""
    return pin_set and now - last_seen_at >= LOCK_AFTER
