"""What must be true before a client's account can close.

An account closes only when Finvest owes the client nothing and nothing is on its way: no
investment waiting for payment or running, no deposit or payout that has not ended, no open
goal and no automatic investing plan that could ask for a payment. Each is a blocker the client
clears first, and every one is checked again by each member of staff and at the moment of
closing.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.shared.money import Money


class Blocker(StrEnum):
    INVESTMENTS_RUNNING = "investments_running"
    MONEY_OWED = "money_owed"
    DEPOSITS_IN_PROGRESS = "deposits_in_progress"
    PAYOUTS_IN_PROGRESS = "payouts_in_progress"
    GOALS_OPEN = "goals_open"
    PLANS_LIVE = "plans_live"


@dataclass(frozen=True, slots=True)
class Standing:
    """The client's position as the owning modules report it."""

    running_investments: int
    # What Finvest owes the client, per currency, from the ledger. Only non-zero amounts.
    owed: tuple[Money, ...]
    open_deposits: int
    open_payouts: int
    open_goals: int
    live_plans: int


def blockers(standing: Standing) -> list[Blocker]:
    """Every reason the account cannot close yet, in the order the client should clear them."""
    found: list[Blocker] = []
    if standing.running_investments:
        found.append(Blocker.INVESTMENTS_RUNNING)
    if any(amount.minor for amount in standing.owed):
        found.append(Blocker.MONEY_OWED)
    if standing.open_deposits:
        found.append(Blocker.DEPOSITS_IN_PROGRESS)
    if standing.open_payouts:
        found.append(Blocker.PAYOUTS_IN_PROGRESS)
    if standing.open_goals:
        found.append(Blocker.GOALS_OPEN)
    if standing.live_plans:
        found.append(Blocker.PLANS_LIVE)
    return found
