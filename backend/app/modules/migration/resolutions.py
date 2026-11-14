"""Retry or close a client the worker could not carry over, with two members of staff.

The worker notes a client it could not carry over in `migration_carry_problems`, with a code
and the attempt it was. Staff put the cause right by hand, then one of them proposes what to do
and why, and another approves or rejects it:

* `retry`: the worker tries the client again. Refused again, they are noted as the next attempt.
* `close`: the switch no longer carries the client over. Staff look after them by hand.

An approval queues the carry-over job again, so the worker retries the client, or removes the
sealed sheets once nobody is waiting any more. A rejection leaves the client noted, and anyone may
propose again. Each note takes at most one proposal at a time and at most one approval.

The caller opens `Database.actor_transaction(staff)` for the member of staff it passes here.
Migration 0076 holds the rest: insert-only rows, and a decision by someone other than the
proposer.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.migration import service, switch
from app.modules.migration.domain.finance import MAX_REASON_LENGTH
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

Action = Literal["retry", "close"]
# Where a noted client stands. `retrying` is an approved retry the worker has not finished.
NotCarriedState = Literal["noted", "retry_proposed", "close_proposed", "retrying"]
ResolutionState = Literal["proposed", "approved", "rejected"]
# The most times the worker notes one client. Migration 0076 holds the same bound.
MAX_ATTEMPTS = 99

_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended('migration.carry_resolution', 76))")
# Each client's latest note, with the one resolution still standing for it: the approved one, or
# else the one awaiting a decision. A rejected one stands for nothing.
_LATEST = text(
    "SELECT p.legacy_client_ref, p.code, p.attempt, p.noted_at, r.id AS resolution_id, "
    "r.action, r.reason, r.proposed_by, r.proposed_at, r.outcome, EXISTS ("
    "  SELECT FROM migration_carried_clients AS c "
    "  JOIN migration_claims AS m ON m.client_id = c.client_id "
    "  WHERE c.legacy_client_ref = p.legacy_client_ref) AS claimed "
    "FROM migration_carry_problems AS p "
    "LEFT JOIN LATERAL ("
    "  SELECT r.id, r.action, r.reason, r.proposed_by, r.proposed_at, d.outcome "
    "  FROM migration_carry_resolutions AS r "
    "  LEFT JOIN migration_carry_resolution_decisions AS d ON d.resolution_id = r.id "
    "  WHERE r.switch_id = p.switch_id AND r.legacy_client_ref = p.legacy_client_ref "
    "    AND r.attempt = p.attempt AND (d.outcome IS NULL OR d.outcome = 'approved') "
    "  ORDER BY d.outcome IS NULL, r.proposed_at DESC, r.id DESC LIMIT 1"
    ") AS r ON true "
    "WHERE p.switch_id = :id AND p.attempt = ("
    "  SELECT max(q.attempt) FROM migration_carry_problems AS q "
    "  WHERE q.switch_id = p.switch_id AND q.legacy_client_ref = p.legacy_client_ref) "
    "ORDER BY p.legacy_client_ref"
)
_RESOLUTION = text(
    "SELECT r.id, r.switch_id, r.legacy_client_ref, r.attempt, r.action, r.reason, "
    "r.proposed_by, r.proposed_at, d.outcome, d.decided_by, d.reason AS decision_reason "
    "FROM migration_carry_resolutions AS r "
    "LEFT JOIN migration_carry_resolution_decisions AS d ON d.resolution_id = r.id "
    "WHERE r.id = :id"
)
_INSERT = text(
    "INSERT INTO migration_carry_resolutions (id, switch_id, legacy_client_ref, attempt, action, "
    "reason, proposed_by, proposed_at) VALUES (:id, :switch_id, :ref, :attempt, :action, "
    ":reason, :proposed_by, :proposed_at)"
)
_INSERT_DECISION = text(
    "INSERT INTO migration_carry_resolution_decisions (resolution_id, outcome, reason, "
    "decided_by, decided_at) VALUES (:id, :outcome, :reason, :decided_by, :decided_at)"
)


@dataclass(frozen=True, slots=True)
class ResolutionView:
    id: str
    switch_id: str
    legacy_client_ref: str
    attempt: int
    action: Action
    reason: str
    proposed_by: str
    proposed_at: datetime
    state: ResolutionState
    decided_by: str | None
    decision_reason: str | None


@dataclass(frozen=True, slots=True)
class NotCarried:
    """A client's latest note and where it stands. Nothing from the sheets."""

    legacy_client_ref: str
    code: str
    attempt: int
    noted_at: datetime
    state: NotCarriedState
    # The proposal awaiting a decision, or the approved retry. Null while only noted.
    resolution_id: str | None
    proposed_by: str | None
    # Why it was proposed, for the person who decides.
    reason: str | None


@dataclass(frozen=True, slots=True)
class Standing:
    """What the worker makes of a client's latest note."""

    attempt: int
    # `wait`: noted or awaiting a decision. `retry`: try again. `closed`: not carried over.
    next: Literal["wait", "retry", "closed"]
    claimed: bool


async def not_carried(session: AsyncSession, switch_id: str) -> list[NotCarried]:
    """Each client the switch has not carried over and not closed, by Finvest 1.0 reference."""
    shown: list[NotCarried] = []
    for row in await session.execute(_LATEST, {"id": switch_id}):
        state = _state(row)
        if state is None:
            continue
        shown.append(
            NotCarried(
                legacy_client_ref=row.legacy_client_ref,
                code=row.code,
                attempt=int(row.attempt),
                noted_at=row.noted_at,
                state=state,
                resolution_id=row.resolution_id,
                proposed_by=row.proposed_by,
                reason=row.reason,
            )
        )
    return shown


async def standings(session: AsyncSession, switch_id: str) -> dict[str, Standing]:
    """Each noted client's latest note, for the worker. Clients never noted are not here."""
    found: dict[str, Standing] = {}
    for row in await session.execute(_LATEST, {"id": switch_id}):
        if row.outcome == "approved":
            next_step: Literal["wait", "retry", "closed"] = (
                "retry" if row.action == "retry" else "closed"
            )
        else:
            next_step = "wait"
        found[row.legacy_client_ref] = Standing(int(row.attempt), next_step, bool(row.claimed))
    return found


async def propose_resolution(
    session: AsyncSession,
    staff: Actor,
    clock: Clock,
    switch_id: str,
    *,
    legacy_client_ref: str,
    action: Action,
    reason: str,
) -> ResolutionView:
    """Ask a second person to approve retrying or closing a noted client.

    `not_found` when the switch has no note for that reference. `conflict` while a proposal for
    it awaits a decision, once one was approved, or when the client was tried too many times.
    """
    _staff_only(staff)
    note = _reason(reason)
    await session.execute(_LOCK)
    await switch.switch_by_id(session, switch_id)
    latest = {
        row.legacy_client_ref: row for row in await session.execute(_LATEST, {"id": switch_id})
    }
    row = latest.get(legacy_client_ref)
    if row is None:
        raise ApplicationError("not_found")
    if _state(row) != "noted":
        raise ApplicationError("conflict")
    if action == "retry" and row.attempt >= MAX_ATTEMPTS:
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["action"], code="too_many_attempts")]
        )
    resolution_id = push_id(clock)
    await session.execute(
        _INSERT,
        {
            "id": resolution_id,
            "switch_id": switch_id,
            "ref": legacy_client_ref,
            "attempt": row.attempt,
            "action": action,
            "reason": note,
            "proposed_by": _id(staff),
            "proposed_at": clock.now(),
        },
    )
    await service._audit(
        session,
        staff,
        "change",
        "migration.carry_resolution_proposed",
        "migration_carry_resolution",
        resolution_id,
        clock,
        details={"switch_id": switch_id, "action": action, "attempt": int(row.attempt)},
    )
    return await _by_id(session, resolution_id)


async def decide_resolution(
    session: AsyncSession,
    staff: Actor,
    clock: Clock,
    resolution_id: str,
    *,
    approve: bool,
    reason: str | None = None,
) -> ResolutionView:
    """Approve or reject a resolution, as a member of staff other than its proposer.

    An approval queues the carry-over again: it retries the client, or, once nobody is waiting,
    removes the sealed sheets. A rejection needs a reason.
    """
    _staff_only(staff)
    note = " ".join((reason or "").split()) or None
    if note is not None and len(note) > MAX_REASON_LENGTH:
        raise _invalid(["reason"], "invalid_reason")
    if not approve and note is None:
        raise _invalid(["reason"], "invalid_reason")
    await session.execute(_LOCK)
    resolution = await _by_id(session, resolution_id)
    if resolution.proposed_by == _id(staff):
        raise ApplicationError("forbidden")
    if resolution.state != "proposed":
        raise ApplicationError("conflict")

    outcome: ResolutionState = "approved" if approve else "rejected"
    now = clock.now()
    await session.execute(
        _INSERT_DECISION,
        {
            "id": resolution_id,
            "outcome": outcome,
            "reason": note,
            "decided_by": _id(staff),
            "decided_at": now,
        },
    )
    if approve:
        await enqueue(
            session,
            JobRequest(
                kind=switch.CARRY_OVER,
                idempotency_key=f"{resolution.switch_id}:{resolution_id}",
                payload={"switch_id": resolution.switch_id},
                max_age=timedelta(days=7),
            ),
            now=now,
        )
    await service._audit(
        session,
        staff,
        "approval",
        f"migration.carry_resolution_{outcome}",
        "migration_carry_resolution",
        resolution_id,
        clock,
        reason=note or resolution.reason,
        details={"switch_id": resolution.switch_id, "action": resolution.action},
    )
    return await _by_id(session, resolution_id)


# Internals --------------------------------------------------------------------------------------


def _state(row: Row[Any]) -> NotCarriedState | None:
    """Where a latest note stands, or None once the client is closed or carried over."""
    if row.resolution_id is None:
        return "noted"
    if row.outcome is None:
        return "retry_proposed" if row.action == "retry" else "close_proposed"
    if row.action == "close" or row.claimed:
        return None
    return "retrying"


async def _by_id(session: AsyncSession, resolution_id: str) -> ResolutionView:
    row = (await session.execute(_RESOLUTION, {"id": resolution_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return ResolutionView(
        id=row.id,
        switch_id=row.switch_id,
        legacy_client_ref=row.legacy_client_ref,
        attempt=int(row.attempt),
        action=row.action,
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        state=row.outcome or "proposed",
        decided_by=row.decided_by,
        decision_reason=row.decision_reason,
    )


def _reason(reason: str) -> str:
    note = " ".join(reason.split())
    if not note or len(note) > MAX_REASON_LENGTH:
        raise _invalid(["reason"], "invalid_reason")
    return note


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def _invalid(path: list[str | int], code: str) -> ApplicationError:
    return ApplicationError("validation_failed", fields=[FieldProblem(field_path=path, code=code)])
