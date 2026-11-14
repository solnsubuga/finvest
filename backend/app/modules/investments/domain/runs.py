"""Which days staff may ask the interest run or the maturity run for again. Pure: no I/O.

Both runs are safe to repeat. The interest run posts "earned so far minus posted so far", so a
day run twice pays nothing the second time, and the maturity run only closes terms that are
still open. What is limited is the day:

* **Interest.** Any day from today back to `INTEREST_DAYS_BACK` days ago, the same window the
  scheduler catches up on by itself. A day that has not come is never run.
* **Maturity.** Today only. One run closes every term due by its day, so today's run covers
  every day before it, and a run for an earlier day would close less, never more.
"""

from datetime import date, timedelta
from enum import StrEnum

INTEREST_DAYS_BACK = 31


class RunKind(StrEnum):
    INTEREST = "interest"
    MATURITY = "maturity"


def run_date_problem(kind: RunKind, run_date: date, today: date) -> str | None:
    """Why a run cannot be asked for on `run_date`, as a code, or None if it can."""
    if run_date > today:
        return "not_yet"
    if kind is RunKind.MATURITY:
        return None if run_date == today else "maturity_today_only"
    if run_date < today - timedelta(days=INTEREST_DAYS_BACK):
        return "too_old"
    return None
