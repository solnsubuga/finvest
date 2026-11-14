"""A paid payout that came back, taken back by two people, and sent again by the client.

A payout the provider or bank confirmed can still come back: a bank account closed after the
transfer left, a mobile money reversal. The statement shows the money coming in again.

1. One member of staff proposes the return, naming that statement line, with a reason.
2. A different one approves it. In the same transaction the ledger posts `payout.returned`
   (debit the pot, credit the client's "payout pending" account for the payout's source), and
   the client is told `payout_returned`, naming where it was sent, never the amount or number.
   They may instead decline it, or the proposer may withdraw it.
3. The client chooses an approved account and sends it again (`resend`). It is a new payout,
   funded from the same handover, under the request key `return:<return id>`, so asking twice is
   the same payout, and a returned payout is sent again at most once by the client. If that one
   fails, its money is parked for staff like any failed payout.

The payout itself stays paid: a finished payout never changes. Ledger entries are never undone
by deleting them; the return is its own entry. Migration 0044 holds each rule in the database.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.ledger import service as ledger
from app.modules.notifications import contract as notifications
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain import posting
from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.payouts import PayoutStatus
from app.modules.payouts.domain.sending import bank_label, destination_label
from app.modules.payouts.payouts import PayoutView
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id

PROPOSE_OPERATION = "payouts.propose_return"
APPROVE_OPERATION = "payouts.approve_return"


class ReturnStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class ReturnRefused(Exception):
    """A step the rules do not allow. `code` is safe to show; `status` is the HTTP meaning."""

    def __init__(
        self, code: str, status: Literal["conflict", "forbidden", "validation_failed"] = "conflict"
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class ReturnView:
    id: str
    payout_id: str
    client_id: str
    statement_line_id: str
    reason: str
    status: ReturnStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    ledger_entry_id: str | None

    @property
    def resend_key(self) -> str:
        """The request key of the payout that sends this money again."""
        return f"return:{self.id}"


_COLUMNS = (
    "id, payout_id, client_id, statement_line_id, reason, status, proposed_by, proposed_at, "
    "decided_by, decided_at, decision_reason, ledger_entry_id"
)
_INSERT = text(
    "INSERT INTO payout_returns (id, payout_id, client_id, statement_line_id, reason, status, "
    "proposed_by, proposed_at) VALUES (:id, :payout_id, :client_id, :statement_line_id, "
    ":reason, 'proposed', :proposed_by, :now)"
)
_GET = text(f"SELECT {_COLUMNS} FROM payout_returns WHERE id = :id")  # noqa: S608
_LOCK = text(f"SELECT {_COLUMNS} FROM payout_returns WHERE id = :id FOR UPDATE")  # noqa: S608
_LIVE_FOR = text(
    f"SELECT {_COLUMNS} FROM payout_returns "  # noqa: S608
    "WHERE payout_id = :payout_id AND status IN ('proposed', 'approved')"
)
_LINE_IN_USE = text(
    "SELECT 1 FROM payout_returns WHERE statement_line_id = :line_id "
    "AND status IN ('proposed', 'approved')"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM payout_returns WHERE status = 'proposed' "  # noqa: S608
    "ORDER BY proposed_at, id LIMIT :limit"
)
_APPROVED_FOR = text(
    f"SELECT {_COLUMNS} FROM payout_returns "  # noqa: S608
    "WHERE payout_id = ANY(:payout_ids) AND status = 'approved'"
)
_DECIDE = text(
    "UPDATE payout_returns SET status = :status, decided_by = :decided_by, decided_at = :now, "
    "decision_reason = :reason, ledger_entry_id = :ledger_entry_id WHERE id = :id"
)
_EVIDENCE = text(
    "SELECT public.finvest_payout_return_evidence(ROW(CAST(:id AS text), "
    "CAST(:payout_id AS text), CAST(:client_id AS text), CAST(:statement_line_id AS text), "
    "CAST(:reason AS text), 'proposed', CAST(:proposed_by AS text), "
    "CAST(:now AS timestamptz), NULL, NULL, NULL, NULL)::public.payout_returns) AS shown"
)


async def propose(
    session: AsyncSession,
    staff: Actor,
    payout_id: str,
    *,
    statement_line_id: str,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> ReturnView:
    """The first person says a paid payout came back, naming the statement line that shows it."""
    _check(staff, reason)

    async def command() -> Receipt:
        payout = await payouts.get_payout(session, payout_id)
        if payout.status is not PayoutStatus.PAID:
            raise ReturnRefused("payout_not_paid")
        if (await session.execute(_LIVE_FOR, {"payout_id": payout_id})).first() is not None:
            raise ReturnRefused("return_already_recorded")
        if (await session.execute(_LINE_IN_USE, {"line_id": statement_line_id})).first():
            raise ReturnRefused("statement_line_in_use")
        return_id = push_id()
        values = {
            "id": return_id,
            "payout_id": payout_id,
            "client_id": payout.client_id,
            "statement_line_id": statement_line_id,
            "reason": reason.strip(),
            "proposed_by": staff.id,
            "now": clock.now(),
        }
        # The database checks the same evidence on insert; asking first names the refusal.
        if not (await session.execute(_EVIDENCE, values)).scalar_one():
            raise ReturnRefused("statement_line_not_this_payout")
        await session.execute(_INSERT, values)
        await _audit(session, staff, "payouts.return_proposed", payout_id, reason, clock)
        return Receipt(201, {"return_id": return_id})

    done = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={
            "payout_id": payout_id,
            "statement_line_id": statement_line_id,
            "reason": reason.strip(),
        },
        now=clock.now(),
        command=command,
    )
    return await get_return(session, str(done.receipt.response["return_id"]))


async def approve(
    session: AsyncSession,
    staff: Actor,
    return_id: str,
    *,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> ReturnView:
    """A different person approves. The money is owed to the client again, and they are told."""
    _check(staff, reason)

    async def command() -> Receipt:
        returned = await _open(session, return_id)
        if returned.proposed_by == staff.id:
            raise ReturnRefused("proposed_by_you", "forbidden")
        payout = await payouts.get_payout(session, returned.payout_id)
        destination = await destinations.get_destination(session, payout.destination_id)
        currency = payout.amount.currency
        pot = await ledger.open_account(
            session, posting.paid_from_account(payout.kind, destination.network, currency), clock
        )
        posted = await ledger.post_entry(
            session,
            posting.returned(
                payout.terms,
                business_date(clock.now()),
                pot_account_id=pot.id,
                funding_account_id=payout.source_account_id,
            ),
            clock,
        )
        await session.execute(
            _DECIDE,
            {
                "id": returned.id,
                "status": ReturnStatus.APPROVED.value,
                "decided_by": staff.id,
                "now": clock.now(),
                "reason": reason.strip(),
                "ledger_entry_id": posted.id,
            },
        )
        await _tell_client(session, payout, clock)
        await _audit(session, staff, "payouts.return_approved", payout.id, reason, clock)
        return Receipt(200, {"return_id": returned.id})

    await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"return_id": return_id, "reason": reason.strip()},
        now=clock.now(),
        command=command,
    )
    return await get_return(session, return_id)


async def decline(
    session: AsyncSession, staff: Actor, return_id: str, *, reason: str, clock: Clock
) -> ReturnView:
    """Someone other than the proposer refuses it. Nothing is posted."""
    _check(staff, reason)
    returned = await _open(session, return_id)
    if returned.proposed_by == staff.id:
        raise ReturnRefused("proposed_by_you", "forbidden")
    await _decide(session, returned.id, ReturnStatus.DECLINED, staff, reason, clock)
    await _audit(session, staff, "payouts.return_declined", returned.payout_id, reason, clock)
    return await get_return(session, return_id)


async def withdraw(
    session: AsyncSession, staff: Actor, return_id: str, *, reason: str, clock: Clock
) -> ReturnView:
    """The proposer takes it back. Nothing is posted."""
    _check(staff, reason)
    returned = await _open(session, return_id)
    if returned.proposed_by != staff.id:
        raise ReturnRefused("not_proposed_by_you", "forbidden")
    await _decide(session, returned.id, ReturnStatus.WITHDRAWN, staff, reason, clock)
    await _audit(session, staff, "payouts.return_withdrawn", returned.payout_id, reason, clock)
    return await get_return(session, return_id)


async def resend(
    session: AsyncSession,
    client: Actor,
    payout_id: str,
    *,
    destination_id: str,
    clock: Clock,
) -> PayoutView:
    """The client sends a returned payout again, to an approved account of theirs.

    It is a new payout for the same amount, from the same handover. Its request key is the
    return's, so asking again after a lost answer gives the same payout, and asking for another
    account once one was chosen is refused (`payouts.PayoutRefused`).
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    payout = await payouts.get_payout(session, payout_id)
    if payout.client_id != client.id:
        raise ApplicationError("not_found")
    returned = (await approved_for(session, [payout_id])).get(payout_id)
    if returned is None:
        raise ReturnRefused("payout_not_returned")
    return await payouts.create_payout(
        session,
        client,
        client_id=client.id,
        destination_id=destination_id,
        amount=payout.amount,
        source_reference=payout.source_reference,
        request_key=returned.resend_key,
        clock=clock,
    )


async def get_return(session: AsyncSession, return_id: str) -> ReturnView:
    row = (await session.execute(_GET, {"id": return_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def waiting(session: AsyncSession, *, limit: int = 50) -> list[ReturnView]:
    """Returns waiting for a second person, oldest first. Empty outside a staff transaction."""
    rows = await session.execute(_WAITING, {"limit": max(1, min(limit, 100))})
    return [_view(row) for row in rows.all()]


async def approved_for(session: AsyncSession, payout_ids: list[str]) -> dict[str, ReturnView]:
    """The approved return of each of these payouts that came back. A client sees only theirs."""
    if not payout_ids:
        return {}
    rows = await session.execute(_APPROVED_FOR, {"payout_ids": payout_ids})
    return {row.payout_id: _view(row) for row in rows.all()}


def _check(staff: Actor, reason: str) -> None:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    if not reason.strip():
        raise ApplicationError("validation_failed")


async def _open(session: AsyncSession, return_id: str) -> ReturnView:
    row = (await session.execute(_LOCK, {"id": return_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    returned = _view(row)
    if returned.status is not ReturnStatus.PROPOSED:
        raise ReturnRefused("return_already_decided")
    return returned


async def _decide(
    session: AsyncSession,
    return_id: str,
    status: ReturnStatus,
    staff: Actor,
    reason: str,
    clock: Clock,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": return_id,
            "status": status.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
            "ledger_entry_id": None,
        },
    )


async def _tell_client(session: AsyncSession, payout: PayoutView, clock: Clock) -> None:
    """In the same transaction as the approval, so the client is told once and only if it holds.
    Never the amount, and never the number."""
    destination = await destinations.get_destination(session, payout.destination_id)
    await notifications.notify(
        session,
        event_name="payout_returned",
        client_id=payout.client_id,
        idempotency_key=f"{payout.id}:returned",
        variables={
            "destination": bank_label(destination.bank_code)
            if destination.kind is DestinationKind.BANK
            else destination_label(destination.network or ""),
        },
        link=notifications.Link("payout", payout.id),
        now=clock.now(),
    )


async def _audit(
    session: AsyncSession, staff: Actor, action: str, payout_id: str, reason: str, clock: Clock
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval",
            action=action,
            resource_type="payout",
            resource_id=payout_id,
            actor=AuditActor(staff.kind, staff.id),
            reason=reason.strip(),
        ),
        clock,
    )


def _view(row: Row[Any]) -> ReturnView:
    return ReturnView(
        id=row.id,
        payout_id=row.payout_id,
        client_id=row.client_id,
        statement_line_id=row.statement_line_id,
        reason=row.reason,
        status=ReturnStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        ledger_entry_id=row.ledger_entry_id,
    )
