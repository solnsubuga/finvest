"""What a holding is paid when its bond matures. Pure: no I/O.

On the maturity date Bank of Uganda repays the face value with the last coupon. The client is
paid the face value and that coupon after tax, as for every coupon before it.

The client's bond holding account holds what they paid: the clean amount and the accrued
interest. Taking it out at maturity, the two parts go back separately, and neither is interest
paid to the client, so neither touches `interest.expense`, which the tax module reads:

* the accrued interest the client paid at purchase was interest to the last coupon date, and
  the first coupon paid it back to them in full. Finvest takes it in as income.
* the clean amount is usually more or less than the face value, because the bond was bought
  above or below par. The face value less the clean amount is Finvest's expense for a bond
  bought below par, and the clean amount less the face value its income for one above.

Every ledger balance stays at or above zero, so each of these has an account of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.modules.bonds.domain.coupons import Coupon, coupon
from app.modules.bonds.domain.issues import BondIssue
from app.shared.money import Money


@dataclass(frozen=True, slots=True)
class Redemption:
    face: Money
    # What the client paid for the holding, now taken out of their bond holding: the clean
    # amount and the accrued interest.
    cost: Money
    accrued: Money
    # The last coupon, before and after tax.
    coupon: Coupon

    @property
    def clean(self) -> Money:
        return self.cost - self.accrued

    @property
    def below_par(self) -> Money:
        """The face value less the clean amount, for a bond bought below par, else zero."""
        difference = self.face - self.clean
        return difference if difference.is_positive else Money.zero(self.face.currency)

    @property
    def above_par(self) -> Money:
        """The clean amount less the face value, for a bond bought above par, else zero."""
        difference = self.clean - self.face
        return difference if difference.is_positive else Money.zero(self.face.currency)

    @property
    def paid(self) -> Money:
        """What the client is paid: the face value and the last coupon after tax."""
        return self.face + self.coupon.net


def redemption(issue: BondIssue, face: Money, cost: Money, accrued: Money) -> Redemption:
    return Redemption(face=face, cost=cost, accrued=accrued, coupon=coupon(issue, face))


def has_matured(issue: BondIssue, run_date: date) -> bool:
    """Whether a run on `run_date` repays the issue: on its maturity date or later, so a run
    that never happened is made up by the next one."""
    return issue.maturity_date <= run_date
