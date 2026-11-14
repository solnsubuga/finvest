"""Transaction monitoring rules. Pure: no I/O, and integers only.

Each rule looks at one new transaction against the client's earlier ones and may raise an
alert. An alert opens a compliance case that staff close with two people: no concern, or a
suspicious transaction report owed to the Financial Intelligence Authority.

The thresholds are decided for now (docs/decisions.md) and are in Uganda shillings. Dollar
transactions are recorded but no rule reads them yet, because the dollar product is paused.
Every alert has a dedupe key, so a repeated job or a burst of payments raises it once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.shared.business_day import business_date
from app.shared.money import Currency


class Kind(StrEnum):
    DEPOSIT = "deposit"
    PAYOUT = "payout"
    # A client-to-client transfer of an investment, recorded once for each side: sent by the
    # sender, counted as money going out, and received by the recipient, counted as money in.
    TRANSFER_SENT = "transfer_sent"
    TRANSFER_RECEIVED = "transfer_received"


# What the deposit rules count as money in, and what the quick withdrawal rule counts as money
# going out. A received transfer is money in, so a large one, or several that add up, are seen
# as a deposit would be.
MONEY_IN = frozenset({Kind.DEPOSIT, Kind.TRANSFER_RECEIVED})
MONEY_OUT = frozenset({Kind.PAYOUT, Kind.TRANSFER_SENT})
TRANSFERS = frozenset({Kind.TRANSFER_SENT, Kind.TRANSFER_RECEIVED})


class Rule(StrEnum):
    # One deposit at or above the large transaction amount.
    LARGE_DEPOSIT = "large_deposit"
    # Several deposits that each stay under the large amount but together reach it within a
    # week: a sign of splitting a payment to stay under a reporting threshold.
    SPLIT_DEPOSITS = "split_deposits"
    # Many deposits in one day.
    MANY_DEPOSITS = "many_deposits"
    # A deposit far larger than the client's usual ones.
    UNUSUAL_DEPOSIT = "unusual_deposit"
    # Most of what came in during the last week asked to go straight back out.
    QUICK_WITHDRAWAL = "quick_withdrawal"


# Decided for now. The large amount follows the Financial Intelligence Authority's large cash
# transaction threshold (to confirm with counsel).
LARGE_AMOUNT = 20_000_000
SPLIT_WINDOW = timedelta(days=7)
MANY_WINDOW = timedelta(hours=24)
MANY_COUNT = 5
UNUSUAL_LOOKBACK = timedelta(days=180)
UNUSUAL_MULTIPLE = 5
UNUSUAL_FLOOR = 5_000_000
UNUSUAL_HISTORY = 3  # earlier deposits needed before "usual" means anything
QUICK_WINDOW = timedelta(days=7)
QUICK_FLOOR = 5_000_000
# A payout of at least half of what came in during the window. As a fraction: 1 / 2.
QUICK_SHARE = (1, 2)


@dataclass(frozen=True, slots=True)
class Transaction:
    id: str
    kind: Kind
    amount_minor: int
    currency: Currency
    occurred_at: datetime

    def __post_init__(self) -> None:
        if type(self.amount_minor) is not int or self.amount_minor <= 0:
            raise ValueError("An amount is a positive whole number of the smallest unit")


@dataclass(frozen=True, slots=True)
class Alert:
    rule: Rule
    # What makes it one alert: the same key raises it once.
    dedupe_key: str
    # The transactions and figures the rule saw, as IDs and whole numbers, for staff.
    detail: dict[str, object] = field(default_factory=dict)


def evaluate(client_id: str, new: Transaction, earlier: Sequence[Transaction]) -> tuple[Alert, ...]:
    """Every alert the new transaction raises. `earlier` is the client's transactions before it,
    in any order; anything in it at or after the new one is ignored."""
    if new.currency is not Currency.UGX:
        return ()
    before = [
        t
        for t in earlier
        if t.currency is Currency.UGX and t.occurred_at <= new.occurred_at and t.id != new.id
    ]
    deposits = [t for t in before if t.kind in MONEY_IN]
    if new.kind in MONEY_IN:
        alerts = (
            _large(new),
            _split(client_id, new, deposits),
            _many(client_id, new, deposits),
            _unusual(new, deposits),
        )
        return tuple(_naming_transfers(alert, [new, *deposits]) for alert in alerts if alert)
    quick = _quick(new, deposits)
    return () if quick is None else (_naming_transfers(quick, [new, *deposits]),)


def _naming_transfers(alert: Alert, seen: Sequence[Transaction]) -> Alert:
    """The alert, with the IDs among those it names that are transfers rather than deposits or
    payouts listed under `transfer_ids`, so staff know where to look each one up."""
    named = set(_ids(alert.detail))
    transfers = [t.id for t in _oldest_first(list(seen)) if t.kind in TRANSFERS and t.id in named]
    if not transfers:
        return alert
    return Alert(alert.rule, alert.dedupe_key, {**alert.detail, "transfer_ids": transfers})


def _ids(detail: dict[str, object]) -> list[str]:
    ids = [
        value for key in ("deposit_id", "payout_id") if isinstance(value := detail.get(key), str)
    ]
    many = detail.get("deposit_ids")
    if isinstance(many, list):
        ids.extend(value for value in many if isinstance(value, str))
    return ids


def _large(new: Transaction) -> Alert | None:
    if new.amount_minor < LARGE_AMOUNT:
        return None
    return Alert(
        Rule.LARGE_DEPOSIT,
        new.id,
        {"deposit_id": new.id, "amount_minor": new.amount_minor, "threshold": LARGE_AMOUNT},
    )


def _split(client_id: str, new: Transaction, deposits: list[Transaction]) -> Alert | None:
    if new.amount_minor >= LARGE_AMOUNT:
        return None
    window = [
        t
        for t in deposits
        if new.occurred_at - t.occurred_at < SPLIT_WINDOW and t.amount_minor < LARGE_AMOUNT
    ]
    total = new.amount_minor + sum(t.amount_minor for t in window)
    # Only the deposit that takes the week over the amount raises it, once a week per client.
    if not window or total < LARGE_AMOUNT or total - new.amount_minor >= LARGE_AMOUNT:
        return None
    week = business_date(new.occurred_at).isocalendar()
    return Alert(
        Rule.SPLIT_DEPOSITS,
        f"{client_id}:{week.year}-W{week.week:02d}",
        {
            "deposit_ids": [t.id for t in _oldest_first(window)] + [new.id],
            "total_minor": total,
            "threshold": LARGE_AMOUNT,
            "days": SPLIT_WINDOW.days,
        },
    )


def _many(client_id: str, new: Transaction, deposits: list[Transaction]) -> Alert | None:
    window = [t for t in deposits if new.occurred_at - t.occurred_at < MANY_WINDOW]
    if len(window) + 1 != MANY_COUNT:
        # Raised by the deposit that reaches the count, not by every one after it.
        return None
    return Alert(
        Rule.MANY_DEPOSITS,
        f"{client_id}:{business_date(new.occurred_at).isoformat()}",
        {
            "deposit_ids": [t.id for t in _oldest_first(window)] + [new.id],
            "count": MANY_COUNT,
            "hours": int(MANY_WINDOW.total_seconds() // 3600),
        },
    )


def _unusual(new: Transaction, deposits: list[Transaction]) -> Alert | None:
    history = [t for t in deposits if new.occurred_at - t.occurred_at < UNUSUAL_LOOKBACK]
    if new.amount_minor < UNUSUAL_FLOOR or len(history) < UNUSUAL_HISTORY:
        return None
    usual = median_minor([t.amount_minor for t in history])
    if new.amount_minor < usual * UNUSUAL_MULTIPLE:
        return None
    return Alert(
        Rule.UNUSUAL_DEPOSIT,
        new.id,
        {
            "deposit_id": new.id,
            "amount_minor": new.amount_minor,
            "usual_minor": usual,
            "multiple": UNUSUAL_MULTIPLE,
            "earlier_deposits": len(history),
        },
    )


def _quick(new: Transaction, deposits: list[Transaction]) -> Alert | None:
    window = [t for t in deposits if new.occurred_at - t.occurred_at < QUICK_WINDOW]
    came_in = sum(t.amount_minor for t in window)
    share_top, share_bottom = QUICK_SHARE
    if came_in < QUICK_FLOOR or new.amount_minor * share_bottom < came_in * share_top:
        return None
    return Alert(
        Rule.QUICK_WITHDRAWAL,
        new.id,
        {
            "payout_id": new.id,
            "amount_minor": new.amount_minor,
            "deposits_minor": came_in,
            "deposit_ids": [t.id for t in _oldest_first(window)],
            "days": QUICK_WINDOW.days,
        },
    )


def median_minor(amounts: Sequence[int]) -> int:
    """The middle amount, the lower of the two middles for an even count. Never a fraction."""
    if not amounts:
        raise ValueError("No amounts")
    ordered = sorted(amounts)
    return ordered[(len(ordered) - 1) // 2]


def _oldest_first(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(transactions, key=lambda t: (t.occurred_at, t.id))


# What the console shows of the rules and of a case. ---------------------------------------

# The rules read shilling transactions only.
RULE_CURRENCY = Currency.UGX


def case_amount_minor(detail: dict[str, object]) -> int | None:
    """The amount a monitoring case is about: the deposit or payout that raised it, or for split
    deposits their total. None when the detail names no amount, as for a screening case."""
    for key in ("amount_minor", "total_minor"):
        value = detail.get(key)
        if type(value) is int:
            return value
    return None
