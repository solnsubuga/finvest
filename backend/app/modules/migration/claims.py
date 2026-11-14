"""Claims of migrated accounts: who has a live link, how far each client has got, and the
figures on the console. The rules are in `domain/claim.py`; PostgreSQL holds the same steps.

The switch creates one claim per migrated client and sends the first links. Sending the message
itself is the notifications module's job: `send_link` returns the token for exactly one message
and keeps only its hash. The sign-in module creates the client's password and session; this
module only records that the link was used and the claim moved on.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.migration.domain.claim import (
    LINK_LIFETIME,
    REMINDER_EVERY,
    ClaimStatus,
    IdentityStep,
    Link,
    LinkProblem,
    Progress,
    can_move,
    hash_token,
    identity_step,
    link_problem,
    problem_text,
    progress,
    reminder_due,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id


@dataclass(frozen=True, slots=True)
class ClaimView:
    client_id: str
    legacy_client_ref: str
    status: ClaimStatus
    identity_step: IdentityStep
    reminders_sent: int
    problem_text: str | None


@dataclass(frozen=True, slots=True)
class OpenedLink:
    """A link that opens: the claim it starts. Nothing about the person."""

    link_id: str
    client_id: str


class LinkRefused(ApplicationError):
    """A link that does not open. The visitor sees only `not_found`; `problem` is for staff."""

    def __init__(self, problem: LinkProblem) -> None:
        super().__init__("not_found")
        self.problem = problem


_INSERT_CLAIM = text(
    "INSERT INTO migration_claims (client_id, legacy_client_ref, needs_id_check, status, "
    "created_at, updated_at) VALUES (:client_id, :ref, :needs_id_check, 'not_invited', :now, :now)"
)
_CLAIM = text(
    "SELECT client_id, legacy_client_ref, needs_id_check, status, reminders_sent, problem_text "
    "FROM migration_claims WHERE client_id = :client_id FOR UPDATE"
)
_MOVE = text(
    "UPDATE migration_claims SET status = :status, problem_text = :problem, "
    "reminders_sent = :reminders, updated_at = :now WHERE client_id = :client_id"
)
# A selfie that does not match sends the client to the full ID check: the one change the database
# allows to `needs_id_check`, made with the step back (migration 0078).
_TO_FULL_CHECK = text(
    "UPDATE migration_claims SET status = 'signed_in', needs_id_check = true, "
    "updated_at = :now WHERE client_id = :client_id AND status = 'identity_submitted' "
    "AND NOT needs_id_check"
)
_CANCEL_LIVE = text(
    "UPDATE migration_claim_links SET cancelled_at = :now "
    "WHERE client_id = :client_id AND used_at IS NULL AND cancelled_at IS NULL"
)
_INSERT_LINK = text(
    "INSERT INTO migration_claim_links (id, client_id, token_hash, sent_at) "
    "VALUES (:id, :client_id, :token_hash, :now)"
)
_LOOK_UP = text(
    "SELECT link_id, client_id, sent_at, used_at, cancelled_at, status "
    "FROM finvest_migration_claim_link(:hash)"
)
# The link's age is checked again here: it may have expired since it was opened.
_USE_LINK = text(
    "UPDATE migration_claim_links SET used_at = :now "
    "WHERE id = :id AND client_id = :client_id AND used_at IS NULL AND cancelled_at IS NULL "
    "AND sent_at > :earliest"
)
_LINK_STATE = text(
    "SELECT used_at, cancelled_at FROM migration_claim_links "
    "WHERE id = :id AND client_id = :client_id"
)
_COUNTS = text("SELECT status, count(*) AS n FROM migration_claims GROUP BY status")
_INVITED = text(
    "SELECT c.client_id, c.reminders_sent, max(l.sent_at) AS last_sent_at "
    "FROM migration_claims AS c JOIN migration_claim_links AS l ON l.client_id = c.client_id "
    "WHERE c.status = 'invited' GROUP BY c.client_id, c.reminders_sent "
    "HAVING max(l.sent_at) <= :before ORDER BY max(l.sent_at), c.client_id LIMIT :limit"
)


async def create_claim(
    session: AsyncSession,
    system: Actor,
    clock: Clock,
    *,
    client_id: str,
    legacy_client_ref: str,
    needs_id_check: bool,
) -> ClaimView:
    """One claim for a client the switch has just created. Nobody is invited yet."""
    _only(system, "system")
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_CLAIM,
                {
                    "client_id": client_id,
                    "ref": legacy_client_ref,
                    "needs_id_check": needs_id_check,
                    "now": clock.now(),
                },
            )
    except IntegrityError:
        raise ApplicationError("conflict") from None
    return ClaimView(
        client_id,
        legacy_client_ref,
        ClaimStatus.NOT_INVITED,
        identity_step(needs_id_check=needs_id_check),
        0,
        None,
    )


async def send_link(session: AsyncSession, actor: Actor, clock: Clock, client_id: str) -> str:
    """A fresh one-time link for a client who has not claimed yet, cancelling any before it.

    The system sends the first link and the weekly reminders; staff send one by hand, for
    example after checking a changed phone number. Returns the token for the one message
    that carries it. Only its hash is kept.
    """
    if actor.kind not in {"system", "staff"}:
        raise ApplicationError("forbidden")
    claim = await _claim(session, client_id)
    if claim.status not in {ClaimStatus.NOT_INVITED, ClaimStatus.INVITED}:
        raise ApplicationError("conflict")
    reminders = claim.reminders_sent
    if claim.status is ClaimStatus.INVITED and actor.kind == "system":
        # A reminder: the domain rule decides whether one is due, and caps how many.
        last = await _last_sent_at(session, client_id)
        if last is None or not reminder_due(
            claim.status, last_sent_at=last, reminders_sent=reminders, now=clock.now()
        ):
            raise ApplicationError("conflict")
        reminders += 1
    token = secrets.token_urlsafe(32)
    now = clock.now()
    await session.execute(_CANCEL_LIVE, {"client_id": client_id, "now": now})
    await session.execute(
        _INSERT_LINK,
        {"id": push_id(), "client_id": client_id, "token_hash": hash_token(token), "now": now},
    )
    if claim.status is ClaimStatus.NOT_INVITED:
        await _move(session, actor, clock, claim, ClaimStatus.INVITED)
    else:
        await session.execute(
            _MOVE,
            {
                "client_id": client_id,
                "status": claim.status.value,
                "problem": None,
                "reminders": reminders,
                "now": now,
            },
        )
    await _audit(session, actor, "change", "migration.claim_link_sent", client_id, clock)
    return token


async def open_link(session: AsyncSession, clock: Clock, token: str) -> OpenedLink:
    """What a link opens, for a visitor who is not signed in. Changes nothing."""
    row = (await session.execute(_LOOK_UP, {"hash": hash_token(token)})).one_or_none()
    link = None
    status = ClaimStatus.NOT_INVITED
    if row is not None:
        link = Link(hash_token(token), row.sent_at, row.used_at, row.cancelled_at)
        status = ClaimStatus(row.status)
    problem = link_problem(link, token, status, clock.now())
    if problem is not None or row is None:
        raise LinkRefused(problem or LinkProblem.NOT_FOUND)
    return OpenedLink(row.link_id, row.client_id)


async def record_signed_in(
    session: AsyncSession, client: Actor, clock: Clock, link: OpenedLink
) -> ClaimView:
    """The client confirmed the phone on file and set a password: the link is spent.

    Safe to repeat with the same link: signing in commits in identity's own transaction, so
    this may run again after it failed. A link that expired since it was opened is refused.
    """
    _only(client, "client")
    if client.id != link.client_id:
        raise ApplicationError("forbidden")
    claim = await _claim(session, link.client_id)
    now = clock.now()
    ids = {"id": link.link_id, "client_id": link.client_id}
    used = await session.execute(_USE_LINK, ids | {"now": now, "earliest": now - LINK_LIFETIME})
    if used.rowcount == 1:  # type: ignore[attr-defined]
        return await _move(session, client, clock, claim, ClaimStatus.SIGNED_IN)
    # Read again: another tab may have committed while this one waited on the row.
    claim = await _claim(session, link.client_id)
    state = (await session.execute(_LINK_STATE, ids)).one_or_none()
    if state is not None and state.used_at is not None and claim.status is not ClaimStatus.INVITED:
        # Already recorded with this link.
        return claim
    if state is not None and state.used_at is None and state.cancelled_at is None:
        raise LinkRefused(LinkProblem.EXPIRED)
    raise ApplicationError("conflict")


async def submit_identity(session: AsyncSession, client: Actor, clock: Clock) -> ClaimView:
    """The selfie or the full ID check is with the identity checks module."""
    _only(client, "client")
    claim = await _claim(session, _id(client))
    return await _move(session, client, clock, claim, ClaimStatus.IDENTITY_SUBMITTED)


async def record_identity_result(
    session: AsyncSession, system: Actor, clock: Clock, client_id: str, *, matched: bool
) -> ClaimView:
    """The identity checks module's answer. A failed check sends the client back a step.

    A selfie that staff found does not match the identity carried over is final in the identity
    checks module, so the client is sent to the full ID check instead (the owner's choice,
    2026-11-14). The identity checks module retires the carried-over check in the same
    decision, so the client starts that check as a new client would.
    """
    _only(system, "system")
    claim = await _claim(session, client_id)
    if matched or claim.identity_step is IdentityStep.FULL_ID_CHECK:
        target = ClaimStatus.IDENTITY_CONFIRMED if matched else ClaimStatus.SIGNED_IN
        return await _move(session, system, clock, claim, target)
    if not can_move(system.kind, claim.status, ClaimStatus.SIGNED_IN):
        raise ApplicationError("conflict")
    await session.execute(_TO_FULL_CHECK, {"client_id": client_id, "now": clock.now()})
    await _audit(
        session, system, "change", "migration.claim_sent_to_full_id_check", client_id, clock
    )
    return replace(claim, status=ClaimStatus.SIGNED_IN, identity_step=IdentityStep.FULL_ID_CHECK)


async def confirm_investments(session: AsyncSession, client: Actor, clock: Clock) -> ClaimView:
    """The client says the investments shown are right. Final."""
    _only(client, "client")
    claim = await _claim(session, _id(client))
    return await _move(session, client, clock, claim, ClaimStatus.CONFIRMED)


async def report_problem(
    session: AsyncSession, client: Actor, clock: Clock, *, description: str
) -> ClaimView:
    """The client says something is wrong. Staff look into it; the money keeps earning."""
    _only(client, "client")
    try:
        cleaned = problem_text(description)
    except ValueError:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["description"], code="invalid")]
        ) from None
    claim = await _claim(session, _id(client))
    return await _move(session, client, clock, claim, ClaimStatus.PROBLEM_REPORTED, cleaned)


async def close_problem(
    session: AsyncSession, staff: Actor, clock: Clock, client_id: str, *, reason: str
) -> ClaimView:
    """Staff have dealt with a reported problem. The client is asked to confirm again."""
    _only(staff, "staff")
    if not reason.strip():
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["reason"], code="invalid")]
        )
    claim = await _claim(session, client_id)
    return await _move(session, staff, clock, claim, ClaimStatus.IDENTITY_CONFIRMED, reason=reason)


async def claim_progress(session: AsyncSession) -> Progress:
    """The figures for the console's migration screen."""
    rows = (await session.execute(_COUNTS)).all()
    return progress({ClaimStatus(row.status): int(row.n) for row in rows})


async def reminders_due(session: AsyncSession, clock: Clock, *, limit: int = 500) -> list[str]:
    """Clients who were sent a link a week or more ago and have not used it, oldest first."""
    now = clock.now()
    rows = (await session.execute(_INVITED, {"before": now - REMINDER_EVERY, "limit": limit})).all()
    return [
        row.client_id
        for row in rows
        if reminder_due(
            ClaimStatus.INVITED,
            last_sent_at=row.last_sent_at,
            reminders_sent=row.reminders_sent,
            now=now,
        )
    ]


async def claim_for(session: AsyncSession, client_id: str) -> ClaimView | None:
    row = (await session.execute(_CLAIM, {"client_id": client_id})).one_or_none()
    return None if row is None else _view(row)


# Internals --------------------------------------------------------------------------------------


def _only(actor: Actor, kind: str) -> None:
    if actor.kind != kind:
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def _view(row: Row[Any]) -> ClaimView:
    return ClaimView(
        row.client_id,
        row.legacy_client_ref,
        ClaimStatus(row.status),
        identity_step(needs_id_check=row.needs_id_check),
        row.reminders_sent,
        row.problem_text,
    )


async def _claim(session: AsyncSession, client_id: str) -> ClaimView:
    row = (await session.execute(_CLAIM, {"client_id": client_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def _last_sent_at(session: AsyncSession, client_id: str) -> datetime | None:
    value = (
        await session.execute(
            text("SELECT max(sent_at) FROM migration_claim_links WHERE client_id = :client_id"),
            {"client_id": client_id},
        )
    ).scalar_one()
    return value  # type: ignore[no-any-return]


async def _move(
    session: AsyncSession,
    actor: Actor,
    clock: Clock,
    claim: ClaimView,
    target: ClaimStatus,
    problem: str | None = None,
    *,
    reason: str | None = None,
) -> ClaimView:
    if not can_move(actor.kind, claim.status, target):
        raise ApplicationError("conflict")
    await session.execute(
        _MOVE,
        {
            "client_id": claim.client_id,
            "status": target.value,
            "problem": problem,
            "reminders": claim.reminders_sent,
            "now": clock.now(),
        },
    )
    await _audit(
        session,
        actor,
        "approval" if reason else "change",
        f"migration.claim_{target.value}",
        claim.client_id,
        clock,
        reason=reason,
    )
    return ClaimView(
        claim.client_id,
        claim.legacy_client_ref,
        target,
        claim.identity_step,
        claim.reminders_sent,
        problem,
    )


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    client_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type="migration_claim",
            resource_id=client_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
        ),
        clock,
    )
