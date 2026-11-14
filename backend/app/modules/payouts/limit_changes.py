"""Changing the withdrawal limits and the review threshold, by two people, on Settings.

One member of staff proposes a whole new version of the limits and the day it starts. A
different one approves, which inserts the version in `withdrawal_limits` in their own name and
records it on the proposal, in one transaction; or rejects it. The proposer may withdraw it.
Migration 0341 holds the rules PostgreSQL enforces, and migration 0031 the version's own.

A version starts on a later day, never today: a client's daily allowance is counted against one
version for the whole day, and payouts already held or queued were judged by the one in force.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.payouts.domain.limits import InvalidLimits, WithdrawalLimits
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

RESOURCE = "withdrawal_limit_change"
MAX_REASON = 500
Decision = Literal["approved", "rejected", "withdrawn"]


@dataclass(frozen=True, slots=True)
class LimitsChange:
    id: str
    limits: WithdrawalLimits
    reason: str
    proposed_by: str
    proposed_at: datetime


_COLUMNS = (
    "id, effective_from, currency, mobile_money_per_payout, mobile_money_per_day, "
    "bank_per_payout, bank_per_day, review_above, reason, proposed_by, proposed_at"
)
_OPEN = text(
    f"SELECT {_COLUMNS} FROM withdrawal_limit_changes WHERE status = 'proposed'"  # noqa: S608
)
_OPEN_BY_ID = text(
    f"SELECT {_COLUMNS} FROM withdrawal_limit_changes "  # noqa: S608 - constant column list
    "WHERE id = :id AND status = 'proposed' FOR UPDATE"
)
_INSERT = text(
    "INSERT INTO withdrawal_limit_changes (id, effective_from, currency, "
    "mobile_money_per_payout, mobile_money_per_day, bank_per_payout, bank_per_day, "
    "review_above, reason, proposed_by, proposed_at) VALUES (:id, :effective_from, :currency, "
    ":mobile_money_per_payout, :mobile_money_per_day, :bank_per_payout, :bank_per_day, "
    ":review_above, :reason, :staff, :now)"
)
_VERSION = text(
    "INSERT INTO withdrawal_limits (id, effective_from, currency, mobile_money_per_payout, "
    "mobile_money_per_day, bank_per_payout, bank_per_day, review_above, proposed_by, "
    "approved_by, reason, created_at) VALUES (:id, :effective_from, :currency, "
    ":mobile_money_per_payout, :mobile_money_per_day, :bank_per_payout, :bank_per_day, "
    ":review_above, :proposed_by, :staff, :reason, :now)"
)
_DECIDE = text(
    "UPDATE withdrawal_limit_changes SET status = :status, decided_by = :staff, "
    "decided_at = :now, decision_reason = :reason, limits_id = :limits_id WHERE id = :id"
)
_TAKEN = text("SELECT EXISTS (SELECT FROM withdrawal_limits WHERE effective_from = :day)")


async def waiting(session: AsyncSession) -> LimitsChange | None:
    """The change waiting for a decision, if there is one."""
    row = (await session.execute(_OPEN)).one_or_none()
    return None if row is None else _change(row)


async def propose(
    session: AsyncSession,
    staff: Actor,
    *,
    effective_from: date,
    mobile_money_per_payout: Money,
    mobile_money_per_day: Money | None,
    bank_per_payout: Money | None,
    bank_per_day: Money | None,
    review_above: Money | None,
    reason: str,
    clock: Clock,
) -> LimitsChange:
    """Put a new version of the limits forward. Nothing changes until someone else approves.

    422 for limits that contradict themselves or a start that is not a later day. 409 if a
    change is already waiting or a version already starts that day.
    """
    reason = _reason(reason)
    try:
        limits = WithdrawalLimits(
            effective_from=effective_from,
            mobile_money_per_payout=mobile_money_per_payout,
            mobile_money_per_day=mobile_money_per_day,
            bank_per_payout=bank_per_payout,
            bank_per_day=bank_per_day,
            review_above=review_above,
        )
    except InvalidLimits:
        raise _invalid("limits", "limits_invalid") from None
    now = clock.now()
    if effective_from <= business_date(now):
        raise _invalid("effective_from", "not_a_later_day")
    if await session.scalar(_TAKEN, {"day": effective_from}):
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["effective_from"], code="day_taken")]
        )
    change = LimitsChange(push_id(), limits, reason, _staff_id(staff), now)
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    **_values(limits),
                    "id": change.id,
                    "reason": reason,
                    "staff": change.proposed_by,
                    "now": now,
                },
            )
    except IntegrityError:
        raise ApplicationError("conflict") from None
    await _audit(session, staff, "proposed", change, reason, clock)
    return change


async def decide(
    session: AsyncSession,
    staff: Actor,
    change_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
) -> LimitsChange:
    """Approve or reject someone else's change, or withdraw your own.

    Approving a change whose start day has come, or whose day another version took, is refused
    with 409: propose it again for a later day.
    """
    reason = _reason(reason)
    row = (await session.execute(_OPEN_BY_ID, {"id": change_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    change = _change(row)
    own = change.proposed_by == _staff_id(staff)
    if own != (decision == "withdrawn"):
        raise ApplicationError("forbidden")
    now = clock.now()
    limits_id: str | None = None
    if decision == "approved":
        if change.limits.effective_from <= business_date(now):
            raise ApplicationError(
                "conflict",
                fields=[FieldProblem(field_path=["effective_from"], code="not_a_later_day")],
            )
        limits_id = push_id()
        try:
            async with session.begin_nested():
                await session.execute(
                    _VERSION,
                    {
                        **_values(change.limits),
                        "id": limits_id,
                        "proposed_by": change.proposed_by,
                        "staff": staff.id,
                        "reason": change.reason,
                        "now": now,
                    },
                )
        except IntegrityError:
            raise ApplicationError(
                "conflict", fields=[FieldProblem(field_path=["effective_from"], code="day_taken")]
            ) from None
    await session.execute(
        _DECIDE,
        {
            "status": decision,
            "staff": staff.id,
            "now": now,
            "reason": reason,
            "limits_id": limits_id,
            "id": change.id,
        },
    )
    await _audit(session, staff, decision, change, reason, clock)
    return change


def _values(limits: WithdrawalLimits) -> dict[str, object]:
    def minor(amount: Money | None) -> int | None:
        return None if amount is None else amount.minor

    return {
        "effective_from": limits.effective_from,
        "currency": limits.currency.value,
        "mobile_money_per_payout": limits.mobile_money_per_payout.minor,
        "mobile_money_per_day": minor(limits.mobile_money_per_day),
        "bank_per_payout": minor(limits.bank_per_payout),
        "bank_per_day": minor(limits.bank_per_day),
        "review_above": minor(limits.review_above),
    }


async def _audit(
    session: AsyncSession,
    staff: Actor,
    step: str,
    change: LimitsChange,
    reason: str,
    clock: Clock,
) -> None:
    # The limits are on the row, which never changes. The event says which day, who and when.
    await audit.record(
        session,
        AuditEvent(
            category="approval" if step in ("approved", "rejected") else "change",
            action=f"payouts.limits_{step}",
            resource_type=RESOURCE,
            resource_id=change.id,
            actor=AuditActor.staff(_staff_id(staff)),
            reason=reason,
            details={"effective_from": change.limits.effective_from.isoformat()},
        ),
        clock,
    )


def _change(row: Row[Any]) -> LimitsChange:
    currency = Currency(row.currency)

    def money(value: int | None) -> Money | None:
        return None if value is None else Money(int(value), currency)

    return LimitsChange(
        id=row.id,
        limits=WithdrawalLimits(
            effective_from=row.effective_from,
            mobile_money_per_payout=Money(int(row.mobile_money_per_payout), currency),
            mobile_money_per_day=money(row.mobile_money_per_day),
            bank_per_payout=money(row.bank_per_payout),
            bank_per_day=money(row.bank_per_day),
            review_above=money(row.review_above),
        ),
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
    )


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON:
        raise _invalid("reason", "required")
    return cleaned
