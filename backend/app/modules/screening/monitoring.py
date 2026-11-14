"""Transaction monitoring: record each deposit and payout, run the rules, open cases.

The worker records a confirmed deposit (from `payments.deposit_confirmed`) and a payout asked
for (from a job payouts enqueues), in screening's own table, then runs the rules in
`domain/monitoring.py` against the client's earlier ones. Each alert opens a `monitoring`
compliance case once, by its rule and dedupe key. A monitoring case does not hold payouts: it
is for staff to look into, and close as no concern or as a report owed to the Financial
Intelligence Authority.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.screening import events, service
from app.modules.screening.domain.cases import CaseSource
from app.modules.screening.domain.monitoring import (
    QUICK_WINDOW,
    SPLIT_WINDOW,
    UNUSUAL_LOOKBACK,
    Kind,
    Transaction,
    evaluate,
)
from app.shared.clock import Clock
from app.shared.money import Currency
from app.shared.push_ids import push_id

MONITOR_DEPOSIT_KIND = events.MONITOR_DEPOSIT_KIND
MONITOR_PAYOUT_KIND = "screening.monitor_payout"
# How far back any rule looks.
LOOKBACK = max(SPLIT_WINDOW, UNUSUAL_LOOKBACK, QUICK_WINDOW)

_INSERT = text(
    "INSERT INTO monitoring_transactions (id, client_id, kind, source_id, currency, "
    "amount_minor, occurred_at, recorded_at) VALUES (:id, :client_id, :kind, :source_id, "
    ":currency, :amount_minor, :occurred_at, :now) "
    "ON CONFLICT (kind, source_id) DO NOTHING RETURNING id"
)
_EARLIER = text(
    "SELECT source_id, kind, currency, amount_minor, occurred_at FROM monitoring_transactions "
    "WHERE client_id = :client_id AND occurred_at > :since AND occurred_at <= :until "
    "AND NOT (kind = :kind AND source_id = :source_id)"
)


async def watch_payout(session: AsyncSession, payout_id: str, now: datetime) -> None:
    """Ask, in the caller's transaction, for a payout to be monitored. Payouts calls it when a
    payout is created. The job carries only the payout's ID."""
    await enqueue(
        session,
        JobRequest(
            kind=MONITOR_PAYOUT_KIND, idempotency_key=payout_id, payload={"payout_id": payout_id}
        ),
        now=now,
    )


async def record(
    session: AsyncSession,
    *,
    client_id: str,
    transaction: Transaction,
    clock: Clock,
) -> list[str] | None:
    """Record one transaction and open a case for each alert it raises, in the worker's
    transaction. The cases opened, or None if this transaction was already recorded.

    One client's transactions are recorded one at a time, so two deposits confirmed together
    each see the other and a split or a burst is not missed between them.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"monitoring:{client_id}"},
    )
    inserted = await session.scalar(
        _INSERT,
        {
            "id": push_id(clock),
            "client_id": client_id,
            "kind": transaction.kind.value,
            "source_id": transaction.id,
            "currency": transaction.currency.value,
            "amount_minor": transaction.amount_minor,
            "occurred_at": transaction.occurred_at,
            "now": clock.now(),
        },
    )
    if inserted is None:
        return None
    rows = await session.execute(
        _EARLIER,
        {
            "client_id": client_id,
            "since": transaction.occurred_at - LOOKBACK,
            "until": transaction.occurred_at,
            "kind": transaction.kind.value,
            "source_id": transaction.id,
        },
    )
    earlier = [
        Transaction(
            row.source_id,
            Kind(row.kind),
            int(row.amount_minor),
            Currency(row.currency),
            row.occurred_at,
        )
        for row in rows
    ]
    opened: list[str] = []
    for alert in evaluate(client_id, transaction, earlier):
        case_id = await service.open_case(
            session,
            client_id=client_id,
            source=CaseSource.MONITORING,
            code=alert.rule.value,
            clock=clock,
            dedupe_key=alert.dedupe_key,
            detail=alert.detail,
        )
        if case_id is not None:
            opened.append(case_id)
    return opened
