"""Checks a journey runs after every scenario: the books balance and the audit trail is whole.

They read the whole ledger as the table owner, so nothing a row policy hides can slip past.
The database already refuses an unbalanced entry; these checks prove it from the outside, on
what a scenario actually left behind, so a bug in a trigger or a migration shows up here too.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.shared.actors import SYSTEM


@dataclass(frozen=True)
class LedgerProblem:
    kind: str
    detail: str


async def ledger_problems(migrator: Database, worker: Database) -> list[LedgerProblem]:
    """Everything wrong with the ledger as a whole. An empty list means it balances."""
    problems: list[LedgerProblem] = []
    async with migrator.transaction() as session:
        # Debits equal credits in every entry.
        for row in await session.execute(
            text(
                "SELECT entry_id, sum(amount_minor) AS total, count(*) AS lines "
                "FROM ledger_lines GROUP BY entry_id "
                "HAVING sum(amount_minor) <> 0 OR count(*) < 2"
            )
        ):
            problems.append(
                LedgerProblem("unbalanced_entry", f"{row.entry_id}: {row.total} / {row.lines}")
            )
        # Every entry has lines, and every line is in its entry's currency.
        for row in await session.execute(
            text(
                "SELECT entry.id FROM ledger_journal_entries AS entry "
                "WHERE NOT EXISTS "
                "(SELECT FROM ledger_lines AS line WHERE line.entry_id = entry.id) "
                "OR EXISTS (SELECT FROM ledger_lines AS line WHERE line.entry_id = entry.id "
                "AND line.currency <> entry.currency)"
            )
        ):
            problems.append(LedgerProblem("entry_lines", row.id))
        # Debits equal credits across the whole ledger, in each currency.
        for row in await session.execute(
            text(
                "SELECT currency, sum(amount_minor) AS total FROM ledger_lines "
                "GROUP BY currency HAVING sum(amount_minor) <> 0"
            )
        ):
            problems.append(LedgerProblem("trial_balance", f"{row.currency}: {row.total}"))
        # No account is below zero in its normal direction.
        for row in await session.execute(
            text(
                "SELECT account.code, account.reference, balance.balance_minor "
                "FROM ledger_balances AS balance JOIN ledger_accounts AS account "
                "ON account.id = balance.account_id WHERE balance.balance_minor < 0"
            )
        ):
            problems.append(
                LedgerProblem(
                    "negative_balance", f"{row.code}/{row.reference}: {row.balance_minor}"
                )
            )
    # Each account's kept balance is the sum of its lines.
    async with worker.actor_transaction(SYSTEM) as session:
        for drift in await ledger.find_balance_drift(session):
            problems.append(
                LedgerProblem(
                    "balance_drift",
                    f"{drift.account_id}: kept {drift.cached_minor}, lines {drift.computed_minor}",
                )
            )
    return problems


@dataclass(frozen=True)
class AuditRow:
    action: str
    outcome: str
    actor_kind: str
    actor_id: str | None
    resource_type: str
    resource_id: str | None


async def audit_since(migrator: Database, since: datetime) -> list[AuditRow]:
    async with migrator.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT action, outcome, actor_kind, actor_id, resource_type, resource_id "
                "FROM audit_events WHERE occurred_at >= :since ORDER BY occurred_at, id"
            ),
            {"since": since},
        )
        return [AuditRow(*row) for row in rows]


async def audit_for(migrator: Database, ids: Iterable[str]) -> list[AuditRow]:
    """Events about any of `ids`, or done by any of them, oldest first."""
    async with migrator.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT action, outcome, actor_kind, actor_id, resource_type, resource_id "
                "FROM audit_events WHERE resource_id = ANY(:ids) OR actor_id = ANY(:ids) "
                "ORDER BY occurred_at, id"
            ),
            {"ids": list(ids)},
        )
        return [AuditRow(*row) for row in rows]


async def staff_entries_without_audit(migrator: Database, since: datetime) -> list[str]:
    """Ledger entries of a kind staff can post, since `since`, whose source has no audit event.

    Every such entry must say who caused it. An entry is tied to its audit event by its
    source: the event's resource is the order, payout or correction the entry came from. The
    ledger does not record who posted an entry, so a kind the worker posts too is held to the
    same rule.
    """
    async with migrator.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT entry.id, entry.source_type, entry.source_id "
                "FROM ledger_journal_entries AS entry "
                "WHERE entry.posted_at >= :since AND entry.source_type IN "
                "(SELECT source_type FROM ledger_staff_source_types) "
                "AND entry.source_type <> 'test.fixture' AND NOT EXISTS ("
                "  SELECT FROM audit_events AS event "
                "  WHERE event.resource_id IN (entry.source_id, entry.id))"
            ),
            {"since": since},
        )
        return [f"{row.source_type}:{row.source_id} ({row.id})" for row in rows]


def missing(trail: list[AuditRow], *actions: str) -> list[str]:
    """The `actions` the trail lacks. A journey asserts this is empty."""
    seen = {row.action for row in trail}
    return [action for action in actions if action not in seen]
