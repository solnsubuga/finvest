"""Commands and queries for payout destinations. The rules are in `domain/destinations.py`.

Every function takes the caller's open transaction, so a step and its audit event commit or
roll back together. The caller must open that transaction for the right actor: the database
refuses a client step in a staff transaction and the other way round (migration 0030).

Two things are the caller's job, because other modules own them. The SMS code is checked by
the identity module before `confirm_code` is called. The name on the client's ID is read from
the clients module and passed to `approve`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.payouts.domain import destinations as rules
from app.modules.payouts.domain.destinations import (
    BankAccount,
    Destination,
    DestinationKind,
    DestinationStatus,
    MobileMoneyNumber,
    NameMatch,
    ProofCheck,
    ProofKind,
    RejectionReason,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id

MAX_QUEUE_PAGE = 100


@dataclass(frozen=True, slots=True)
class DestinationView:
    id: str
    client_id: str
    kind: DestinationKind
    network: str | None
    bank_code: str | None
    masked_account: str
    holder_name: str
    status: DestinationStatus
    proof_kind: ProofKind | None
    proof_document_id: str | None
    name_match: NameMatch | None
    rejection_reason: RejectionReason | None
    approved_at: datetime | None
    payable_from: datetime | None
    first_paid_at: datetime | None
    created_at: datetime

    @property
    def state(self) -> Destination:
        return Destination(
            kind=self.kind,
            status=self.status,
            approved_at=self.approved_at,
            first_paid_at=self.first_paid_at,
            rejection_reason=self.rejection_reason,
        )


_COLUMNS = (
    "id, client_id, kind, network, bank_code, account_number, holder_name, status, proof_kind, "
    "proof_document_id, name_match, rejection_reason, approved_at, first_paid_at, created_at"
)
_INSERT = text(
    "INSERT INTO payout_destinations (id, client_id, kind, network, bank_code, account_number, "
    "holder_name, status, created_at, updated_at) VALUES (:id, :client_id, :kind, :network, "
    ":bank_code, :account_number, :holder_name, 'awaiting_code', :now, :now)"
)
_LOCK = text(f"SELECT {_COLUMNS} FROM payout_destinations WHERE id = :id FOR UPDATE")  # noqa: S608
_GET = text(f"SELECT {_COLUMNS} FROM payout_destinations WHERE id = :id")  # noqa: S608
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} FROM payout_destinations "  # noqa: S608 - constant column list
    "WHERE client_id = :client_id AND status <> 'removed' ORDER BY created_at DESC, id DESC"
)
_QUEUE = text(
    f"SELECT {_COLUMNS} FROM payout_destinations "  # noqa: S608 - constant column list
    "WHERE status = 'in_review' ORDER BY updated_at, id LIMIT :limit"
)
_UPDATE = text(
    "UPDATE payout_destinations SET status = :status, proof_kind = :proof_kind, "
    "proof_document_id = :proof_document_id, proof_name = :proof_name, name_match = :name_match, "
    "statement_date = :statement_date, checked_by = :checked_by, approved_at = :approved_at, "
    "rejection_reason = :rejection_reason, updated_at = :now WHERE id = :id"
)
_KEEP = text(
    "SELECT proof_kind, proof_document_id, proof_name, name_match, statement_date, checked_by "
    "FROM payout_destinations WHERE id = :id"
)
_FIRST_PAID = text(
    "UPDATE payout_destinations SET first_paid_at = :paid_at, updated_at = :paid_at "
    "WHERE id = :id AND first_paid_at IS NULL"
)


async def add_destination(
    session: AsyncSession,
    client: Actor,
    account: MobileMoneyNumber | BankAccount,
    holder_name: str,
    clock: Clock,
) -> DestinationView:
    """Save a new destination for the signed-in client. It starts by awaiting the SMS code."""
    name = holder_name.strip()
    if not name or len(name) > 120:
        raise ApplicationError("validation_failed")
    is_mobile = isinstance(account, MobileMoneyNumber)
    destination_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": destination_id,
                    "client_id": client.id,
                    "kind": DestinationKind.MOBILE_MONEY if is_mobile else DestinationKind.BANK,
                    "network": account.network if isinstance(account, MobileMoneyNumber) else None,
                    "bank_code": account.bank_code if isinstance(account, BankAccount) else None,
                    "account_number": account.msisdn
                    if isinstance(account, MobileMoneyNumber)
                    else account.account_number,
                    "holder_name": name,
                    "now": clock.now(),
                },
            )
    except IntegrityError:
        # The client already has this account saved and not removed.
        raise ApplicationError("conflict") from None
    await _audit(session, client, "payouts.destination_added", destination_id, clock)
    return await get_destination(session, destination_id)


async def confirm_code(
    session: AsyncSession, client: Actor, destination_id: str, clock: Clock
) -> DestinationView:
    """Move past the SMS step. Call only after the identity module accepted the code."""
    view = await _locked(session, destination_id)
    await _save(session, view, rules.confirm_code(view.state), clock)
    await _audit(session, client, "payouts.destination_code_confirmed", destination_id, clock)
    return await get_destination(session, destination_id)


async def submit_proof(
    session: AsyncSession,
    client: Actor,
    destination_id: str,
    proof: ProofKind,
    document_id: str,
    clock: Clock,
) -> DestinationView:
    """Attach proof of ownership and send the destination to the staff queue."""
    view = await _locked(session, destination_id)
    after = rules.submit_proof(view.state, proof)
    await _save(session, view, after, clock, proof_kind=proof, proof_document_id=document_id)
    await _audit(session, client, "payouts.destination_proof_submitted", destination_id, clock)
    return await get_destination(session, destination_id)


async def approve(
    session: AsyncSession,
    staff: Actor,
    destination_id: str,
    *,
    id_name: str,
    proof_name: str,
    statement_date: date | None,
    reason: str,
    clock: Clock,
) -> DestinationView:
    """Record a staff check that passed, and start the 24-hour safety wait.

    `proof_name` is the name the staff member read on the proof. The comparison with the ID
    name is made here, on the server, and is never taken from the request. A different name is
    refused whoever asks.
    """
    view = await _locked(session, destination_id)
    if view.proof_kind is None:
        raise rules.DestinationError("destination_not_in_review")
    match = rules.compare_names(id_name, proof_name)
    check = ProofCheck(view.proof_kind, match, statement_date)
    now = clock.now()
    after = rules.approve(view.state, check, now)
    try:
        async with session.begin_nested():
            await _save(
                session,
                view,
                after,
                clock,
                proof_name=proof_name.strip()[:120],
                name_match=match,
                statement_date=statement_date,
                checked_by=staff.id,
            )
    except IntegrityError:
        # The same account is already approved for another client. One of them is not its
        # owner, so this one is not approved and a person must look at both.
        raise rules.DestinationError("account_in_use") from None
    await _audit(
        session,
        staff,
        "payouts.destination_approved",
        destination_id,
        clock,
        category="approval",
        reason=reason,
        details={"name_match": match.value, "proof_kind": view.proof_kind.value},
    )
    return await get_destination(session, destination_id)


async def reject(
    session: AsyncSession,
    staff: Actor,
    destination_id: str,
    rejection: RejectionReason,
    reason: str,
    clock: Clock,
) -> DestinationView:
    view = await _locked(session, destination_id)
    await _save(session, view, rules.reject(view.state, rejection), clock, checked_by=staff.id)
    await _audit(
        session,
        staff,
        "payouts.destination_rejected",
        destination_id,
        clock,
        category="approval",
        reason=reason,
        details={"rejection_reason": rejection.value},
    )
    return await get_destination(session, destination_id)


async def remove(
    session: AsyncSession,
    actor: Actor,
    destination_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    """Take a destination out of use for good.

    The client may remove their own. A staff member may remove any, and must say why: it is
    recorded as an operator control in the audit trail.
    """
    is_staff = actor.kind == "staff"
    if is_staff and not (reason or "").strip():
        raise ApplicationError("validation_failed")
    view = await _locked(session, destination_id)
    await _save(session, view, rules.remove(view.state), clock)
    await _audit(
        session,
        actor,
        "payouts.destination_removed",
        destination_id,
        clock,
        category="operator_control" if is_staff else "change",
        reason=reason if is_staff else None,
    )


async def record_first_payout(
    session: AsyncSession, destination_id: str, paid_at: datetime
) -> None:
    """Note when money first arrived. Worker only. A later payout changes nothing."""
    await session.execute(_FIRST_PAID, {"id": destination_id, "paid_at": paid_at})


async def get_destination(session: AsyncSession, destination_id: str) -> DestinationView:
    row = (await session.execute(_GET, {"id": destination_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def usable_destination(
    session: AsyncSession, client_id: str, destination_id: str
) -> DestinationView:
    """The destination, if it is this client's and approved. Otherwise `not_found`.

    For a module that stores a destination as a standing choice, such as where an investment
    pays out at maturity. Whether it can be paid right now is checked when the payout is made.
    """
    view = await get_destination(session, destination_id)
    if view.client_id != client_id or view.status is not DestinationStatus.APPROVED:
        raise ApplicationError("not_found")
    return view


async def destinations_for_client(session: AsyncSession, client_id: str) -> list[DestinationView]:
    rows = await session.execute(_FOR_CLIENT, {"client_id": client_id})
    return [_view(row) for row in rows.all()]


async def review_queue(session: AsyncSession, *, limit: int = 50) -> list[DestinationView]:
    """Destinations waiting for a staff check, oldest first. Empty outside a staff transaction."""
    rows = await session.execute(_QUEUE, {"limit": max(1, min(limit, MAX_QUEUE_PAGE))})
    return [_view(row) for row in rows.all()]


async def _locked(session: AsyncSession, destination_id: str) -> DestinationView:
    row = (await session.execute(_LOCK, {"id": destination_id})).one_or_none()
    if row is None:
        # Missing, or another client's: a row policy hides it either way.
        raise ApplicationError("not_found")
    return _view(row)


async def _save(
    session: AsyncSession, before: DestinationView, after: Destination, clock: Clock, **changes: Any
) -> None:
    kept = (await session.execute(_KEEP, {"id": before.id})).one()._asdict()
    await session.execute(
        _UPDATE,
        {
            **kept,
            **changes,
            "id": before.id,
            "status": after.status,
            "approved_at": after.approved_at,
            "rejection_reason": after.rejection_reason,
            "now": clock.now(),
        },
    )


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    destination_id: str,
    clock: Clock,
    *,
    category: str = "change",
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,  # type: ignore[arg-type]
            action=action,
            resource_type="payout_destination",
            resource_id=destination_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details or {},
        ),
        clock,
    )


def _view(row: Row[Any]) -> DestinationView:
    kind = DestinationKind(row.kind)
    masked = (
        MobileMoneyNumber(row.account_number).masked
        if kind is DestinationKind.MOBILE_MONEY
        else BankAccount(row.bank_code, row.account_number).masked
    )
    rejection = RejectionReason(row.rejection_reason) if row.rejection_reason else None
    state = Destination(
        kind, DestinationStatus(row.status), row.approved_at, row.first_paid_at, rejection
    )
    return DestinationView(
        id=row.id,
        client_id=row.client_id,
        kind=kind,
        network=row.network,
        bank_code=row.bank_code,
        masked_account=masked,
        holder_name=row.holder_name,
        status=state.status,
        proof_kind=ProofKind(row.proof_kind) if row.proof_kind else None,
        proof_document_id=row.proof_document_id,
        name_match=NameMatch(row.name_match) if row.name_match else None,
        rejection_reason=rejection,
        approved_at=row.approved_at,
        payable_from=rules.payable_from(state),
        first_paid_at=row.first_paid_at,
        created_at=row.created_at,
    )
