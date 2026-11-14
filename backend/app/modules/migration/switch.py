"""The switch from Finvest 1.0, proposed by one member of staff and approved by another.

The proposal names the trial that cleared every check, and comes with the same two sheets. They
are kept sealed: the approval reads them again, and so does the worker that carries the
investments over. Their fingerprints must still be the trial's, so nobody can switch with a file
that was never checked.

An approval creates the account of every client in the approver's transaction, with no password,
so nobody can sign in as them until they claim it. Either every client gets an account or none
does: a phone number that already has an account refuses the whole approval and names its row.

The caller opens `Database.actor_transaction(staff)` for the member of staff it passes here.
Migration 0074 holds the rest: a decision is by someone other than the proposer, at most one
switch is approved, and a client is carried over only in the transaction that approved it.
"""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.infrastructure.sealing import Sealer, associated_data
from app.modules.identity.service import create_unclaimed_client
from app.modules.investments import service as investments
from app.modules.migration import service
from app.modules.migration.domain.finance import MAX_REASON_LENGTH, check_investments
from app.modules.migration.domain.trial import TrialReport, run_trial
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

SwitchState = Literal["proposed", "approved", "rejected"]
# The worker's job that carries every client of an approved switch over.
CARRY_OVER = "migration.carry_over"
# The most rows an answer names. The error envelope holds at most 100 field problems.
MAX_NAMED_ROWS = 100

_FILES_TABLE = "migration_switch_files"
# One proposal or decision at a time, so two people cannot both propose and cross-approve.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended('migration.switch', 74))")
_COLUMNS = (
    "s.id, s.trial_id, s.as_of, s.clients_sha256, s.investments_sha256, s.proposed_by, "
    "s.proposed_at, d.outcome, d.decided_by, d.reason AS decision_reason, "
    "(SELECT count(*) FROM migration_carried_clients AS c WHERE c.switch_id = s.id) AS carried"
)
_LATEST = text(
    f"SELECT {_COLUMNS} FROM migration_switches AS s "  # noqa: S608
    "LEFT JOIN migration_switch_decisions AS d ON d.switch_id = s.id "
    "ORDER BY s.proposed_at DESC, s.id DESC LIMIT 1"
)
_BY_ID = text(
    f"SELECT {_COLUMNS} FROM migration_switches AS s "  # noqa: S608
    "LEFT JOIN migration_switch_decisions AS d ON d.switch_id = s.id WHERE s.id = :id"
)
_APPROVED = text("SELECT 1 FROM migration_switch_decisions WHERE outcome = 'approved'")
_TRIAL_FILES = text(
    "SELECT clients_sha256, investments_sha256 FROM migration_trials WHERE id = :id"
)
_INSERT = text(
    "INSERT INTO migration_switches (id, trial_id, as_of, clients_sha256, investments_sha256, "
    "proposed_by, proposed_at) VALUES (:id, :trial_id, :as_of, :clients_sha256, "
    ":investments_sha256, :proposed_by, :proposed_at)"
)
_INSERT_FILES = text(
    "INSERT INTO migration_switch_files (switch_id, clients_csv, investments_csv) "
    "VALUES (:switch_id, :clients_csv, :investments_csv)"
)
_FILES = text(
    "SELECT clients_csv, investments_csv FROM migration_switch_files WHERE switch_id = :id"
)
_INSERT_DECISION = text(
    "INSERT INTO migration_switch_decisions (switch_id, outcome, reason, decided_by, decided_at) "
    "VALUES (:switch_id, :outcome, :reason, :decided_by, :decided_at)"
)
_INSERT_CARRIED = text(
    "INSERT INTO migration_carried_clients (legacy_client_ref, switch_id, client_id, carried_by, "
    "carried_at) VALUES (:ref, :switch_id, :client_id, :carried_by, :carried_at)"
)


@dataclass(frozen=True, slots=True)
class SwitchView:
    id: str
    trial_id: str
    as_of: date
    proposed_by: str
    proposed_at: datetime
    state: SwitchState
    decided_by: str | None
    # Why it was rejected. A rejection always gives one; an approval may.
    decision_reason: str | None
    # How many accounts the approval created. Zero until it is approved.
    clients_carried: int


@dataclass(frozen=True, slots=True)
class Sheets:
    """The two sheets of a switch, opened. They hold personal data: never log or return them."""

    clients_csv: str
    investments_csv: str


async def latest_switch(session: AsyncSession) -> SwitchView | None:
    """The newest proposal and what was decided, for the migration screen."""
    row = (await session.execute(_LATEST)).one_or_none()
    return None if row is None else _view(row)


async def propose_switch(
    session: AsyncSession,
    sealer: Sealer,
    staff: Actor,
    clock: Clock,
    *,
    clients_csv: str,
    investments_csv: str,
) -> SwitchView:
    """Ask a second person to approve the switch with these two sheets. Nothing is created yet.

    Refused with `conflict` while anything blocks the switch, a switch awaits a decision or one
    was approved. The sheets must be the latest trial's, byte for byte, and every client in them
    must have a phone number, since the phone is how a client claims the account.
    """
    _staff_only(staff)
    if max(len(clients_csv.encode()), len(investments_csv.encode())) > service.MAX_SHEET_BYTES:
        raise ApplicationError("payload_too_large")
    await session.execute(_LOCK)
    latest = await latest_switch(session)
    if latest is not None and latest.state != "rejected":
        raise ApplicationError("conflict")
    sheets = Sheets(clients_csv, investments_csv)
    trial_id, as_of, _ = await _cleared(session, clock, sheets)

    switch_id = push_id(clock)
    now = clock.now()
    await session.execute(
        _INSERT,
        {
            "id": switch_id,
            "trial_id": trial_id,
            "as_of": as_of,
            "clients_sha256": _sha256(clients_csv),
            "investments_sha256": _sha256(investments_csv),
            "proposed_by": _id(staff),
            "proposed_at": now,
        },
    )
    await session.execute(
        _INSERT_FILES,
        {
            "switch_id": switch_id,
            "clients_csv": sealer.seal(clients_csv, _associated("clients_csv", switch_id)),
            "investments_csv": sealer.seal(
                investments_csv, _associated("investments_csv", switch_id)
            ),
        },
    )
    await service._audit(
        session,
        staff,
        "change",
        "migration.switch_proposed",
        "migration_switch",
        switch_id,
        clock,
        details={"trial_id": trial_id, "as_of": as_of.isoformat()},
    )
    return await _by_id(session, switch_id)


async def decide_switch(
    session: AsyncSession,
    sealer: Sealer,
    staff: Actor,
    clock: Clock,
    switch_id: str,
    *,
    approve: bool,
    reason: str | None = None,
) -> SwitchView:
    """Approve or reject the switch, as a member of staff other than its proposer.

    An approval checks everything again and creates every client's account. It is refused with
    `conflict` when a newer trial was run or anything blocks the switch now, and with
    `phone_taken`, naming the rows, when a phone number already has an account in Finvest 2.0.
    Nothing is kept from a refused approval.
    """
    _staff_only(staff)
    note = " ".join((reason or "").split()) or None
    if note is not None and len(note) > MAX_REASON_LENGTH:
        raise _invalid(["reason"], "invalid_reason")
    if not approve and note is None:
        raise _invalid(["reason"], "invalid_reason")
    await session.execute(_LOCK)
    switch = await _by_id(session, switch_id)
    if switch.proposed_by == _id(staff):
        raise ApplicationError("forbidden")
    if switch.state != "proposed":
        raise ApplicationError("conflict")

    report: TrialReport | None = None
    if approve:
        if (await session.execute(_APPROVED)).first() is not None:
            raise ApplicationError("conflict")
        sheets = await open_sheets(session, sealer, switch_id)
        trial_id, _, report = await _cleared(session, clock, sheets)
        if trial_id != switch.trial_id:
            raise ApplicationError("conflict")

    outcome: SwitchState = "approved" if approve else "rejected"
    now = clock.now()
    # Not in a savepoint: the carried clients below check that this row is of this transaction.
    await session.execute(
        _INSERT_DECISION,
        {
            "switch_id": switch_id,
            "outcome": outcome,
            "reason": note,
            "decided_by": _id(staff),
            "decided_at": now,
        },
    )
    if report is not None:
        await _create_accounts(session, staff, clock, switch_id, report)
        # The worker carries each client's identity, investments and claim over from here.
        await enqueue(
            session,
            JobRequest(
                kind=CARRY_OVER,
                idempotency_key=switch_id,
                payload={"switch_id": switch_id},
                max_age=timedelta(days=7),
            ),
            now=now,
        )
    await service._audit(
        session,
        staff,
        "approval",
        f"migration.switch_{outcome}",
        "migration_switch",
        switch_id,
        clock,
        # An approval is recorded with a reason. Without one of their own, it says what it rests on.
        reason=note or f"Every check cleared for the trial as of {switch.as_of.isoformat()}",
        details={"clients": len(report.clients)} if report is not None else None,
    )
    return await _by_id(session, switch_id)


async def switch_by_id(session: AsyncSession, switch_id: str) -> SwitchView:
    """One switch and what was decided. `not_found` for an unknown ID."""
    return await _by_id(session, switch_id)


async def open_sheets(session: AsyncSession, sealer: Sealer, switch_id: str) -> Sheets:
    """The sheets a switch was proposed with. `not_found` once the worker has removed them."""
    row = (await session.execute(_FILES, {"id": switch_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return Sheets(
        sealer.open(row.clients_csv, _associated("clients_csv", switch_id)),
        sealer.open(row.investments_csv, _associated("investments_csv", switch_id)),
    )


# Internals --------------------------------------------------------------------------------------


async def _cleared(
    session: AsyncSession, clock: Clock, sheets: Sheets
) -> tuple[str, date, TrialReport]:
    """The latest trial, when these are its sheets and nothing blocks the switch."""
    view = await service.overview(session)
    if view.blockers or view.trial is None:
        raise ApplicationError(
            "conflict",
            fields=[FieldProblem(field_path=["blockers"], code=code) for code in view.blockers],
        )
    trial = view.trial
    files = (await session.execute(_TRIAL_FILES, {"id": trial.id})).one()
    if _sha256(sheets.clients_csv) != files.clients_sha256:
        raise _invalid(["clients_csv"], "not_the_trial_sheet")
    if _sha256(sheets.investments_csv) != files.investments_sha256:
        raise _invalid(["investments_csv"], "not_the_trial_sheet")
    # Investments are opened with interest up to the "as of" day, which must be over.
    if trial.as_of >= business_date(clock.now()):
        raise _invalid(["as_of"], "not_yet_past")

    views = await investments.list_products(session, clock)
    products = {product.product.code: product.product for product in views}
    tax = {view.product.code: view.rate.tax_bps for view in views if view.rate is not None}
    report = run_trial(
        sheets.clients_csv, sheets.investments_csv, as_of=trial.as_of, products=products
    )
    # The same sheets read differently only if a product or tax rate changed since the trial.
    if not report.clean or check_investments(report, products, tax).differing != trial.differing:
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["blockers"], code="trial_out_of_date")]
        )
    without_phone = [client.row for client in report.clients if client.phone is None]
    if without_phone:
        raise ApplicationError(
            "validation_failed",
            fields=[
                FieldProblem(field_path=["clients", row, "phone"], code="missing")
                for row in without_phone[:MAX_NAMED_ROWS]
            ],
        )
    return trial.id, trial.as_of, report


async def _create_accounts(
    session: AsyncSession, staff: Actor, clock: Clock, switch_id: str, report: TrialReport
) -> None:
    taken: list[int] = []
    now = clock.now()
    for client in report.clients:
        assert client.phone is not None  # refused by `_cleared`
        try:
            client_id = await create_unclaimed_client(
                session, clock, staff, phone=client.phone, email=client.email
            )
        except ApplicationError as error:
            if error.code != "phone_taken":
                raise
            taken.append(client.row)
            continue
        await session.execute(
            _INSERT_CARRIED,
            {
                "ref": client.legacy_client_ref,
                "switch_id": switch_id,
                "client_id": client_id,
                "carried_by": _id(staff),
                "carried_at": now,
            },
        )
    if taken:
        # Raised before the transaction commits, so no account and no decision is kept.
        raise ApplicationError(
            "phone_taken",
            fields=[
                FieldProblem(field_path=["clients", row, "phone"], code="phone_taken")
                for row in taken[:MAX_NAMED_ROWS]
            ],
        )


async def _by_id(session: AsyncSession, switch_id: str) -> SwitchView:
    row = (await session.execute(_BY_ID, {"id": switch_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


def _view(row: Row[Any]) -> SwitchView:
    return SwitchView(
        id=row.id,
        trial_id=row.trial_id,
        as_of=row.as_of,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        state=row.outcome or "proposed",
        decided_by=row.decided_by,
        decision_reason=row.decision_reason,
        clients_carried=int(row.carried),
    )


def _associated(column: str, switch_id: str) -> bytes:
    return associated_data(_FILES_TABLE, column, switch_id)


def _sha256(sheet: str) -> str:
    return hashlib.sha256(sheet.encode()).hexdigest()


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def _invalid(path: list[str | int], code: str) -> ApplicationError:
    return ApplicationError("validation_failed", fields=[FieldProblem(field_path=path, code=code)])
