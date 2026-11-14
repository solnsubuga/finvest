"""Commands and queries for payout destinations. The rules are in `domain/destinations.py`.

Every function takes the caller's open transaction, so a step and its audit event commit or
roll back together. The caller must open that transaction for the right actor: the database
refuses a client step in a staff transaction and the other way round (migration 0030).

The SMS code is the caller's job, because the identity module owns it: it is spent
(`spend_payout_account_code`) in the same transaction as `confirm_code`. The name on the
client's ID is read here from the identity checks module, never taken from the caller.

Approving an account takes two members of staff. One records a check with `check`; a
different one approves it with `approve`. One person may refuse with `reject`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.kyc import service as kyc
from app.modules.payouts.domain import destinations as rules
from app.modules.payouts.domain.banks import find_bank
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
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

MAX_QUEUE_PAGE = 100
# Accounts one client may hold while still waiting for their code. Each can be texted, so this
# bounds the texts a client can cause, beside the limits on the codes themselves.
MAX_AWAITING_CODE = 5


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
    # The check of the current proof: what was read on it, who recorded it and when, and who
    # approved it. Staff only: the client routes never return these.
    proof_name: str | None
    statement_date: date | None
    checked_by: str | None
    checked_at: datetime | None
    approved_by: str | None
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
            checked_by=self.checked_by,
        )


_COLUMNS = (
    "id, client_id, kind, network, bank_code, account_number, holder_name, status, proof_kind, "
    "proof_document_id, proof_name, name_match, statement_date, rejection_reason, checked_by, "
    "checked_at, approved_by, approved_at, first_paid_at, created_at"
)
_INSERT = text(
    "INSERT INTO payout_destinations (id, client_id, kind, network, bank_code, account_number, "
    "holder_name, status, created_at, updated_at) VALUES (:id, :client_id, :kind, :network, "
    ":bank_code, :account_number, :holder_name, 'awaiting_code', :now, :now)"
)
_LOCK_CLIENT = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_AWAITING_CODE = text(
    "SELECT count(*) FROM payout_destinations "
    "WHERE client_id = :client_id AND status = 'awaiting_code'"
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
_CHECKED = text(
    f"SELECT {_COLUMNS} FROM payout_destinations "  # noqa: S608 - constant column list
    "WHERE status = 'in_review' AND checked_by IS NOT NULL AND approved_by IS NULL "
    "ORDER BY checked_at, id LIMIT :limit"
)
_UPDATE = text(
    "UPDATE payout_destinations SET status = :status, proof_kind = :proof_kind, "
    "proof_document_id = :proof_document_id, proof_name = :proof_name, name_match = :name_match, "
    "statement_date = :statement_date, checked_by = :checked_by, checked_at = :checked_at, "
    "approved_by = :approved_by, approved_at = :approved_at, "
    "rejection_reason = :rejection_reason, updated_at = :now WHERE id = :id"
)
_KEEP = text(
    "SELECT proof_kind, proof_document_id, proof_name, name_match, statement_date, checked_by, "
    "checked_at, approved_by FROM payout_destinations WHERE id = :id"
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
    if isinstance(account, BankAccount) and find_bank(account.bank_code) is None:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["body", "bank_code"], code="bank_unknown")],
        )
    # One client's adds wait on each other, so two at once cannot both pass the cap.
    await session.execute(_LOCK_CLIENT, {"key": f"payouts.destinations:{client.id}"})
    waiting = await session.scalar(_AWAITING_CODE, {"client_id": client.id})
    if (waiting or 0) >= MAX_AWAITING_CODE:
        raise ApplicationError(
            "conflict",
            fields=[FieldProblem(field_path=["body"], code="too_many_unconfirmed_accounts")],
        )
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
    await _save(
        session,
        view,
        after,
        clock,
        proof_kind=proof,
        proof_document_id=document_id,
        # New proof is checked afresh (migration 0035).
        proof_name=None,
        name_match=None,
        statement_date=None,
        checked_by=None,
        checked_at=None,
    )
    await _audit(session, client, "payouts.destination_proof_submitted", destination_id, clock)
    return await get_destination(session, destination_id)


async def check(
    session: AsyncSession,
    staff: Actor,
    destination_id: str,
    *,
    proof_name: str | None,
    statement_date: date | None,
    reason: str,
    clock: Clock,
) -> DestinationView:
    """Record the first member of staff's check of the proof. The account stays in review.

    `proof_name` is the name they read on the proof. It is compared here with the name on the
    client's verified ID, read from the identity checks module, never from the request. A
    different name is refused whoever asks; the account then waits for a refusal. A check made
    again replaces the one before it.

    A mobile money number is checked with the name its network gave, as the worker recorded it.
    Nobody types it: `proof_name` must be left out for one.
    """
    if staff.id is None:
        raise ApplicationError("forbidden")
    view = await _locked(session, destination_id)
    if view.proof_kind is None:
        raise rules.DestinationError("destination_not_in_review")
    id_name = await kyc.verified_name(session, view.client_id)
    if id_name is None:
        raise rules.DestinationError("client_not_verified")
    if view.proof_kind is ProofKind.NETWORK_REGISTERED_NAME:
        if proof_name is not None:
            raise rules.DestinationError("proof_name_from_network")
        if view.proof_name is None:
            raise rules.DestinationError("destination_not_in_review")
        read = view.proof_name
    elif proof_name is None:
        raise rules.DestinationError("proof_name_required")
    else:
        read = proof_name.strip()[:120]
    match = rules.compare_names(id_name, read)
    now = clock.now()
    after = rules.record_check(
        view.state, ProofCheck(view.proof_kind, match, statement_date), staff.id, now
    )
    await _save(
        session,
        view,
        after,
        clock,
        proof_name=read,
        name_match=match,
        statement_date=statement_date,
        checked_by=staff.id,
        checked_at=now,
    )
    await _audit(
        session,
        staff,
        "payouts.destination_checked",
        destination_id,
        clock,
        category="approval",
        reason=reason,
        details={"name_match": match.value, "proof_kind": view.proof_kind.value},
    )
    return await get_destination(session, destination_id)


async def network_name_matches(
    session: AsyncSession,
    actor: Actor,
    destination_id: str,
    lookup_id: str,
    registered_name: str,
    match: NameMatch,
    clock: Clock,
) -> DestinationView:
    """The name the network holds for a number matches the ID: send it to staff. Worker only.

    The caller holds the row lock and has checked the number still waits for its name.
    """
    view = await _locked(session, destination_id)
    after = rules.network_name_matches(view.state)
    await _save(
        session,
        view,
        after,
        clock,
        proof_kind=ProofKind.NETWORK_REGISTERED_NAME,
        proof_document_id=lookup_id,
        proof_name=registered_name,
        name_match=match,
    )
    await _audit(
        session,
        actor,
        "payouts.destination_network_name_matched",
        destination_id,
        clock,
        details={"name_match": match.value},
    )
    return await get_destination(session, destination_id)


async def refuse_by_network(
    session: AsyncSession,
    actor: Actor,
    destination_id: str,
    lookup_id: str,
    registered_name: str | None,
    rejection: RejectionReason,
    clock: Clock,
) -> DestinationView:
    """The network's answer refuses a number: a different name, or none. Final. Worker only."""
    view = await _locked(session, destination_id)
    after = rules.refuse_by_network(view.state, rejection)
    different = rejection is RejectionReason.NAME_DIFFERENT
    await _save(
        session,
        view,
        after,
        clock,
        proof_kind=ProofKind.NETWORK_REGISTERED_NAME,
        proof_document_id=lookup_id,
        proof_name=registered_name if different else None,
        name_match=NameMatch.DIFFERENT if different else None,
    )
    await _audit(
        session,
        actor,
        "payouts.destination_refused_by_network",
        destination_id,
        clock,
        category="approval",
        reason=(
            "The name the network holds for the number is not the name on the ID"
            if different
            else "The network holds no name for the number"
        ),
        details={"rejection_reason": rejection.value},
    )
    return await get_destination(session, destination_id)


async def approve(
    session: AsyncSession, staff: Actor, destination_id: str, *, reason: str, clock: Clock
) -> DestinationView:
    """A second member of staff approves the recorded check, and the 24-hour wait starts.

    Whoever recorded the check cannot approve it. The check is approved as it was recorded.
    """
    if staff.id is None:
        raise ApplicationError("forbidden")
    view = await _locked(session, destination_id)
    if view.proof_kind is None or view.name_match is None:
        raise rules.DestinationError(
            "destination_not_in_review"
            if view.status is not DestinationStatus.IN_REVIEW
            else "destination_not_checked"
        )
    recorded = ProofCheck(view.proof_kind, view.name_match, view.statement_date)
    now = clock.now()
    after = rules.approve(view.state, recorded, staff.id, now)
    try:
        async with session.begin_nested():
            await _save(session, view, after, clock, approved_by=staff.id)
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
        details={
            "name_match": view.name_match.value,
            "proof_kind": view.proof_kind.value,
            "checked_by": view.checked_by,
        },
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
    """Refuse the proof. One member of staff is enough, whoever checked it."""
    if staff.id is None:
        raise ApplicationError("forbidden")
    view = await _locked(session, destination_id)
    after = rules.reject(view.state, rejection, staff.id)
    await _save(session, view, after, clock, checked_by=staff.id)
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


_SEND_TO = text(
    "SELECT network, account_number FROM payout_destinations "
    "WHERE id = :id AND kind = 'mobile_money'"
)


async def account_number(session: AsyncSession, destination_id: str) -> str:
    """The whole account number, for staff to compare with the proof. Staff transactions only:
    the client routes show the masked number. Opening it is recorded with `record_opened`."""
    number = await session.scalar(
        text("SELECT account_number FROM payout_destinations WHERE id = :id"),
        {"id": destination_id},
    )
    if number is None:
        raise ApplicationError("not_found")
    return str(number)


async def record_opened(
    session: AsyncSession, staff: Actor, destination_id: str, clock: Clock
) -> None:
    """Write to the audit trail that a member of staff opened an account to check it. Its proof
    and number name the client, so each look is recorded."""
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    await _audit(
        session, staff, "payouts.destination_opened", destination_id, clock, category="access"
    )


async def mobile_money_number(session: AsyncSession, destination_id: str) -> tuple[str, str]:
    """The network and full number of a mobile money destination: for the worker sending a
    payout, and for texting the code that proves the client holds it.

    The number is personal data: it goes to the provider or the SMS and nowhere else, never
    into a log or a job payload.
    """
    row = (await session.execute(_SEND_TO, {"id": destination_id})).one_or_none()
    if row is None or row.network is None:
        raise ApplicationError("not_found")
    return row.network, row.account_number


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


async def checks_awaiting_approval(
    session: AsyncSession, *, limit: int = 50
) -> list[DestinationView]:
    """Checks one member of staff recorded, waiting for a different one to approve, oldest
    first. Empty outside a staff transaction."""
    rows = await session.execute(_CHECKED, {"limit": max(1, min(limit, MAX_QUEUE_PAGE))})
    return [_view(row) for row in rows.all()]


async def locked(session: AsyncSession, destination_id: str) -> DestinationView:
    """The destination, locked for the rest of the transaction. `not_found` if it is hidden."""
    return await _locked(session, destination_id)


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
        kind,
        DestinationStatus(row.status),
        row.approved_at,
        row.first_paid_at,
        rejection,
        row.checked_by,
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
        proof_name=row.proof_name,
        statement_date=row.statement_date,
        checked_by=row.checked_by,
        checked_at=row.checked_at,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        payable_from=rules.payable_from(state),
        first_paid_at=row.first_paid_at,
        created_at=row.created_at,
    )
