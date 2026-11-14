"""The Overview's figures from what the owning modules read. Pure.

Every amount is an integer in the smallest unit; nothing here rounds.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from app.shared.money import Currency, Money

# Client liability codes that are money on its way out: handed to payouts and not yet left.
IN_TRANSIT_CODES = ("client.payout_pending", "client.payout_in_flight")
AWAITING_START_CODE = "client.awaiting_start"
# Finvest's own accounts that hold the cash.
CASH_CODES = ("bank.trust", "iotec.wallet")
DEPOSIT_SOURCE = "payment.deposit"
PAYOUT_SOURCE = "payout"
# Money in and out is shown for this many days, today included.
FLOW_DAYS = 7
# Cash needed for maturities is shown for this many weeks, this week first.
LADDER_WEEKS = 4
# Below this, a network's float on the ioTec wallet is low: payouts on it may start to wait.
# Decided for now from the design ("below the 10m warning line"); change it here.
FLOAT_WARNING_LINE = Money(10_000_000, Currency.UGX)
FLOAT_NETWORKS = ("mtn", "airtel")


@dataclass(frozen=True, slots=True)
class DayFlow:
    day: date
    money_in: Money
    money_out: Money


@dataclass(frozen=True, slots=True)
class SourceDay:
    """What entries of one source did to the cash accounts on one day."""

    day: date
    source_type: str
    debits: Money
    credits: Money


def flows(
    found: Iterable[SourceDay], currency: Currency, *, today: date, days: int = FLOW_DAYS
) -> list[DayFlow]:
    """Money in and out of the cash accounts per day, every day listed, oldest first.

    In is cash a client deposit brought in. Out is cash a payout took, less any payout that came
    back. A pot transfer between Finvest's own accounts is neither, so it is not read at all.
    """
    zero = Money.zero(currency)
    start = today - timedelta(days=days - 1)
    money_in = dict.fromkeys((start + timedelta(days=n) for n in range(days)), zero)
    money_out = dict(money_in)
    for row in found:
        if row.day not in money_in:
            continue
        if row.source_type == DEPOSIT_SOURCE:
            money_in[row.day] = money_in[row.day] + row.debits - row.credits
        elif row.source_type == PAYOUT_SOURCE:
            money_out[row.day] = money_out[row.day] + row.credits - row.debits
    return [DayFlow(day, money_in[day], money_out[day]) for day in money_in]


@dataclass(frozen=True, slots=True)
class MaturingDay:
    day: date
    investments: int
    principal: Money
    pay_out_principal: Money


@dataclass(frozen=True, slots=True)
class LadderWeek:
    """Monday to Sunday. The first week starts today, so it is shorter."""

    first_day: date
    last_day: date
    investments: int
    principal: Money
    pay_out_principal: Money


def ladder_window(today: date, weeks: int = LADDER_WEEKS) -> tuple[date, date]:
    """From today to the Sunday that ends the last week shown."""
    this_sunday = today + timedelta(days=6 - today.weekday())
    return today, this_sunday + timedelta(weeks=weeks - 1)


def ladder(
    found: Iterable[MaturingDay], currency: Currency, *, today: date, weeks: int = LADDER_WEEKS
) -> list[LadderWeek]:
    """What matures each week, every week listed even at zero."""
    starts = [today] + [
        today + timedelta(days=7 - today.weekday() + 7 * n) for n in range(weeks - 1)
    ]
    ends = [start + timedelta(days=6 - start.weekday()) for start in starts]
    zero = Money.zero(currency)
    counts = [0] * weeks
    principals = [zero] * weeks
    pay_outs = [zero] * weeks
    for day in found:
        index = next((n for n in range(weeks) if starts[n] <= day.day <= ends[n]), None)
        if index is None:
            continue
        counts[index] += day.investments
        principals[index] = principals[index] + day.principal
        pay_outs[index] = pay_outs[index] + day.pay_out_principal
    return [
        LadderWeek(starts[n], ends[n], counts[n], principals[n], pay_outs[n]) for n in range(weeks)
    ]


@dataclass(frozen=True, slots=True)
class Float:
    """One network's share of Finvest's ioTec wallet, by the ledger."""

    network: str
    balance: Money
    warning_line: Money

    @property
    def low(self) -> bool:
        return self.balance < self.warning_line


def floats(by_network: Mapping[str | None, Money]) -> list[Float]:
    """Each mobile money network's float, every network listed even with no account yet."""
    zero = Money.zero(FLOAT_WARNING_LINE.currency)
    return [
        Float(network, by_network.get(network, zero), FLOAT_WARNING_LINE)
        for network in FLOAT_NETWORKS
    ]


@dataclass(frozen=True, slots=True)
class Balancing:
    """How far the daily balancing of the books with the providers has got."""

    last_closed: date | None
    # A day prepared by one person and waiting for a second.
    waiting_for_approval: date | None
    # Days that have ended and are not closed yet. Yesterday is the first day that can close.
    days_behind: int


def balancing(last_closed: date | None, waiting: date | None, *, today: date) -> Balancing:
    yesterday = today - timedelta(days=1)
    behind = 0 if last_closed is None else max(0, (yesterday - last_closed).days)
    return Balancing(last_closed, waiting, behind)


AttentionCode = Literal[
    "float_low",
    "payouts_waiting_for_float",
    "review_queue",
    "approvals_for_you",
    "books_not_balanced",
    "day_close_to_approve",
    "interest_run_behind",
    "interest_run_missing",
    "maturities_not_closed",
]
Tone = Literal["warning", "info"]


@dataclass(frozen=True, slots=True)
class Attention:
    code: AttentionCode
    tone: Tone
    count: int
    # The longest-waiting item's time, when there is one.
    since: datetime | None = None
    # A network for `float_low`, a date for books and runs, as a short label. Never personal.
    subject: str | None = None


_ORDERED: tuple[AttentionCode, ...] = (
    "float_low",
    "payouts_waiting_for_float",
    "interest_run_missing",
    "interest_run_behind",
    "maturities_not_closed",
    "books_not_balanced",
    "review_queue",
    "approvals_for_you",
    "day_close_to_approve",
)
_ORDER = {code: n for n, code in enumerate(_ORDERED)}


def attention(found: Iterable[Attention]) -> list[Attention]:
    """Warnings first, then the rest, each in a fixed order. Nothing at zero is shown."""
    return sorted(
        (item for item in found if item.count > 0),
        key=lambda item: (item.tone != "warning", _ORDER[item.code], item.subject or ""),
    )
