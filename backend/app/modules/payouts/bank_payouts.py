"""Paying bank payouts from the trust account, in batches, and settling each one from the
trust statement.

The worker sends mobile money only. A bank payout waits, queued, for staff:

1. One member of staff prepares a batch of queued bank payouts in one currency, with a reason.
2. A different one approves it. In the same transaction every payout in it moves from queued to
   sent, and the client is told it is being processed. The preparer may withdraw a batch, and
   anyone else may decline it, which frees its payouts for another batch.
3. The approved batch's transfer file is downloaded and uploaded in the bank's portal. Each
   download writes an access event, because the file holds whole account numbers.
4. Once the trust statement is in, one member of staff proposes how each payout ended: paid,
   naming the statement line that took the money, or failed, naming a statement at least two
   days after the approval with no line that could be it. A different one approves it, which
   settles the money from the trust account or parks it in the client's "payout pending"
   account, and tells the client.

Every step needs a reason. Preparing, approving and declining a batch, and proposing and
approving a settlement, take an idempotency key. Migration 0039 holds each rule in the database.
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import Row, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain.banks import find_bank
from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.payouts import IllegalTransition, PayoutEvent, PayoutStatus
from app.modules.payouts.jobs import tell_client
from app.modules.payouts.payouts import PayoutView
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

MAX_BATCH = 200

PREPARE_OPERATION = "payouts.prepare_bank_batch"
APPROVE_OPERATION = "payouts.approve_bank_batch"
DECLINE_OPERATION = "payouts.decline_bank_batch"
PROPOSE_SETTLEMENT_OPERATION = "payouts.propose_bank_settlement"
APPROVE_SETTLEMENT_OPERATION = "payouts.approve_bank_settlement"

# The columns of the transfer file, in order. The payout ID is the reference the bank is asked
# to carry, so the statement line can be told apart from others of the same amount.
FILE_COLUMNS = (
    "Reference",
    "Beneficiary name",
    "Bank",
    "Account number",
    "Amount",
    "Currency",
    "Narrative",
)


class BatchStatus(StrEnum):
    PREPARED = "prepared"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class Decision(StrEnum):
    """Where a settlement is."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class BankPayoutRefused(Exception):
    """A step the rules do not allow. `code` is safe to show; `status` is the HTTP meaning."""

    def __init__(
        self, code: str, status: Literal["conflict", "forbidden", "validation_failed"] = "conflict"
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class BatchView:
    id: str
    currency: Currency
    reason: str
    status: BatchStatus
    prepared_by: str
    prepared_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    payout_ids: tuple[str, ...]
    total: Money


@dataclass(frozen=True, slots=True)
class BatchItem:
    """One payout in a batch, as the second person checks it: the amount, and the approved
    account it goes to, masked."""

    payout_id: str
    client_id: str
    amount: Money
    bank_name: str | None
    masked_account: str
    holder_name: str


@dataclass(frozen=True, slots=True)
class SettlementView:
    id: str
    payout_id: str
    batch_id: str
    outcome: Literal["paid", "failed"]
    statement_line_id: str | None
    statement_id: str | None
    reason: str
    status: Decision
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    # The bulk settlement this is one payout of, decided only with it (bulk_settlements.py).
    bulk_id: str | None = None


@dataclass(frozen=True, slots=True)
class SentBankPayout:
    """A bank payout that has left in an approved batch and waits to be settled."""

    payout: PayoutView
    batch_id: str
    sent_at: datetime
    settlement: SettlementView | None


_BATCH_COLUMNS = (
    "id, currency, reason, status, prepared_by, prepared_at, decided_by, decided_at, "
    "decision_reason"
)
_SETTLEMENT_COLUMNS = (
    "id, payout_id, batch_id, outcome, statement_line_id, statement_id, reason, status, "
    "proposed_by, proposed_at, decided_by, decided_at, decision_reason, bulk_id"
)
# Queued bank payouts in no batch that is prepared or approved, oldest first.
_WAITING_PAYOUTS = text(
    "SELECT p.id FROM payouts p WHERE p.status = 'queued' AND p.kind = 'bank' "
    "AND p.currency = :currency AND NOT EXISTS (SELECT FROM payout_bank_batch_items i "
    "JOIN payout_bank_batches b ON b.id = i.batch_id WHERE i.payout_id = p.id "
    "AND b.status IN ('prepared', 'approved')) ORDER BY p.created_at, p.id LIMIT :limit"
)
# The same test as the waiting list, asked only of the payouts chosen, so a batch can take a
# payout from beyond the first page of the list.
_FREE_AMONG = text(
    "SELECT p.id FROM payouts p WHERE p.id IN :ids AND p.status = 'queued' AND p.kind = 'bank' "
    "AND p.currency = :currency AND NOT EXISTS (SELECT FROM payout_bank_batch_items i "
    "JOIN payout_bank_batches b ON b.id = i.batch_id WHERE i.payout_id = p.id "
    "AND b.status IN ('prepared', 'approved'))"
).bindparams(bindparam("ids", expanding=True))
_INSERT_BATCH = text(
    "INSERT INTO payout_bank_batches (id, currency, reason, status, prepared_by, prepared_at) "
    "VALUES (:id, :currency, :reason, 'prepared', :prepared_by, :now)"
)
_INSERT_ITEM = text(
    "INSERT INTO payout_bank_batch_items (batch_id, payout_id) VALUES (:batch_id, :payout_id)"
)
_GET_BATCH = text(f"SELECT {_BATCH_COLUMNS} FROM payout_bank_batches WHERE id = :id")  # noqa: S608
_LOCK_BATCH = text(
    f"SELECT {_BATCH_COLUMNS} FROM payout_bank_batches WHERE id = :id FOR UPDATE"  # noqa: S608
)
_ITEMS = text(
    "SELECT i.payout_id, p.amount_minor FROM payout_bank_batch_items i "
    "JOIN payouts p ON p.id = i.payout_id WHERE i.batch_id = :id ORDER BY p.created_at, p.id"
)
_BATCHES = text(
    f"SELECT {_BATCH_COLUMNS} FROM payout_bank_batches "  # noqa: S608
    "WHERE status = ANY(:statuses) ORDER BY prepared_at DESC, id DESC LIMIT :limit"
)
_DECIDE_BATCH = text(
    "UPDATE payout_bank_batches SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)
_BATCH_OF = text(
    "SELECT b.id, b.decided_at FROM payout_bank_batch_items i "
    "JOIN payout_bank_batches b ON b.id = i.batch_id "
    "WHERE i.payout_id = :payout_id AND b.status = 'approved'"
)
_SENT = text(
    "SELECT p.id AS payout_id, b.id AS batch_id, b.decided_at AS sent_at FROM payouts p "
    "JOIN payout_bank_batch_items i ON i.payout_id = p.id "
    "JOIN payout_bank_batches b ON b.id = i.batch_id AND b.status = 'approved' "
    "WHERE p.status = 'sent' AND p.kind = 'bank' ORDER BY b.decided_at, p.id LIMIT :limit"
)
_INSERT_SETTLEMENT = text(
    "INSERT INTO payout_bank_settlements (id, payout_id, batch_id, outcome, statement_line_id, "
    "statement_id, reason, status, proposed_by, proposed_at) VALUES (:id, :payout_id, "
    ":batch_id, :outcome, :statement_line_id, :statement_id, :reason, 'proposed', "
    ":proposed_by, :now)"
)
_GET_SETTLEMENT = text(
    f"SELECT {_SETTLEMENT_COLUMNS} FROM payout_bank_settlements WHERE id = :id"  # noqa: S608
)
_LOCK_SETTLEMENT = text(
    f"SELECT {_SETTLEMENT_COLUMNS} FROM payout_bank_settlements "  # noqa: S608
    "WHERE id = :id FOR UPDATE"
)
_OPEN_SETTLEMENT = text(
    f"SELECT {_SETTLEMENT_COLUMNS} FROM payout_bank_settlements "  # noqa: S608
    "WHERE payout_id = :payout_id AND status = 'proposed'"
)
# A line is named by one live settlement at a time: one payout's, or one bulk's.
_LINE_IN_USE = text(
    "SELECT 1 FROM payout_bank_settlements WHERE statement_line_id = :line_id "
    "AND status IN ('proposed', 'approved') UNION ALL "
    "SELECT 1 FROM payout_bank_bulk_settlements WHERE statement_line_id = :line_id "
    "AND status IN ('proposed', 'approved')"
)
_DECIDE_SETTLEMENT = text(
    "UPDATE payout_bank_settlements SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)
_EVIDENCE = text(
    "SELECT public.finvest_payout_bank_settlement_evidence(ROW(CAST(:id AS text), "
    "CAST(:payout_id AS text), CAST(:batch_id AS text), CAST(:outcome AS text), "
    "CAST(:statement_line_id AS text), CAST(:statement_id AS text), CAST(:reason AS text), "
    "'proposed', CAST(:proposed_by AS text), CAST(:now AS timestamptz), NULL, NULL, NULL, "
    "NULL)"
    "::public.payout_bank_settlements) AS shown"
)


# Batches.


async def waiting_payouts(
    session: AsyncSession, currency: Currency, *, limit: int = MAX_BATCH
) -> list[PayoutView]:
    """Queued bank payouts no live batch holds, oldest first. Empty outside staff."""
    rows = await session.execute(
        _WAITING_PAYOUTS,
        {"currency": currency.value, "limit": max(1, min(limit, MAX_BATCH))},
    )
    return [await payouts.get_payout(session, row.id) for row in rows.all()]


async def prepare_batch(
    session: AsyncSession,
    staff: Actor,
    *,
    currency: Currency,
    payout_ids: Sequence[str],
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> BatchView:
    """The first person puts the queued bank payouts they chose into a batch for a second
    person to approve. Nothing leaves yet."""
    _check(staff, reason)
    chosen = list(dict.fromkeys(payout_ids))
    if not chosen or len(chosen) != len(payout_ids) or len(chosen) > MAX_BATCH:
        raise BankPayoutRefused("batch_payouts_invalid", "validation_failed")

    async def command() -> Receipt:
        batch_id = push_id()
        await session.execute(
            _INSERT_BATCH,
            {
                "id": batch_id,
                "currency": currency.value,
                "reason": reason.strip(),
                "prepared_by": staff.id,
                "now": clock.now(),
            },
        )
        rows = await session.execute(_FREE_AMONG, {"ids": chosen, "currency": currency.value})
        free = {row.id for row in rows.all()}
        for payout_id in chosen:
            if payout_id not in free:
                # Not a queued bank payout in this currency, or already in another batch. The
                # database refuses it too; checking first names it.
                payout = await _payout_or_none(session, payout_id)
                if payout is None or payout.kind is not DestinationKind.BANK:
                    raise BankPayoutRefused("payout_not_bank")
                if payout.amount.currency is not currency:
                    raise BankPayoutRefused("currency_not_supported", "validation_failed")
                if payout.status is not PayoutStatus.QUEUED:
                    raise BankPayoutRefused("payout_not_queued")
                raise BankPayoutRefused("payout_in_another_batch")
            await session.execute(_INSERT_ITEM, {"batch_id": batch_id, "payout_id": payout_id})
        await _audit(session, staff, "payouts.bank_batch_prepared", batch_id, reason, clock)
        return Receipt(201, {"batch_id": batch_id})

    done = await run_once(
        session,
        actor=staff,
        operation=PREPARE_OPERATION,
        key=idempotency_key,
        request={"currency": currency.value, "payout_ids": chosen, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await get_batch(session, str(done.receipt.response["batch_id"]))


async def approve_batch(
    session: AsyncSession,
    staff: Actor,
    batch_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> BatchView:
    """A different person approves the batch, and every payout in it is sent from the trust
    account in the same transaction. The transfer file can then be downloaded."""
    _check(staff, reason)

    async def command() -> Receipt:
        batch = await _open_batch(session, batch_id)
        if batch.prepared_by == staff.id:
            raise BankPayoutRefused("prepared_by_you", "forbidden")
        await _decide_batch(session, batch.id, BatchStatus.APPROVED, staff, reason, clock)
        for payout_id in batch.payout_ids:
            try:
                moved = await payouts.move_bank_payout(
                    session, staff, payout_id, PayoutEvent.BANK_BATCH_APPROVED, clock
                )
            except IllegalTransition:
                # Only a queued payout can be in a prepared batch, and nothing else moves a
                # queued bank payout. Refuse rather than send part of a batch.
                raise BankPayoutRefused("payout_not_queued") from None
            await tell_client(session, moved, clock.now())
        await _audit(session, staff, "payouts.bank_batch_approved", batch.id, reason, clock)
        return Receipt(200, {"batch_id": batch.id})

    await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"batch_id": batch_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await get_batch(session, batch_id)


async def decline_batch(
    session: AsyncSession,
    staff: Actor,
    batch_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> BatchView:
    """Someone other than the preparer refuses the batch. Its payouts stay queued and are free
    for another batch. Nothing has left, so one person may do it."""
    _check(staff, reason)

    async def command() -> Receipt:
        batch = await _open_batch(session, batch_id)
        if batch.prepared_by == staff.id:
            raise BankPayoutRefused("prepared_by_you", "forbidden")
        await _decide_batch(session, batch.id, BatchStatus.DECLINED, staff, reason, clock)
        await _audit(session, staff, "payouts.bank_batch_declined", batch.id, reason, clock)
        return Receipt(200, {"batch_id": batch.id})

    await run_once(
        session,
        actor=staff,
        operation=DECLINE_OPERATION,
        key=idempotency_key,
        request={"batch_id": batch_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await get_batch(session, batch_id)


async def withdraw_batch(
    session: AsyncSession, staff: Actor, batch_id: str, *, reason: str, clock: Clock
) -> BatchView:
    """The preparer takes their batch back. Its payouts stay queued."""
    _check(staff, reason)
    batch = await _open_batch(session, batch_id)
    if batch.prepared_by != staff.id:
        raise BankPayoutRefused("not_prepared_by_you", "forbidden")
    await _decide_batch(session, batch.id, BatchStatus.WITHDRAWN, staff, reason, clock)
    await _audit(session, staff, "payouts.bank_batch_withdrawn", batch.id, reason, clock)
    return await get_batch(session, batch_id)


async def get_batch(session: AsyncSession, batch_id: str) -> BatchView:
    row = (await session.execute(_GET_BATCH, {"id": batch_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return await _batch_view(session, row)


async def batch_items(session: AsyncSession, batch: BatchView) -> list[BatchItem]:
    """Each payout in the batch with its account masked, for the person deciding it. Account
    numbers in full are only in the transfer file."""
    items: list[BatchItem] = []
    for payout_id in batch.payout_ids:
        payout = await payouts.get_payout(session, payout_id)
        destination = await destinations.get_destination(session, payout.destination_id)
        bank = find_bank(destination.bank_code or "")
        items.append(
            BatchItem(
                payout_id=payout.id,
                client_id=payout.client_id,
                amount=payout.amount,
                bank_name=None if bank is None else bank.name,
                masked_account=destination.masked_account,
                holder_name=destination.holder_name,
            )
        )
    return items


async def batches(
    session: AsyncSession, statuses: Sequence[BatchStatus], *, limit: int = 50
) -> list[BatchView]:
    """Batches in these statuses, newest first. Empty outside a staff transaction."""
    rows = await session.execute(
        _BATCHES,
        {"statuses": [each.value for each in statuses], "limit": max(1, min(limit, 100))},
    )
    return [await _batch_view(session, row) for row in rows.all()]


async def transfer_file(session: AsyncSession, staff: Actor, batch_id: str, clock: Clock) -> str:
    """The approved batch as a CSV file to upload in the bank's portal.

    It holds whole account numbers and names, so it exists only for an approved batch and each
    download writes an access event. It is never stored.
    """
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    batch = await get_batch(session, batch_id)
    if batch.status is not BatchStatus.APPROVED:
        raise BankPayoutRefused("batch_not_approved")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(FILE_COLUMNS)
    for payout_id in batch.payout_ids:
        payout = await payouts.get_payout(session, payout_id)
        destination = await destinations.get_destination(session, payout.destination_id)
        bank = find_bank(destination.bank_code or "")
        writer.writerow(
            (
                payout.id,
                _cell(destination.holder_name),
                "" if bank is None else bank.name,
                await destinations.account_number(session, destination.id),
                _amount(payout.amount),
                payout.amount.currency.value,
                f"Finvest {payout.id}",
            )
        )
    await audit.record(
        session,
        AuditEvent(
            category="access",
            action="payouts.bank_batch_file_downloaded",
            resource_type="payout_bank_batch",
            resource_id=batch.id,
            actor=AuditActor(staff.kind, staff.id),
        ),
        clock,
    )
    return out.getvalue()


# Settlements.


async def sent_payouts(session: AsyncSession, *, limit: int = 100) -> list[SentBankPayout]:
    """Bank payouts sent in an approved batch and not yet settled, oldest first, each with its
    open settlement. Empty outside a staff transaction."""
    rows = (await session.execute(_SENT, {"limit": max(1, min(limit, 200))})).all()
    found: list[SentBankPayout] = []
    for row in rows:
        open_row = (
            await session.execute(_OPEN_SETTLEMENT, {"payout_id": row.payout_id})
        ).one_or_none()
        found.append(
            SentBankPayout(
                await payouts.get_payout(session, row.payout_id),
                row.batch_id,
                row.sent_at,
                None if open_row is None else _settlement_view(open_row),
            )
        )
    return found


async def propose_settlement(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    *,
    outcome: Literal["paid", "failed"],
    statement_line_id: str | None,
    statement_id: str | None,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> SettlementView:
    """The first person's word on how a bank payout ended, with the trust statement as
    evidence. A different person must approve it."""
    _check(staff, reason)
    if outcome == "paid" and (statement_line_id is None or statement_id is not None):
        raise BankPayoutRefused("statement_line_required", "validation_failed")
    if outcome == "failed" and (statement_id is None or statement_line_id is not None):
        raise BankPayoutRefused("statement_required", "validation_failed")

    async def command() -> Receipt:
        payout = await payouts.get_payout(session, payout_id)
        if payout.kind is not DestinationKind.BANK:
            raise BankPayoutRefused("payout_not_bank")
        batch = (await session.execute(_BATCH_OF, {"payout_id": payout_id})).one_or_none()
        if payout.status is not PayoutStatus.SENT or batch is None:
            raise BankPayoutRefused("payout_not_sent")
        if (await session.execute(_OPEN_SETTLEMENT, {"payout_id": payout_id})).first():
            raise BankPayoutRefused("settlement_already_proposed")
        if (
            statement_line_id is not None
            and (await session.execute(_LINE_IN_USE, {"line_id": statement_line_id})).first()
        ):
            raise BankPayoutRefused("statement_line_in_use")
        settlement_id = push_id()
        values = {
            "id": settlement_id,
            "payout_id": payout_id,
            "batch_id": batch.id,
            "outcome": outcome,
            "statement_line_id": statement_line_id,
            "statement_id": statement_id,
            "reason": reason.strip(),
            "proposed_by": staff.id,
            "now": clock.now(),
        }
        # The database checks the same evidence on insert; asking first names the refusal.
        if not (await session.execute(_EVIDENCE, values)).scalar_one():
            raise BankPayoutRefused(
                "statement_line_not_this_payout" if outcome == "paid" else "statement_not_enough"
            )
        await session.execute(_INSERT_SETTLEMENT, values)
        await _audit(
            session, staff, "payouts.bank_settlement_proposed", payout_id, reason, clock,
            resource_type="payout",
        )  # fmt: skip
        return Receipt(201, {"settlement_id": settlement_id})

    done = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_SETTLEMENT_OPERATION,
        key=idempotency_key,
        request={
            "payout_id": payout_id,
            "outcome": outcome,
            "statement_line_id": statement_line_id,
            "statement_id": statement_id,
            "reason": reason.strip(),
        },
        now=clock.now(),
        command=command,
    )
    return await get_settlement(session, str(done.receipt.response["settlement_id"]))


async def approve_settlement(
    session: AsyncSession,
    staff: Actor,
    settlement_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> PayoutView:
    """A different person approves. The payout becomes paid, settled from the trust account, or
    failed, parked in the client's "payout pending" account, and the client is told."""
    _check(staff, reason)

    async def command() -> Receipt:
        settlement = await _open_settlement(session, settlement_id)
        if settlement.proposed_by == staff.id:
            raise BankPayoutRefused("proposed_by_you", "forbidden")
        await _decide_settlement(session, settlement.id, Decision.APPROVED, staff, reason, clock)
        event = (
            PayoutEvent.BANK_SETTLED_PAID
            if settlement.outcome == "paid"
            else PayoutEvent.BANK_SETTLED_FAILED
        )
        try:
            moved = await payouts.move_bank_payout(
                session, staff, settlement.payout_id, event, clock
            )
        except IllegalTransition:
            raise BankPayoutRefused("payout_not_sent") from None
        await tell_client(session, moved, clock.now())
        await _audit(
            session, staff, "payouts.bank_settlement_approved", moved.id, reason, clock,
            resource_type="payout",
        )  # fmt: skip
        return Receipt(200, {"payout_id": moved.id})

    done = await run_once(
        session,
        actor=staff,
        operation=APPROVE_SETTLEMENT_OPERATION,
        key=idempotency_key,
        request={"settlement_id": settlement_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await payouts.get_payout(session, str(done.receipt.response["payout_id"]))


async def decline_settlement(
    session: AsyncSession, staff: Actor, settlement_id: str, *, reason: str, clock: Clock
) -> SettlementView:
    """Someone other than the proposer refuses it. The payout stays sent, to be proposed again."""
    _check(staff, reason)
    settlement = await _open_settlement(session, settlement_id)
    if settlement.proposed_by == staff.id:
        raise BankPayoutRefused("proposed_by_you", "forbidden")
    await _decide_settlement(session, settlement.id, Decision.DECLINED, staff, reason, clock)
    await _audit(
        session, staff, "payouts.bank_settlement_declined", settlement.payout_id, reason, clock,
        resource_type="payout",
    )  # fmt: skip
    return await get_settlement(session, settlement_id)


async def withdraw_settlement(
    session: AsyncSession, staff: Actor, settlement_id: str, *, reason: str, clock: Clock
) -> SettlementView:
    """The proposer takes it back."""
    _check(staff, reason)
    settlement = await _open_settlement(session, settlement_id)
    if settlement.proposed_by != staff.id:
        raise BankPayoutRefused("not_proposed_by_you", "forbidden")
    await _decide_settlement(session, settlement.id, Decision.WITHDRAWN, staff, reason, clock)
    await _audit(
        session, staff, "payouts.bank_settlement_withdrawn", settlement.payout_id, reason, clock,
        resource_type="payout",
    )  # fmt: skip
    return await get_settlement(session, settlement_id)


async def get_settlement(session: AsyncSession, settlement_id: str) -> SettlementView:
    row = (await session.execute(_GET_SETTLEMENT, {"id": settlement_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _settlement_view(row)


# Helpers.


def _check(staff: Actor, reason: str) -> None:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    if not reason.strip():
        raise ApplicationError("validation_failed")


async def _payout_or_none(session: AsyncSession, payout_id: str) -> PayoutView | None:
    try:
        return await payouts.get_payout(session, payout_id)
    except ApplicationError:
        return None


async def _open_batch(session: AsyncSession, batch_id: str) -> BatchView:
    row = (await session.execute(_LOCK_BATCH, {"id": batch_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    batch = await _batch_view(session, row)
    if batch.status is not BatchStatus.PREPARED:
        raise BankPayoutRefused("batch_already_decided")
    return batch


async def _open_settlement(session: AsyncSession, settlement_id: str) -> SettlementView:
    row = (await session.execute(_LOCK_SETTLEMENT, {"id": settlement_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    settlement = _settlement_view(row)
    if settlement.status is not Decision.PROPOSED:
        raise BankPayoutRefused("settlement_already_decided")
    if settlement.bulk_id is not None:
        # One payout of a bulk debit: decided only with the bulk, all together.
        raise BankPayoutRefused("settlement_in_bulk")
    return settlement


async def _decide_batch(
    session: AsyncSession,
    batch_id: str,
    status: BatchStatus,
    staff: Actor,
    reason: str,
    clock: Clock,
) -> None:
    await session.execute(
        _DECIDE_BATCH,
        {
            "id": batch_id,
            "status": status.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
        },
    )


async def _decide_settlement(
    session: AsyncSession,
    settlement_id: str,
    status: Decision,
    staff: Actor,
    reason: str,
    clock: Clock,
) -> None:
    await session.execute(
        _DECIDE_SETTLEMENT,
        {
            "id": settlement_id,
            "status": status.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
        },
    )


async def _batch_view(session: AsyncSession, row: Row[Any]) -> BatchView:
    currency = Currency(row.currency)
    items = (await session.execute(_ITEMS, {"id": row.id})).all()
    total = sum((int(item.amount_minor) for item in items), 0)
    return BatchView(
        id=row.id,
        currency=currency,
        reason=row.reason,
        status=BatchStatus(row.status),
        prepared_by=row.prepared_by,
        prepared_at=row.prepared_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        payout_ids=tuple(item.payout_id for item in items),
        total=Money(total, currency),
    )


def _settlement_view(row: Row[Any]) -> SettlementView:
    return SettlementView(
        id=row.id,
        payout_id=row.payout_id,
        batch_id=row.batch_id,
        outcome=row.outcome,
        statement_line_id=row.statement_line_id,
        statement_id=row.statement_id,
        reason=row.reason,
        status=Decision(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        bulk_id=row.bulk_id,
    )


def _amount(money: Money) -> str:
    """The amount as the bank's file wants it: major units, no separators, as many decimal
    places as the currency has (none for UGX)."""
    digits = money.currency.minor_digits
    if digits == 0:
        return str(money.minor)
    whole, part = divmod(money.minor, 10**digits)
    return f"{whole}.{part:0{digits}d}"


def _cell(value: str) -> str:
    """A name as a CSV cell that a spreadsheet will not run as a formula."""
    return "'" + value if value[:1] in _FORMULA_STARTS else value


# Spreadsheets read a cell starting with any of these as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


async def _audit(
    session: AsyncSession,
    staff: Actor,
    action: str,
    resource_id: str,
    reason: str,
    clock: Clock,
    *,
    resource_type: str = "payout_bank_batch",
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor=AuditActor(staff.kind, staff.id),
            reason=reason.strip(),
        ),
        clock,
    )
