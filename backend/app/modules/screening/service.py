"""Screens, what they found, and the compliance cases staff close with two people.

Nothing here talks to the provider. Asking for a screen saves a pending run and a job in the
caller's transaction; the worker (`jobs.py`) screens the client with no transaction open and
records the answer through `record_result` or `record_failure`. Migration 0180 holds the rules
PostgreSQL enforces: only the worker finishes a screen or opens a case, a case closes only with
a proposal approved in the same transaction, and the approver is never the proposer.

Logs carry IDs and codes only: never a name, a match or a client's details.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import CursorResult, Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.screening.domain.cases import (
    MAX_TEXT,
    SETTLES_ENTITY,
    CaseOutcome,
    CaseSource,
    CaseStatus,
    Decision,
    ProposalStatus,
    may_decide,
    outcome_allowed,
)
from app.modules.screening.domain.provider import Match, MatchCategory, ScreeningResult
from app.modules.screening.domain.standing import (
    GIVE_UP_AFTER,
    RESCREEN_EVERY,
    LastRun,
    Outcome,
    Reason,
    RunStatus,
    standing,
    what_was_found,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

SCREEN_KIND = "screening.screen_client"
RESCREEN_KIND = "screening.rescreen_due"
SCREEN_ATTEMPTS = 8
RESOURCE = "compliance_case"
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    client_id: str
    reason: Reason
    requested_by: str | None
    requested_at: datetime
    status: RunStatus
    finished_at: datetime | None
    provider: str | None
    provider_reference: str | None
    failure_code: str | None
    match_count: int | None
    new_match_count: int | None


@dataclass(frozen=True, slots=True)
class FoundMatch:
    match: Match
    # Already ruled out or accepted for this client on an earlier case.
    known: bool


@dataclass(frozen=True, slots=True)
class ScreeningStanding:
    """Where a client stands with screening, for the identity check review and for payouts."""

    outcome: Outcome
    # The last finished screen, if any.
    screened_at: datetime | None
    # The open screening case, or the one that confirmed a sanctions match.
    case_id: str | None
    # Staff accepted the client as a politically exposed person on a closed case.
    pep: bool


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    client_id: str
    source: CaseSource
    code: str
    run_id: str | None
    detail: dict[str, Any]
    opened_at: datetime
    status: CaseStatus
    outcome: CaseOutcome | None
    closed_at: datetime | None
    # The open proposal, if a first person has made one.
    waiting: Proposal | None


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    case_id: str
    outcome: CaseOutcome
    reason: str
    proposed_by: str
    proposed_by_name: str
    proposed_at: datetime
    status: ProposalStatus
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None


@dataclass(frozen=True, slots=True)
class CaseDetail:
    case: Case
    # For a screening case: the screen and everything it found.
    run: Run | None
    matches: tuple[FoundMatch, ...]
    proposals: tuple[Proposal, ...]


@dataclass(frozen=True, slots=True)
class Summary:
    open_cases: int
    open_screening_cases: int
    open_monitoring_cases: int
    waiting_for_second_person: int
    # Finished screens in the last 30 days, and those of them that could not be run.
    screened_recently: int
    unavailable_recently: int
    not_run_recently: int
    pending: int
    last_screened_at: datetime | None


_RUN_COLUMNS = (
    "id, client_id, reason, requested_by, requested_at, status, finished_at, provider, "
    "provider_reference, failure_code, match_count, new_match_count"
)
_RUN = text(f"SELECT {_RUN_COLUMNS} FROM screening_runs WHERE id = :id")  # noqa: S608 - constant column list
_LOCK_RUN = text(
    f"SELECT {_RUN_COLUMNS} FROM screening_runs "  # noqa: S608 - constant column list
    "WHERE id = :id FOR UPDATE"
)
_CLIENT_RUNS = text(
    f"SELECT {_RUN_COLUMNS} FROM screening_runs WHERE client_id = :client_id "  # noqa: S608 - constant column list
    "ORDER BY requested_at DESC, id DESC LIMIT :limit"
)
_INSERT_RUN = text(
    "INSERT INTO screening_runs (id, client_id, reason, requested_by, requested_at, "
    "idempotency_key) VALUES (:id, :client_id, :reason, :requested_by, :now, :key)"
)
_FINISH = text(
    "UPDATE screening_runs SET status = :status, finished_at = :now, provider = :provider, "
    "provider_reference = :reference, failure_code = :failure, match_count = :matches, "
    "new_match_count = :new WHERE id = :id AND status = 'pending'"
)
_INSERT_MATCH = text(
    "INSERT INTO screening_matches (run_id, position, entity_id, name, category, strength, "
    "sources, known) VALUES (:run_id, :position, :entity_id, :name, :category, :strength, "
    "CAST(:sources AS text[]), :known)"
)
_MATCHES = text(
    "SELECT entity_id, name, category, strength, sources, known FROM screening_matches "
    "WHERE run_id = :run_id ORDER BY position"
)
# Entities staff already decided for this client: not this person, or an accepted PEP.
# Entities already on a case for this client: ruled out or accepted, confirmed as sanctioned,
# or still open. Finding one again opens no new case: an open case already holds the client,
# and a confirmed match holds them for good.
_KNOWN = text(
    "SELECT DISTINCT m.entity_id FROM screening_matches m "
    "JOIN compliance_cases c ON c.run_id = m.run_id "
    "WHERE c.client_id = :client_id AND m.known = false AND (c.status = 'open' "
    "OR c.outcome = ANY(CAST(:settled AS text[])) OR c.outcome = 'sanctioned')"
)
_STALE = text(
    "SELECT id FROM screening_runs WHERE status = 'pending' AND requested_at < :before "
    "ORDER BY requested_at LIMIT :limit FOR UPDATE SKIP LOCKED"
)
_LAST_RUNS = text(
    "SELECT DISTINCT ON (client_id) client_id, status, requested_at, finished_at "
    "FROM screening_runs WHERE client_id = ANY(CAST(:ids AS text[])) "
    "ORDER BY client_id, requested_at DESC, id DESC"
)
_LAST_FINISHED = text(
    "SELECT client_id, max(finished_at) AS finished_at, bool_or(status = 'pending') AS pending "
    "FROM screening_runs WHERE client_id = ANY(CAST(:ids AS text[])) GROUP BY client_id"
)
_CASE_FLAGS = text(
    "SELECT client_id, "
    "(array_agg(id ORDER BY opened_at DESC) FILTER (WHERE source = 'screening' "
    "AND status = 'open'))[1] AS open_case, "
    "(array_agg(id ORDER BY opened_at DESC) FILTER (WHERE source = 'screening' "
    "AND outcome = 'sanctioned'))[1] AS sanctioned_case, "
    "bool_or(source = 'screening' AND outcome = 'pep_accepted') AS pep "
    "FROM compliance_cases WHERE client_id = ANY(CAST(:ids AS text[])) GROUP BY client_id"
)
_CASE_COLUMNS = (
    "c.id, c.client_id, c.source, c.code, c.run_id, c.detail, c.opened_at, c.status, "
    "c.outcome, c.closed_at"
)
_PROPOSAL_COLUMNS = (
    "p.id, p.case_id, p.outcome, p.reason, p.proposed_by, ps.name AS proposed_by_name, "
    "p.proposed_at, p.status, p.decided_by, ds.name AS decided_by_name, p.decided_at, "
    "p.decision_note"
)
_PROPOSALS_FROM = (
    "FROM compliance_case_proposals p JOIN identity_staff ps ON ps.id = p.proposed_by "
    "LEFT JOIN identity_staff ds ON ds.id = p.decided_by "
)
_CASE = text(f"SELECT {_CASE_COLUMNS} FROM compliance_cases c WHERE c.id = :id")  # noqa: S608 - constant column list
_LOCK_CASE = text("SELECT status FROM compliance_cases WHERE id = :id FOR UPDATE")
_CASE_PROPOSALS = text(
    f"SELECT {_PROPOSAL_COLUMNS} {_PROPOSALS_FROM} WHERE p.case_id = ANY(CAST(:ids AS text[])) "
    "ORDER BY p.proposed_at, p.id"
)
_PROPOSAL = text(f"SELECT {_PROPOSAL_COLUMNS} {_PROPOSALS_FROM} WHERE p.id = :id")
_LOCK_PROPOSAL = text(
    "SELECT case_id FROM compliance_case_proposals WHERE id = :id AND status = 'proposed' "
    "FOR UPDATE"
)
_WAITING = text(
    f"SELECT {_PROPOSAL_COLUMNS} {_PROPOSALS_FROM} WHERE p.status = 'proposed' "
    "ORDER BY p.proposed_at, p.id"
)
_SUMMARY = text(
    "SELECT "
    "(SELECT count(*) FROM compliance_cases WHERE status = 'open') AS open_cases, "
    "(SELECT count(*) FROM compliance_cases WHERE status = 'open' AND source = 'screening') "
    "AS open_screening, "
    "(SELECT count(*) FROM compliance_cases WHERE status = 'open' AND source = 'monitoring') "
    "AS open_monitoring, "
    "(SELECT count(*) FROM compliance_case_proposals WHERE status = 'proposed') AS waiting, "
    "(SELECT count(*) FROM screening_runs WHERE status <> 'pending' AND finished_at >= :since) "
    "AS screened, "
    "(SELECT count(*) FROM screening_runs WHERE status = 'unavailable' "
    "AND finished_at >= :since) AS unavailable, "
    "(SELECT count(*) FROM screening_runs WHERE status IN ('not_run', 'no_subject') "
    "AND finished_at >= :since) AS not_run, "
    "(SELECT count(*) FROM screening_runs WHERE status = 'pending') AS pending, "
    "(SELECT max(finished_at) FROM screening_runs WHERE status IN ('clear', 'possible_match')) "
    "AS last_screened_at"
)


# Asking for a screen.


async def request_screening(
    session: AsyncSession,
    *,
    client_id: str,
    reason: Reason,
    idempotency_key: str,
    now: datetime,
    requested_by: str | None = None,
) -> str | None:
    """Ask the worker to screen the client, in the caller's transaction.

    Returns the new run's ID, or None when a screen was already asked for under this key: the
    same key asks once, however often it is repeated. `requested_by` is the member of staff who
    asked, for a screen asked for by hand.
    """
    if (reason is Reason.STAFF) != (requested_by is not None):
        raise ValueError("A screen asked for by hand names the member of staff")
    run_id = push_id()
    try:
        # Not ON CONFLICT: that needs a client to read runs, and a client reads none. A repeat
        # key is the unique constraint's refusal, kept inside a savepoint.
        async with session.begin_nested():
            await session.execute(
                _INSERT_RUN,
                {
                    "id": run_id,
                    "client_id": client_id,
                    "reason": reason.value,
                    "requested_by": requested_by,
                    "now": now,
                    "key": idempotency_key,
                },
            )
    except IntegrityError as error:
        if "uq_screening_runs_idempotency_key" not in str(error.orig):
            raise
        return None
    await enqueue(
        session,
        JobRequest(
            kind=SCREEN_KIND,
            idempotency_key=run_id,
            payload={"run_id": run_id},
            max_attempts=SCREEN_ATTEMPTS,
            max_age=GIVE_UP_AFTER,
        ),
        now=now,
    )
    logger.info("screening_requested", run_id=run_id, reason=reason.value)
    return run_id


async def request_by_staff(
    session: AsyncSession, staff: Actor, client_id: str, idempotency_key: str, clock: Clock
) -> Run:
    """A member of staff asks for a fresh screen now, for example after a client's name changed.

    One person's step: it only reads lists, so it needs no second person.
    """
    acting = _staff_id(staff)
    run_id = await request_screening(
        session,
        client_id=client_id,
        reason=Reason.STAFF,
        idempotency_key=f"staff:{acting}:{idempotency_key}",
        now=clock.now(),
        requested_by=acting,
    )
    if run_id is None:
        found = await session.scalar(
            text("SELECT id FROM screening_runs WHERE idempotency_key = :key"),
            {"key": f"staff:{acting}:{idempotency_key}"},
        )
        if found is None:
            raise ApplicationError("conflict")
        run_id = str(found)
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="screening.rescreen_requested",
            resource_type="screening_run",
            resource_id=run_id,
            actor=AuditActor.staff(acting),
            details={"client_id": client_id},
        ),
        clock,
    )
    return await _run(session, run_id)


# The worker's side.


async def pending_run(session: AsyncSession, run_id: str) -> Run | None:
    """The run, if it is still waiting to be screened."""
    run = await _maybe_run(session, run_id)
    return run if run is not None and run.status is RunStatus.PENDING else None


async def record_result(
    session: AsyncSession,
    run_id: str,
    *,
    provider: str,
    result: ScreeningResult,
    clock: Clock,
) -> Run | None:
    """Record what the provider found. A new match opens a case for staff.

    None if the run was already finished, for example given up on while the provider was slow.
    """
    row = (await session.execute(_LOCK_RUN, {"id": run_id})).one_or_none()
    if row is None or row.status != RunStatus.PENDING:
        return None
    known = frozenset(
        str(entity)
        for entity in (
            await session.execute(
                _KNOWN,
                {
                    "client_id": row.client_id,
                    "settled": [outcome.value for outcome in SETTLES_ENTITY],
                },
            )
        ).scalars()
    )
    found = what_was_found(result.matches, known)
    now = clock.now()
    await session.execute(
        _FINISH,
        {
            "id": run_id,
            "status": found.status.value,
            "now": now,
            "provider": provider,
            "reference": result.reference,
            "failure": None,
            "matches": len(result.matches),
            "new": len(found.new),
        },
    )
    for position, match in enumerate(result.matches):
        await session.execute(
            _INSERT_MATCH,
            {
                "run_id": run_id,
                "position": position,
                "entity_id": match.entity_id,
                "name": match.name[:300],
                "category": match.category.value,
                "strength": match.strength,
                "sources": [source[:200] for source in match.sources],
                "known": match.entity_id in known,
            },
        )
    if found.new:
        case_id = await _open_case(
            session,
            client_id=row.client_id,
            source=CaseSource.SCREENING,
            code="possible_match",
            run_id=run_id,
            clock=clock,
        )
        logger.info("screening_case_opened", run_id=run_id, case_id=case_id)
    logger.info("screening_finished", run_id=run_id, status=found.status.value)
    return await _run(session, run_id)


async def record_failure(
    session: AsyncSession,
    run_id: str,
    status: RunStatus,
    *,
    provider: str | None,
    failure_code: str | None,
    now: datetime,
) -> Run | None:
    """Finish a run with nothing screened: no provider, no subject, or the provider failed."""
    if status not in (RunStatus.UNAVAILABLE, RunStatus.NOT_RUN, RunStatus.NO_SUBJECT):
        raise ValueError("Only a screen that found nothing out fails")
    if (status is RunStatus.UNAVAILABLE) != (failure_code is not None):
        raise ValueError("An unavailable screen says why")
    finished: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        _FINISH,
        {
            "id": run_id,
            "status": status.value,
            "now": now,
            "provider": provider,
            "reference": None,
            "failure": failure_code,
            "matches": None,
            "new": None,
        },
    )
    if finished.rowcount != 1:
        return None
    logger.info("screening_finished", run_id=run_id, status=status.value, error_code=failure_code)
    return await _run(session, run_id)


async def give_up_on_stale(session: AsyncSession, now: datetime, *, limit: int = 500) -> int:
    """Record screens the provider never answered as unavailable, so staff see them."""
    stale = list(
        (await session.execute(_STALE, {"before": now - GIVE_UP_AFTER, "limit": limit})).scalars()
    )
    for run_id in stale:
        await record_failure(
            session,
            str(run_id),
            RunStatus.UNAVAILABLE,
            provider=None,
            failure_code="provider_no_answer",
            now=now,
        )
    return len(stale)


@dataclass(frozen=True, slots=True)
class LastScreened:
    finished_at: datetime | None
    pending: bool


async def last_screened(
    session: AsyncSession, client_ids: Sequence[str]
) -> dict[str, LastScreened]:
    """When each client last finished a screen, and whether one is waiting. Unscreened left out."""
    if not client_ids:
        return {}
    rows = await session.execute(_LAST_FINISHED, {"ids": list(client_ids)})
    return {
        row.client_id: LastScreened(finished_at=row.finished_at, pending=bool(row.pending))
        for row in rows
    }


# Standing.


async def standings(
    session: AsyncSession, client_ids: Sequence[str]
) -> dict[str, ScreeningStanding]:
    """Where each client stands, in two queries. A client never screened is left out."""
    ids = list(dict.fromkeys(client_ids))
    if not ids:
        return {}
    last = {row.client_id: row for row in await session.execute(_LAST_RUNS, {"ids": ids})}
    flags = {row.client_id: row for row in await session.execute(_CASE_FLAGS, {"ids": ids})}
    finished = await last_screened(session, ids)
    found: dict[str, ScreeningStanding] = {}
    for client_id in ids:
        run = last.get(client_id)
        flag = flags.get(client_id)
        outcome = standing(
            None
            if run is None
            else LastRun(RunStatus(run.status), run.requested_at, run.finished_at),
            open_case=flag is not None and flag.open_case is not None,
            sanctioned=flag is not None and flag.sanctioned_case is not None,
        )
        if outcome is None:
            continue
        screened = finished.get(client_id)
        found[client_id] = ScreeningStanding(
            outcome=outcome,
            screened_at=screened.finished_at if screened else None,
            case_id=None if flag is None else (flag.sanctioned_case or flag.open_case),
            pep=bool(flag is not None and flag.pep),
        )
    return found


async def latest_outcome(session: AsyncSession, client_id: str) -> ScreeningStanding | None:
    """Where the client stands with screening. None if they were never screened."""
    return (await standings(session, [client_id])).get(client_id)


# Cases.


async def _open_case(
    session: AsyncSession,
    *,
    client_id: str,
    source: CaseSource,
    code: str,
    clock: Clock,
    run_id: str | None = None,
    dedupe_key: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str | None:
    """Open a case in the worker's transaction. None if this alert was already raised."""
    case_id = push_id(clock)
    opened: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        text(
            "INSERT INTO compliance_cases (id, client_id, source, code, run_id, dedupe_key, "
            "detail, opened_at) VALUES (:id, :client_id, :source, :code, :run_id, :dedupe_key, "
            "CAST(:detail AS jsonb), :now) ON CONFLICT DO NOTHING"
        ),
        {
            "id": case_id,
            "client_id": client_id,
            "source": source.value,
            "code": code,
            "run_id": run_id,
            "dedupe_key": dedupe_key,
            "detail": _json(detail or {}),
            "now": clock.now(),
        },
    )
    if opened.rowcount != 1:
        return None
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=f"compliance.case_opened.{source.value}",
            resource_type=RESOURCE,
            resource_id=case_id,
            actor=AuditActor.system(),
            details={"client_id": client_id, "code": code},
        ),
        clock,
    )
    return case_id


async def list_cases(
    session: AsyncSession,
    *,
    status: CaseStatus | None = None,
    source: CaseSource | None = None,
    client_id: str | None = None,
    limit: int = 100,
) -> list[Case]:
    """Cases, open ones oldest first (the queue) and closed ones newest first."""
    order = "c.opened_at, c.id" if status is CaseStatus.OPEN else "c.opened_at DESC, c.id DESC"
    rows = (
        await session.execute(
            text(
                f"SELECT {_CASE_COLUMNS} FROM compliance_cases c WHERE "  # noqa: S608 - constant column list
                "(CAST(:status AS text) IS NULL OR c.status = :status) "
                "AND (CAST(:source AS text) IS NULL OR c.source = :source) "
                "AND (CAST(:client_id AS text) IS NULL OR c.client_id = :client_id) "
                f"ORDER BY {order} LIMIT :limit"
            ),
            {
                "status": status.value if status else None,
                "source": source.value if source else None,
                "client_id": client_id,
                "limit": max(1, min(limit, 200)),
            },
        )
    ).all()
    waiting = await _proposals_for(session, [row.id for row in rows])
    return [
        _case(
            row,
            next((p for p in waiting.get(row.id, ()) if p.status is ProposalStatus.PROPOSED), None),
        )
        for row in rows
    ]


async def get_case(session: AsyncSession, case_id: str) -> CaseDetail:
    row = (await session.execute(_CASE, {"id": case_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    proposals = (await _proposals_for(session, [case_id])).get(case_id, ())
    case = _case(row, next((p for p in proposals if p.status is ProposalStatus.PROPOSED), None))
    run = await _run(session, case.run_id) if case.run_id else None
    matches: tuple[FoundMatch, ...] = ()
    if run is not None:
        matches = tuple(
            FoundMatch(
                Match(
                    entity_id=match.entity_id,
                    name=match.name,
                    category=MatchCategory(match.category),
                    strength=match.strength,
                    sources=tuple(match.sources),
                ),
                known=match.known,
            )
            for match in await session.execute(_MATCHES, {"run_id": run.id})
        )
    return CaseDetail(case=case, run=run, matches=matches, proposals=tuple(proposals))


async def runs_for_client(session: AsyncSession, client_id: str, *, limit: int = 50) -> list[Run]:
    rows = await session.execute(
        _CLIENT_RUNS, {"client_id": client_id, "limit": max(1, min(limit, 200))}
    )
    return [_run_view(row) for row in rows]


async def waiting_proposals(session: AsyncSession) -> list[Proposal]:
    """Every proposal waiting for a second person, oldest first, for the approvals inbox."""
    return [_proposal(row) for row in await session.execute(_WAITING)]


async def summary(session: AsyncSession, now: datetime) -> Summary:
    row = (await session.execute(_SUMMARY, {"since": now - RESCREEN_EVERY})).one()
    return Summary(
        open_cases=row.open_cases,
        open_screening_cases=row.open_screening,
        open_monitoring_cases=row.open_monitoring,
        waiting_for_second_person=row.waiting,
        screened_recently=row.screened,
        unavailable_recently=row.unavailable,
        not_run_recently=row.not_run,
        pending=row.pending,
        last_screened_at=row.last_screened_at,
    )


async def propose(
    session: AsyncSession,
    staff: Actor,
    case_id: str,
    outcome: CaseOutcome,
    reason: str,
    clock: Clock,
) -> Proposal:
    """Propose how to close an open case. A different member of staff decides."""
    acting = _staff_id(staff)
    reason = _text(reason, "reason")
    status = await session.scalar(_LOCK_CASE, {"id": case_id})
    if status is None:
        raise ApplicationError("not_found")
    if status != CaseStatus.OPEN:
        raise ApplicationError("conflict", fields=[_problem("case_id", "case_closed")])
    case = (await session.execute(_CASE, {"id": case_id})).one()
    if not outcome_allowed(CaseSource(case.source), outcome):
        raise ApplicationError(
            "validation_failed", fields=[_problem("outcome", "outcome_does_not_fit_case")]
        )
    proposal_id = push_id(clock)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO compliance_case_proposals (id, case_id, outcome, reason, "
                    "proposed_by, proposed_at) VALUES (:id, :case_id, :outcome, :reason, "
                    ":staff, :now)"
                ),
                {
                    "id": proposal_id,
                    "case_id": case_id,
                    "outcome": outcome.value,
                    "reason": reason,
                    "staff": acting,
                    "now": clock.now(),
                },
            )
    except IntegrityError:
        # A proposal for this case is already waiting.
        raise ApplicationError(
            "conflict", fields=[_problem("case_id", "proposal_waiting")]
        ) from None
    await _audit(
        session,
        acting,
        "compliance.case_proposed",
        case_id,
        case.client_id,
        reason,
        clock,
        outcome=outcome,
    )
    return await _proposal_by_id(session, proposal_id)


async def decide(
    session: AsyncSession,
    staff: Actor,
    proposal_id: str,
    decision: Decision,
    note: str | None,
    clock: Clock,
) -> Proposal:
    """Approve or send back someone else's proposal, or withdraw your own.

    Approving closes the case with the proposed outcome, in the same transaction. Sending back
    needs a note; the case stays open for a new proposal.
    """
    acting = _staff_id(staff)
    if decision is Decision.SENT_BACK or note is not None:
        note = _text(note or "", "note")
    case_id = await session.scalar(_LOCK_PROPOSAL, {"id": proposal_id})
    if case_id is None:
        exists = await session.scalar(
            text("SELECT 1 FROM compliance_case_proposals WHERE id = :id"), {"id": proposal_id}
        )
        raise ApplicationError("conflict" if exists else "not_found")
    proposal = await _proposal_by_id(session, proposal_id)
    if not may_decide(proposer=proposal.proposed_by, decider=acting, decision=decision):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    now = clock.now()
    await session.scalar(_LOCK_CASE, {"id": case_id})
    await session.execute(
        text(
            "UPDATE compliance_case_proposals SET status = :status, decided_by = :staff, "
            "decided_at = :now, decision_note = :note, decided_in = CASE WHEN "
            "CAST(:status AS varchar) = 'approved' THEN pg_current_xact_id() END WHERE id = :id"
        ),
        {"status": decision.value, "staff": acting, "now": now, "note": note, "id": proposal_id},
    )
    if decision is Decision.APPROVED:
        await session.execute(
            text(
                "UPDATE compliance_cases SET status = 'closed', outcome = :outcome, "
                "closed_at = :now, closed_by_proposal = :proposal WHERE id = :id "
                "AND status = 'open'"
            ),
            {"outcome": proposal.outcome.value, "now": now, "proposal": proposal_id, "id": case_id},
        )
        logger.info("compliance_case_closed", case_id=case_id, outcome=proposal.outcome.value)
    client_id = str(
        await session.scalar(
            text("SELECT client_id FROM compliance_cases WHERE id = :id"), {"id": case_id}
        )
    )
    await _audit(
        session,
        acting,
        f"compliance.case_proposal_{decision.value}",
        str(case_id),
        client_id,
        note or proposal.reason,
        clock,
        outcome=proposal.outcome,
    )
    return await _proposal_by_id(session, proposal_id)


# Helpers.


async def _run(session: AsyncSession, run_id: str) -> Run:
    run = await _maybe_run(session, run_id)
    if run is None:
        raise ApplicationError("not_found")
    return run


async def _maybe_run(session: AsyncSession, run_id: str) -> Run | None:
    row = (await session.execute(_RUN, {"id": run_id})).one_or_none()
    return None if row is None else _run_view(row)


async def _proposals_for(session: AsyncSession, case_ids: list[str]) -> dict[str, list[Proposal]]:
    if not case_ids:
        return {}
    found: dict[str, list[Proposal]] = {}
    for row in await session.execute(_CASE_PROPOSALS, {"ids": case_ids}):
        found.setdefault(row.case_id, []).append(_proposal(row))
    return found


async def _proposal_by_id(session: AsyncSession, proposal_id: str) -> Proposal:
    row = (await session.execute(_PROPOSAL, {"id": proposal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _proposal(row)


async def _audit(
    session: AsyncSession,
    staff_id: str,
    action: str,
    case_id: str,
    client_id: str,
    reason: str,
    clock: Clock,
    *,
    outcome: CaseOutcome,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "sent_back")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=case_id,
            actor=AuditActor.staff(staff_id),
            reason=reason,
            details={"client_id": client_id, "outcome": outcome.value},
        ),
        clock,
    )


def _run_view(row: Row[Any]) -> Run:
    return Run(
        id=row.id,
        client_id=row.client_id,
        reason=Reason(row.reason),
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        status=RunStatus(row.status),
        finished_at=row.finished_at,
        provider=row.provider,
        provider_reference=row.provider_reference,
        failure_code=row.failure_code,
        match_count=row.match_count,
        new_match_count=row.new_match_count,
    )


def _case(row: Row[Any], waiting: Proposal | None) -> Case:
    return Case(
        id=row.id,
        client_id=row.client_id,
        source=CaseSource(row.source),
        code=row.code,
        run_id=row.run_id,
        detail=dict(row.detail or {}),
        opened_at=row.opened_at,
        status=CaseStatus(row.status),
        outcome=CaseOutcome(row.outcome) if row.outcome else None,
        closed_at=row.closed_at,
        waiting=waiting,
    )


def _proposal(row: Row[Any]) -> Proposal:
    return Proposal(
        id=row.id,
        case_id=row.case_id,
        outcome=CaseOutcome(row.outcome),
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_by_name=row.proposed_by_name,
        proposed_at=row.proposed_at,
        status=ProposalStatus(row.status),
        decided_by=row.decided_by,
        decided_by_name=row.decided_by_name,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _text(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > MAX_TEXT:
        raise ApplicationError("validation_failed", fields=[_problem(field, "required")])
    return cleaned


def _problem(field: str, code: str) -> FieldProblem:
    return FieldProblem(field_path=["body", field], code=code)


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id
