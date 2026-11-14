"""The platform's settings: the value in use, and the two-person change to each.

Every function works inside the caller's transaction. A proposal, its decision and their audit
events commit together or not at all. Migration 0340 holds the rules PostgreSQL enforces: the
person who decides is never the person who proposed, and a decided row never changes.

Other modules read a setting through the typed functions at the end (`payment_channels`,
`public_holidays` and so on). They never read the table.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.platform_settings.domain.catalogue import (
    DEFINITIONS,
    KEYS,
    MAX_REASON,
    BankPayoutCutoff,
    InvalidSetting,
    Key,
    MaturityReminders,
    OfficeNetworks,
    PaymentChannels,
    PublicHolidays,
    StaffIdleMinutes,
    StatementLayouts,
    Value,
    checked,
    default,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "platform_setting"
Decision = Literal["approved", "rejected", "withdrawn"]


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    key: Key
    value: Value
    reason: str
    proposed_by: str
    proposed_at: datetime


@dataclass(frozen=True, slots=True)
class Setting:
    """One setting as the console's Settings screen lists it."""

    key: Key
    value: Value
    # None while the default is in use: nobody has approved a value yet.
    changed_at: datetime | None
    changed_by: str | None
    proposal: Proposal | None


_IN_USE = text(
    "SELECT value FROM platform_settings WHERE key = :key AND status = 'approved' "
    "ORDER BY decided_at DESC, id DESC LIMIT 1"
)
_ALL_IN_USE = text(
    "SELECT DISTINCT ON (key) key, value, decided_at, decided_by FROM platform_settings "
    "WHERE status = 'approved' ORDER BY key, decided_at DESC, id DESC"
)
_COLUMNS = "id, key, value, reason, proposed_by, proposed_at"
_OPEN = text(f"SELECT {_COLUMNS} FROM platform_settings WHERE status = 'proposed'")  # noqa: S608
_OPEN_BY_ID = text(
    f"SELECT {_COLUMNS} FROM platform_settings "  # noqa: S608 - constant column list
    "WHERE id = :id AND status = 'proposed' FOR UPDATE"
)
_INSERT = text(
    "INSERT INTO platform_settings (id, key, value, reason, proposed_by, proposed_at) "
    "VALUES (:id, :key, CAST(:value AS jsonb), :reason, :staff, :now)"
)
_DECIDE = text(
    "UPDATE platform_settings SET status = :status, decided_by = :staff, "
    "decided_at = :now, decision_reason = :reason WHERE id = :id"
)


class ApprovalCheck:
    """A rule a value must pass at the moment it is approved, beyond its own shape.

    The office networks are the one case: approving a list that leaves out where the approver is
    sitting would lock them, and possibly everyone, out of the console.
    """

    def __init__(self, address: str | None) -> None:
        self.address = address

    def check(self, value: Value) -> None:
        if isinstance(value, OfficeNetworks) and not value.allows(self.address):
            raise ApplicationError(
                "conflict", fields=[FieldProblem(field_path=["value"], code="leaves_you_out")]
            )


async def current(session: AsyncSession, key: Key, now: datetime) -> Value:
    """The value in use for `key`: the last one approved, or the default."""
    row = (await session.execute(_IN_USE, {"key": key})).one_or_none()
    return default(key, now) if row is None else _parse(key, row.value)


async def list_settings(session: AsyncSession, now: datetime) -> list[Setting]:
    """Every setting, the value in use and any change waiting for a decision."""
    in_use = {row.key: row for row in (await session.execute(_ALL_IN_USE)).all()}
    proposals = {row.key: _proposal(row) for row in (await session.execute(_OPEN)).all()}
    listed = []
    for key in KEYS:
        row = in_use.get(key)
        listed.append(
            Setting(
                key=key,
                value=_parse(key, row.value) if row else default(key, now),
                changed_at=row.decided_at if row else None,
                changed_by=row.decided_by if row else None,
                proposal=proposals.get(key),
            )
        )
    return listed


async def waiting(session: AsyncSession) -> list[Proposal]:
    """Every change waiting for a decision, for the approvals inbox."""
    return [_proposal(row) for row in (await session.execute(_OPEN)).all()]


async def propose(
    session: AsyncSession,
    staff: Actor,
    key: Key,
    value: Any,
    reason: str,
    clock: Clock,
    *,
    check: ApprovalCheck,
) -> Proposal:
    """Put a new value forward. Nothing changes until a different person approves it.

    `check` is applied to the proposer too, so nobody proposes office networks that leave out
    the place they are proposing from.
    """
    reason = _reason(reason)
    parsed, stored = _checked(key, value)
    check.check(parsed)
    proposal = Proposal(push_id(), key, parsed, reason, _staff_id(staff), clock.now())
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": proposal.id,
                    "key": key,
                    "value": json.dumps(stored),
                    "reason": reason,
                    "staff": proposal.proposed_by,
                    "now": proposal.proposed_at,
                },
            )
    except IntegrityError:
        # A change to this setting is already waiting. It is decided or withdrawn first.
        raise ApplicationError("conflict") from None
    await _audit(session, staff, "proposed", proposal, reason, clock)
    return proposal


async def decide(
    session: AsyncSession,
    staff: Actor,
    proposal_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
    *,
    check: ApprovalCheck,
) -> Proposal:
    """Approve or reject someone else's proposal, or withdraw your own."""
    reason = _reason(reason)
    row = (await session.execute(_OPEN_BY_ID, {"id": proposal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    proposal = _proposal(row)
    own = proposal.proposed_by == _staff_id(staff)
    if own != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    if decision == "approved":
        check.check(proposal.value)
    await session.execute(
        _DECIDE,
        {"status": decision, "staff": staff.id, "now": clock.now(), "reason": reason, "id": row.id},
    )
    await _audit(session, staff, decision, proposal, reason, clock)
    return proposal


async def proposal_key(session: AsyncSession, proposal_id: str) -> Key | None:
    """Which setting a waiting proposal changes, so the route can check the right permission."""
    key = await session.scalar(
        text("SELECT key FROM platform_settings WHERE id = :id AND status = 'proposed'"),
        {"id": proposal_id},
    )
    return cast(Key, key) if key in KEYS else None


# Typed reads for the modules each setting applies to ------------------------------------------


async def _typed[V: Value](session: AsyncSession, key: Key, kind: type[V], now: datetime) -> V:
    value = await current(session, key, now)
    if not isinstance(value, kind):  # pragma: no cover - the catalogue pairs them
        raise TypeError(f"{key} is not a {kind.__name__}")
    return value


async def payment_channels(session: AsyncSession, now: datetime) -> PaymentChannels:
    return await _typed(session, "payment_channels", PaymentChannels, now)


async def public_holidays(session: AsyncSession, now: datetime) -> PublicHolidays:
    return await _typed(session, "public_holidays", PublicHolidays, now)


async def bank_payout_cutoff(session: AsyncSession, now: datetime) -> BankPayoutCutoff:
    return await _typed(session, "bank_payout_cutoff", BankPayoutCutoff, now)


async def maturity_reminders(session: AsyncSession, now: datetime) -> MaturityReminders:
    return await _typed(session, "maturity_reminders", MaturityReminders, now)


async def staff_idle_minutes(session: AsyncSession, now: datetime) -> StaffIdleMinutes:
    return await _typed(session, "staff_idle_minutes", StaffIdleMinutes, now)


async def office_networks(session: AsyncSession, now: datetime) -> OfficeNetworks:
    return await _typed(session, "office_networks", OfficeNetworks, now)


async def statement_layouts(session: AsyncSession, now: datetime) -> StatementLayouts:
    return await _typed(session, "statement_layouts", StatementLayouts, now)


# Helpers --------------------------------------------------------------------------------------


def _checked(key: Key, value: Any) -> tuple[Value, Any]:
    try:
        return checked(key, value)
    except InvalidSetting as error:
        raise ApplicationError(
            "validation_failed",
            fields=[
                FieldProblem(field_path=list(path), code=code) for path, code in error.problems
            ],
        ) from None


def _parse(key: Key, stored: Any) -> Value:
    # A stored value passed these same rules when it was proposed and approved.
    return DEFINITIONS[key].parse(stored)


async def _audit(
    session: AsyncSession, staff: Actor, step: str, proposal: Proposal, reason: str, clock: Clock
) -> None:
    # The value itself is on the row, which never changes. The event says which, who and when.
    await audit.record(
        session,
        AuditEvent(
            category="approval" if step in ("approved", "rejected") else "change",
            action=f"settings.{proposal.key}_{step}",
            resource_type=RESOURCE,
            resource_id=proposal.id,
            actor=AuditActor.staff(_staff_id(staff)),
            reason=reason,
            details={"key": proposal.key},
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


def _proposal(row: Row[Any]) -> Proposal:
    key = cast(Key, row.key)
    return Proposal(
        id=row.id,
        key=key,
        value=_parse(key, row.value),
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
    )
