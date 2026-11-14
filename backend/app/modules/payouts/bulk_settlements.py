"""Settling many bank payouts of one batch from a single bulk debit on the trust statement.

When a batch's transfer file is uploaded, the bank often takes the money in one debit for the
whole file rather than one line per payout. Two people settle those payouts together:

1. One member of staff proposes a bulk settlement: one trust statement line going out, in the
   batch's currency, on or after the day the batch was approved, and the sent payouts of that
   batch it paid. Their amounts must add up to the line exactly. A paid settlement is written
   for each payout in the same transaction, naming the bulk.
2. A different one approves it. In the same transaction every payout in it becomes paid,
   settled from the trust account, and each client is told. They may instead decline it, or the
   proposer may withdraw it; either way the payouts stay sent, to be settled again.

A payout the bank did not pay is left out of the bulk and settled on its own, as failed, with
`bank_payouts.propose_settlement`. Proposing and approving take an idempotency key. Migration
0280 holds each rule in the database.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Row, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.payouts import bank_payouts, payouts
from app.modules.payouts.bank_payouts import BankPayoutRefused, BatchStatus, BatchView, Decision
from app.modules.payouts.domain.payouts import IllegalTransition, PayoutEvent
from app.modules.payouts.jobs import tell_client
from app.modules.payouts.payouts import PayoutView
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import Direction, StatementSource
from app.modules.reconciliation.statements import StatementLineView
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Money
from app.shared.push_ids import push_id

PROPOSE_OPERATION = "payouts.propose_bank_bulk_settlement"
APPROVE_OPERATION = "payouts.approve_bank_bulk_settlement"

# How long after a batch is approved its bulk debit is looked for on the statement.
LINE_WINDOW = timedelta(days=45)


@dataclass(frozen=True, slots=True)
class BulkView:
    id: str
    batch_id: str
    statement_line_id: str
    reason: str
    status: Decision
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    payout_ids: tuple[str, ...]
    total: Money
    # The statement line itself, so the second person sees what the bank took. None only if
    # the line has left the reconciliation search window.
    line: StatementLineView | None


@dataclass(frozen=True, slots=True)
class BulkDebit:
    """What the first person chooses from: the batch's payouts still to settle, and the trust
    statement lines going out since it was approved that no settlement names yet and that are
    no larger than those payouts together."""

    batch: BatchView
    payouts: tuple[PayoutView, ...]
    lines: tuple[StatementLineView, ...]


_COLUMNS = (
    "id, batch_id, statement_line_id, reason, status, proposed_by, proposed_at, decided_by, "
    "decided_at, decision_reason"
)
_INSERT = text(
    "INSERT INTO payout_bank_bulk_settlements (id, batch_id, statement_line_id, reason, status, "
    "proposed_by, proposed_at) VALUES (:id, :batch_id, :statement_line_id, :reason, 'proposed', "
    ":proposed_by, :now)"
)
_INSERT_PAYOUT = text(
    "INSERT INTO payout_bank_settlements (id, payout_id, batch_id, outcome, statement_line_id, "
    "statement_id, reason, status, proposed_by, proposed_at, bulk_id) VALUES (:id, :payout_id, "
    ":batch_id, 'paid', :statement_line_id, NULL, :reason, 'proposed', :proposed_by, :now, "
    ":bulk_id)"
)
_GET = text(f"SELECT {_COLUMNS} FROM payout_bank_bulk_settlements WHERE id = :id")  # noqa: S608
_LOCK = text(
    f"SELECT {_COLUMNS} FROM payout_bank_bulk_settlements WHERE id = :id FOR UPDATE"  # noqa: S608
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM payout_bank_bulk_settlements "  # noqa: S608
    "WHERE status = 'proposed' ORDER BY proposed_at, id LIMIT :limit"
)
_PAYOUTS_OF = text(
    "SELECT s.id, s.payout_id, p.amount_minor FROM payout_bank_settlements s "
    "JOIN payouts p ON p.id = s.payout_id WHERE s.bulk_id = :id ORDER BY p.created_at, p.id"
)
_DECIDE = text(
    "UPDATE payout_bank_bulk_settlements SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)
_DECIDE_PAYOUTS = text(
    "UPDATE payout_bank_settlements SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_reason = :reason WHERE bulk_id = :id"
)
# The batch's sent payouts that no open settlement names, oldest first.
_TO_SETTLE = text(
    "SELECT p.id FROM payout_bank_batch_items i JOIN payouts p ON p.id = i.payout_id "
    "WHERE i.batch_id = :batch_id AND p.status = 'sent' AND NOT EXISTS ("
    "SELECT FROM payout_bank_settlements s WHERE s.payout_id = p.id AND s.status = 'proposed') "
    "ORDER BY p.created_at, p.id"
)
_LINES_IN_USE = text(
    "SELECT statement_line_id FROM payout_bank_settlements WHERE statement_line_id IN :ids "
    "AND status IN ('proposed', 'approved') UNION "
    "SELECT statement_line_id FROM payout_bank_bulk_settlements WHERE statement_line_id IN :ids "
    "AND status IN ('proposed', 'approved')"
).bindparams(bindparam("ids", expanding=True))
_FITS = text(
    "SELECT public.finvest_payout_bank_bulk_line_fits(ROW(CAST(:id AS text), "
    "CAST(:batch_id AS text), CAST(:statement_line_id AS text), CAST(:reason AS text), "
    "'proposed', CAST(:proposed_by AS text), CAST(:now AS timestamptz), NULL, NULL, NULL)"
    "::public.payout_bank_bulk_settlements) AS fits"
)


async def bulk_debit(session: AsyncSession, batch_id: str) -> BulkDebit:
    """The approved batch's payouts still to settle, and the statement lines that could be the
    debit that paid some or all of them. Staff transactions only."""
    batch = await bank_payouts.get_batch(session, batch_id)
    if batch.status is not BatchStatus.APPROVED or batch.decided_at is None:
        raise BankPayoutRefused("batch_not_approved")
    rows = await session.execute(_TO_SETTLE, {"batch_id": batch.id})
    to_settle = tuple([await payouts.get_payout(session, row.id) for row in rows.all()])
    owed = sum(each.amount.minor for each in to_settle)
    approved_on = business_date(batch.decided_at)
    # A busy trust account may hold more lines than one search returns, so the debit for every
    # payout still to settle, the usual case, is also searched for by its amount.
    lines = list(
        {
            line.id: line
            for line in [
                *await _lines_since(session, batch, approved_on),
                *await _lines_since(session, batch, approved_on, Money(owed, batch.currency)),
            ]
        }.values()
    )
    lines.sort(key=lambda line: (line.value_date, line.id))
    in_use = await _in_use(session, [line.id for line in lines])
    return BulkDebit(
        batch,
        to_settle,
        tuple(line for line in lines if line.id not in in_use and 0 < line.amount.minor <= owed),
    )


async def propose(
    session: AsyncSession,
    staff: Actor,
    batch_id: str,
    *,
    statement_line_id: str,
    payout_ids: Sequence[str],
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> BulkView:
    """The first person says one bulk debit paid these payouts of one batch. A different person
    must approve it."""
    _check(staff, reason)
    chosen = list(dict.fromkeys(payout_ids))
    if not chosen or len(chosen) != len(payout_ids) or len(chosen) > bank_payouts.MAX_BATCH:
        raise BankPayoutRefused("bulk_payouts_invalid", "validation_failed")

    async def command() -> Receipt:
        debit = await bulk_debit(session, batch_id)
        free = {each.id: each for each in debit.payouts}
        for payout_id in chosen:
            if payout_id not in debit.batch.payout_ids:
                raise BankPayoutRefused("payout_not_in_batch")
            if payout_id not in free:
                # Settled already, or another settlement is open for it.
                raise BankPayoutRefused("payout_not_to_settle")
        if await _in_use(session, [statement_line_id]):
            raise BankPayoutRefused("statement_line_in_use")
        total = Money(sum(free[each].amount.minor for each in chosen), debit.batch.currency)
        line = await _line(session, debit.batch, statement_line_id, total)
        if line is None:
            raise BankPayoutRefused("statement_line_not_this_batch")
        if line.amount != total:
            raise BankPayoutRefused("amounts_do_not_add_up")
        bulk_id = push_id()
        values = {
            "id": bulk_id,
            "batch_id": debit.batch.id,
            "statement_line_id": line.id,
            "reason": reason.strip(),
            "proposed_by": staff.id,
            "now": clock.now(),
        }
        # The database checks the same line on insert; asking first names the refusal.
        if not (await session.execute(_FITS, values)).scalar_one():
            raise BankPayoutRefused("statement_line_not_this_batch")
        await session.execute(_INSERT, values)
        for payout_id in chosen:
            await session.execute(
                _INSERT_PAYOUT,
                {**values, "id": push_id(), "payout_id": payout_id, "bulk_id": bulk_id},
            )
        await _audit(
            session, staff, "payouts.bank_bulk_settlement_proposed", bulk_id, reason, clock
        )
        return Receipt(201, {"bulk_id": bulk_id})

    done = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={
            "batch_id": batch_id,
            "statement_line_id": statement_line_id,
            "payout_ids": chosen,
            "reason": reason.strip(),
        },
        now=clock.now(),
        command=command,
    )
    return await get_bulk(session, str(done.receipt.response["bulk_id"]))


async def approve(
    session: AsyncSession,
    staff: Actor,
    bulk_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> BulkView:
    """A different person approves. Every payout in it becomes paid, settled from the trust
    account, and each client is told, all in this transaction."""
    _check(staff, reason)

    async def command() -> Receipt:
        bulk = await _open(session, bulk_id)
        if bulk.proposed_by == staff.id:
            raise BankPayoutRefused("proposed_by_you", "forbidden")
        await _decide(session, bulk.id, Decision.APPROVED, staff, reason, clock)
        for payout_id in bulk.payout_ids:
            try:
                moved = await payouts.move_bank_payout(
                    session, staff, payout_id, PayoutEvent.BANK_SETTLED_PAID, clock
                )
            except IllegalTransition:
                raise BankPayoutRefused("payout_not_sent") from None
            await tell_client(session, moved, clock.now())
        await _audit(
            session, staff, "payouts.bank_bulk_settlement_approved", bulk.id, reason, clock
        )
        return Receipt(200, {"bulk_id": bulk.id})

    await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"bulk_id": bulk_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await get_bulk(session, bulk_id)


async def decline(
    session: AsyncSession, staff: Actor, bulk_id: str, *, reason: str, clock: Clock
) -> BulkView:
    """Someone other than the proposer refuses it. Its payouts stay sent, to be settled again."""
    _check(staff, reason)
    bulk = await _open(session, bulk_id)
    if bulk.proposed_by == staff.id:
        raise BankPayoutRefused("proposed_by_you", "forbidden")
    await _decide(session, bulk.id, Decision.DECLINED, staff, reason, clock)
    await _audit(session, staff, "payouts.bank_bulk_settlement_declined", bulk.id, reason, clock)
    return await get_bulk(session, bulk_id)


async def withdraw(
    session: AsyncSession, staff: Actor, bulk_id: str, *, reason: str, clock: Clock
) -> BulkView:
    """The proposer takes it back. Its payouts stay sent."""
    _check(staff, reason)
    bulk = await _open(session, bulk_id)
    if bulk.proposed_by != staff.id:
        raise BankPayoutRefused("not_proposed_by_you", "forbidden")
    await _decide(session, bulk.id, Decision.WITHDRAWN, staff, reason, clock)
    await _audit(session, staff, "payouts.bank_bulk_settlement_withdrawn", bulk.id, reason, clock)
    return await get_bulk(session, bulk_id)


async def get_bulk(session: AsyncSession, bulk_id: str) -> BulkView:
    row = (await session.execute(_GET, {"id": bulk_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return await _view(session, row)


async def waiting(session: AsyncSession, *, limit: int = 50) -> list[BulkView]:
    """Bulk settlements waiting for a second person, oldest first. Empty outside staff."""
    rows = await session.execute(_WAITING, {"limit": max(1, min(limit, 100))})
    return [await _view(session, row) for row in rows.all()]


# Helpers.


async def _lines_since(
    session: AsyncSession, batch: BatchView, approved_on: date, amount: Money | None = None
) -> list[StatementLineView]:
    found = await statements.find_statement_lines(
        session,
        source=StatementSource.BANK,
        date_from=approved_on,
        date_to=approved_on + LINE_WINDOW,
        amount=amount,
    )
    return [
        line
        for line in found
        if line.account_reference == "trust"
        and line.direction is Direction.OUT
        and line.amount.currency is batch.currency
    ]


async def _line(
    session: AsyncSession, batch: BatchView, line_id: str, total: Money
) -> StatementLineView | None:
    """The chosen line, if it is money out of the trust account since the batch was approved.
    Searched for by the chosen payouts' total first, which finds it however busy the account;
    then by date alone, so a line for another amount is named as not adding up."""
    assert batch.decided_at is not None
    approved_on = business_date(batch.decided_at)
    for amount in (total, None):
        lines = await _lines_since(session, batch, approved_on, amount)
        found = next((line for line in lines if line.id == line_id), None)
        if found is not None:
            return found
    return None


async def _in_use(session: AsyncSession, line_ids: list[str]) -> set[str]:
    if not line_ids:
        return set()
    rows = await session.execute(_LINES_IN_USE, {"ids": line_ids})
    return set(rows.scalars())


def _check(staff: Actor, reason: str) -> None:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    if not reason.strip():
        raise ApplicationError("validation_failed")


async def _open(session: AsyncSession, bulk_id: str) -> BulkView:
    row = (await session.execute(_LOCK, {"id": bulk_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    bulk = await _view(session, row)
    if bulk.status is not Decision.PROPOSED:
        raise BankPayoutRefused("settlement_already_decided")
    return bulk


async def _decide(
    session: AsyncSession,
    bulk_id: str,
    status: Decision,
    staff: Actor,
    reason: str,
    clock: Clock,
) -> None:
    """The bulk first, then each payout in it, as the database checks."""
    values = {
        "id": bulk_id,
        "status": status.value,
        "decided_by": staff.id,
        "now": clock.now(),
        "reason": reason.strip(),
    }
    await session.execute(_DECIDE, values)
    await session.execute(_DECIDE_PAYOUTS, values)


async def _view(session: AsyncSession, row: Row[Any]) -> BulkView:
    batch = await bank_payouts.get_batch(session, row.batch_id)
    items = (await session.execute(_PAYOUTS_OF, {"id": row.id})).all()
    total = Money(sum(int(item.amount_minor) for item in items), batch.currency)
    return BulkView(
        id=row.id,
        batch_id=row.batch_id,
        statement_line_id=row.statement_line_id,
        reason=row.reason,
        status=Decision(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        payout_ids=tuple(item.payout_id for item in items),
        total=total,
        line=await _line(session, batch, row.statement_line_id, total),
    )


async def _audit(
    session: AsyncSession, staff: Actor, action: str, bulk_id: str, reason: str, clock: Clock
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval",
            action=action,
            resource_type="payout_bank_bulk_settlement",
            resource_id=bulk_id,
            actor=AuditActor(staff.kind, staff.id),
            reason=reason.strip(),
        ),
        clock,
    )
