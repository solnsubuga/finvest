"""The account code map of each accounting system, and the two-person change to it.

Every function works inside the caller's transaction. A proposal, its decision and their audit
events commit together or not at all. Migration 0290 holds the rules PostgreSQL enforces: the
person who decides is never the person who proposed, and a decided map never changes.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.finance.domain.code_maps import MAX_REASON, InvalidCodeMap, changes, clean
from app.modules.finance.domain.formats import CodeMap, System
from app.modules.ledger import statements
from app.modules.ledger.statements import LedgerCode
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "finance_code_map"
Decision = Literal["approved", "rejected", "withdrawn"]
Status = Literal["proposed", "approved", "rejected", "withdrawn"]


@dataclass(frozen=True, slots=True)
class MapVersion:
    """One proposed map: waiting, or decided."""

    id: str
    system: System
    accounts: dict[str, str]
    tax_rate: str | None
    reason: str
    status: Status
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None

    def as_code_map(self) -> CodeMap:
        return CodeMap(self.system, dict(self.accounts), self.tax_rate)


@dataclass(frozen=True, slots=True)
class SystemMaps:
    """One system as the Settings screen lists it."""

    system: System
    # The map in use, or None before any was approved.
    active: MapVersion | None
    # The proposal waiting for a second person, or None.
    proposal: MapVersion | None
    # The last few decisions, newest first, for the screen's history.
    history: list[MapVersion]


_COLUMNS = (
    "id, system, tax_rate, reason, status, proposed_by, proposed_at, decided_by, decided_at, "
    "decision_reason"
)
_ACTIVE = text(
    f"SELECT {_COLUMNS} FROM finance_code_maps "  # noqa: S608 - constant column list
    "WHERE system = :system AND status = 'approved' "
    "ORDER BY decided_at DESC, id DESC LIMIT 1"
)
_OPEN = text(
    f"SELECT {_COLUMNS} FROM finance_code_maps "  # noqa: S608 - constant column list
    "WHERE system = :system AND status = 'proposed'"
)
_ALL_OPEN = text(
    f"SELECT {_COLUMNS} FROM finance_code_maps "  # noqa: S608 - constant column list
    "WHERE status = 'proposed' ORDER BY proposed_at, id"
)
_HISTORY = text(
    f"SELECT {_COLUMNS} FROM finance_code_maps "  # noqa: S608 - constant column list
    "WHERE system = :system AND status <> 'proposed' "
    "ORDER BY decided_at DESC, id DESC LIMIT :limit"
)
_OPEN_BY_ID = text(
    f"SELECT {_COLUMNS} FROM finance_code_maps "  # noqa: S608 - constant column list
    "WHERE id = :id AND status = 'proposed' FOR UPDATE"
)
_LINES = text(
    "SELECT map_id, ledger_code, account FROM finance_code_map_lines "
    "WHERE map_id = ANY(:ids) ORDER BY ledger_code"
)
_INSERT = text(
    "INSERT INTO finance_code_maps (id, system, tax_rate, reason, proposed_by, proposed_at) "
    "VALUES (:id, :system, :tax_rate, :reason, :staff, :now)"
)
_INSERT_LINE = text(
    "INSERT INTO finance_code_map_lines (map_id, ledger_code, account) "
    "VALUES (:map_id, :code, :account)"
)
_DECIDE = text(
    "UPDATE finance_code_maps SET status = :status, decided_by = :staff, decided_at = :now, "
    "decision_reason = :reason WHERE id = :id"
)
HISTORY = 5


async def active_map(session: AsyncSession, system: System) -> MapVersion | None:
    """The map an export of `system` uses now: the approved one decided last."""
    row = (await session.execute(_ACTIVE, {"system": system.value})).one_or_none()
    return (await _with_lines(session, [row]))[0] if row else None


async def list_maps(session: AsyncSession) -> list[SystemMaps]:
    """Every system, in the order the console shows them."""
    listed = []
    for system in System:
        active = await active_map(session, system)
        open_row = (await session.execute(_OPEN, {"system": system.value})).one_or_none()
        history_rows = (
            await session.execute(_HISTORY, {"system": system.value, "limit": HISTORY})
        ).all()
        listed.append(
            SystemMaps(
                system=system,
                active=active,
                proposal=(await _with_lines(session, [open_row]))[0] if open_row else None,
                history=[_version(row, {}) for row in history_rows],
            )
        )
    return listed


async def waiting(session: AsyncSession) -> list[MapVersion]:
    """Every proposal waiting for a second person, oldest first, without its accounts."""
    return [_version(row, {}) for row in (await session.execute(_ALL_OPEN)).all()]


async def ledger_codes(session: AsyncSession) -> list[LedgerCode]:
    return await statements.codes(session)


async def propose(
    session: AsyncSession,
    staff: Actor,
    system: System,
    accounts: dict[str, str],
    tax_rate: str | None,
    reason: str,
    clock: Clock,
) -> MapVersion:
    """Put a whole new map for `system` forward. Exports keep the map in use until it is
    approved by someone else."""
    staff_id = _staff_id(staff)
    reason = _reason(reason)
    known = {code.code for code in await statements.codes(session)}
    try:
        cleaned, rate = clean(system, accounts, tax_rate, known)
    except InvalidCodeMap as error:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=path, code=code) for path, code in error.problems],
        ) from None
    before = await active_map(session, system)
    now = clock.now()
    version = MapVersion(
        id=push_id(clock),
        system=system,
        accounts=cleaned,
        tax_rate=rate,
        reason=reason,
        status="proposed",
        proposed_by=staff_id,
        proposed_at=now,
        decided_by=None,
        decided_at=None,
        decision_reason=None,
    )
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": version.id,
                    "system": system.value,
                    "tax_rate": rate,
                    "reason": reason,
                    "staff": staff_id,
                    "now": now,
                },
            )
    except IntegrityError:
        # A change to this system's map is already waiting. It is decided or withdrawn first.
        raise ApplicationError("conflict") from None
    for code, account in sorted(cleaned.items()):
        await session.execute(
            _INSERT_LINE, {"map_id": version.id, "code": code, "account": account}
        )
    counts = changes(before.accounts if before else {}, cleaned)
    await _audit(
        session,
        staff_id,
        "finance.code_map_proposed",
        version,
        reason,
        clock,
        {"codes": len(cleaned), **counts},
    )
    return version


async def decide(
    session: AsyncSession,
    staff: Actor,
    map_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
) -> MapVersion:
    """Approve or reject someone else's proposal, or withdraw your own."""
    staff_id = _staff_id(staff)
    reason = _reason(reason)
    row = (await session.execute(_OPEN_BY_ID, {"id": map_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    version = (await _with_lines(session, [row]))[0]
    own = version.proposed_by == staff_id
    if own != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    now = clock.now()
    await session.execute(
        _DECIDE,
        {"status": decision, "staff": staff_id, "now": now, "reason": reason, "id": version.id},
    )
    await _audit(
        session,
        staff_id,
        f"finance.code_map_{decision}",
        version,
        reason,
        clock,
        {"codes": len(version.accounts)},
    )
    return MapVersion(
        id=version.id,
        system=version.system,
        accounts=version.accounts,
        tax_rate=version.tax_rate,
        reason=version.reason,
        status=decision,
        proposed_by=version.proposed_by,
        proposed_at=version.proposed_at,
        decided_by=staff_id,
        decided_at=now,
        decision_reason=reason,
    )


async def _with_lines(session: AsyncSession, rows: list[Row[Any]]) -> list[MapVersion]:
    ids = [row.id for row in rows]
    found: dict[str, dict[str, str]] = {map_id: {} for map_id in ids}
    for line in (await session.execute(_LINES, {"ids": ids})).all():
        found[line.map_id][line.ledger_code] = line.account
    return [_version(row, found[row.id]) for row in rows]


def _version(row: Row[Any], accounts: dict[str, str]) -> MapVersion:
    return MapVersion(
        id=row.id,
        system=System(row.system),
        accounts=accounts,
        tax_rate=row.tax_rate,
        reason=row.reason,
        status=row.status,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
    )


async def _audit(
    session: AsyncSession,
    staff_id: str,
    action: str,
    version: MapVersion,
    reason: str,
    clock: Clock,
    details: dict[str, int],
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "rejected")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=version.id,
            actor=AuditActor.staff(staff_id),
            reason=reason,
            details={"system": version.system.value, **details},
        ),
        clock,
    )


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["reason"], code="required")]
        )
    return cleaned
