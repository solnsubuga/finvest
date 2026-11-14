"""Staff routes for the console's Compliance screen: screening, and the cases staff close.

Seeing anything here needs `compliance.view`. Proposing how to close a case, deciding someone
else's proposal, withdrawing your own and asking for a fresh screen need
`compliance_cases.change`, and approving, which closes the case, also needs the second step from
the last few minutes. Closing a case always takes two people.

Clients never see any of this: telling a client they matched a list could tip them off.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.kyc import service as kyc
from app.modules.payouts import payouts
from app.modules.screening import service
from app.modules.screening.domain.cases import (
    MAX_TEXT,
    OUTCOMES,
    CaseOutcome,
    CaseSource,
    CaseStatus,
    Decision,
    ProposalStatus,
)
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

Viewers = Annotated[StaffSession, Depends(require_permission("compliance.view"))]
Changers = Annotated[StaffSession, Depends(require_permission("compliance_cases.change"))]
# Approving closes the case, and a sanctions outcome holds the client's payouts, so it asks for
# a passkey or app code from the last few minutes.
Approvers = Annotated[StaffSession, Depends(require_confirmed("compliance_cases.change"))]

Text = Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
SourceName = Literal["screening", "monitoring"]
StatusName = Literal["open", "closed"]
OutcomeName = Literal["not_a_match", "pep_accepted", "sanctioned", "no_concern", "report_to_fia"]
ProposalStatusName = Literal["proposed", "approved", "sent_back", "withdrawn"]
StandingName = Literal[
    "clear", "possible_match", "confirmed_match", "unavailable", "pending", "not_run"
]
RunStatusName = Literal[
    "pending", "clear", "possible_match", "unavailable", "not_run", "no_subject"
]
ReasonName = Literal["onboarding", "rescreen", "payout", "staff"]
CategoryName = Literal["sanctions", "pep", "adverse_media", "other"]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class ClientOut(Contract):
    id: PushId
    # "SURNAME Given names" from an approved identity check, else None.
    name: str | None
    account_number: str | None


class CaseProposalOut(Contract):
    id: PushId
    outcome: OutcomeName
    reason: str
    proposed_by: PushId
    proposed_by_name: str
    proposed_at: datetime
    status: ProposalStatusName
    decided_by: PushId | None
    decided_by_name: str | None
    decided_at: datetime | None
    # Why it was sent back, or a note left when approving.
    decision_note: str | None


class CaseOut(Contract):
    id: PushId
    client: ClientOut
    source: SourceName
    # `possible_match` for screening, or a monitoring rule's code.
    code: str
    opened_at: datetime
    status: StatusName
    outcome: OutcomeName | None
    closed_at: datetime | None
    # The open proposal, if a first person has made one.
    waiting: CaseProposalOut | None


class RunOut(Contract):
    id: PushId
    reason: ReasonName
    requested_by: PushId | None
    requested_at: datetime
    status: RunStatusName
    finished_at: datetime | None
    provider: str | None
    # The provider's reference, to find the search in the provider's own console.
    provider_reference: str | None
    failure_code: str | None
    match_count: int | None
    new_match_count: int | None


class MatchOut(Contract):
    entity_id: str
    # The listed entity's name, not the client's.
    name: str
    category: CategoryName
    # How close the provider says the match is, as a whole percentage.
    strength: int
    sources: list[str]
    # Already ruled out or accepted for this client on an earlier case.
    known: bool


class StandingOut(Contract):
    outcome: StandingName
    screened_at: datetime | None
    case_id: PushId | None
    pep: bool


class CaseDetailOut(Contract):
    case: CaseOut
    # For a screening case: the screen and what it found.
    run: RunOut | None
    matches: list[MatchOut]
    # For a monitoring case: the transactions and figures the rule saw.
    detail: dict[str, Any]
    proposals: list[CaseProposalOut]
    standing: StandingOut | None
    # The outcomes that fit this case.
    outcomes: list[OutcomeName]
    # The reader may propose: the case is open, nothing is waiting, and they hold the permission.
    can_propose: bool
    # The reader may approve or send back the waiting proposal: they did not make it.
    can_decide: bool
    # The reader made the waiting proposal, and may only withdraw it.
    yours: bool


class SummaryOut(Contract):
    # False until a screening provider is set up: screens are recorded as not run.
    screening_on: bool
    provider: str
    open_cases: int
    open_screening_cases: int
    open_monitoring_cases: int
    waiting_for_second_person: int
    screened_last_30_days: int
    unavailable_last_30_days: int
    not_run_last_30_days: int
    pending: int
    last_screened_at: datetime | None
    payouts_waiting: int


class HeldAmountOut(Contract):
    minor: int
    currency: Literal["UGX", "USD"]


class WaitingPayoutOut(Contract):
    """A payout whose money is reserved and waiting for its client to be screened."""

    id: PushId
    client: ClientOut
    kind: Literal["mobile_money", "bank"]
    amount: HeldAmountOut
    created_at: datetime
    # Where the client stands: why it waits.
    standing: StandingOut | None


class ClientScreeningOut(Contract):
    client: ClientOut
    standing: StandingOut | None
    runs: list[RunOut]
    cases: list[CaseOut]


class CaseProposeIn(Contract):
    outcome: OutcomeName
    reason: Text


class SendBackIn(Contract):
    note: Text


class ApproveIn(Contract):
    note: Text | None = None


def _proposal(proposal: service.Proposal) -> CaseProposalOut:
    return CaseProposalOut(
        id=proposal.id,
        outcome=proposal.outcome.value,
        reason=proposal.reason,
        proposed_by=proposal.proposed_by,
        proposed_by_name=proposal.proposed_by_name,
        proposed_at=proposal.proposed_at,
        status=proposal.status.value,
        decided_by=proposal.decided_by,
        decided_by_name=proposal.decided_by_name,
        decided_at=proposal.decided_at,
        decision_note=proposal.decision_note,
    )


def _client(client_id: str, identities: dict[str, kyc.Identity]) -> ClientOut:
    found = identities.get(client_id)
    return ClientOut(
        id=client_id,
        name=found.verified_name if found else None,
        account_number=found.account_number if found else None,
    )


def _case(case: service.Case, identities: dict[str, kyc.Identity]) -> CaseOut:
    return CaseOut(
        id=case.id,
        client=_client(case.client_id, identities),
        source=case.source.value,
        code=case.code,
        opened_at=case.opened_at,
        status=case.status.value,
        outcome=case.outcome.value if case.outcome else None,
        closed_at=case.closed_at,
        waiting=_proposal(case.waiting) if case.waiting else None,
    )


def _run(run: service.Run) -> RunOut:
    return RunOut(
        id=run.id,
        reason=run.reason.value,
        requested_by=run.requested_by,
        requested_at=run.requested_at,
        status=run.status.value,
        finished_at=run.finished_at,
        provider=run.provider,
        provider_reference=run.provider_reference,
        failure_code=run.failure_code,
        match_count=run.match_count,
        new_match_count=run.new_match_count,
    )


def _standing(standing: service.ScreeningStanding | None) -> StandingOut | None:
    if standing is None:
        return None
    return StandingOut(
        outcome=standing.outcome.value,
        screened_at=standing.screened_at,
        case_id=standing.case_id,
        pep=standing.pep,
    )


def create_router(screening_provider: str) -> APIRouter:
    """`screening_provider` is the worker's setting, `none` until a provider is set up."""
    router = APIRouter(prefix="/staff/compliance", tags=["staff compliance"], responses=_REFUSALS)

    @router.get("/summary")
    async def compliance_summary(staff: Viewers, database: Db, clock: Time) -> SummaryOut:
        """What is waiting, and whether screening is running."""
        async with database.actor_transaction(staff.actor) as session:
            found = await service.summary(session, clock.now())
            waiting = len(await payouts.waiting_for_screening(session, limit=200))
        return SummaryOut(
            screening_on=screening_provider != "none",
            provider=screening_provider,
            open_cases=found.open_cases,
            open_screening_cases=found.open_screening_cases,
            open_monitoring_cases=found.open_monitoring_cases,
            waiting_for_second_person=found.waiting_for_second_person,
            screened_last_30_days=found.screened_recently,
            unavailable_last_30_days=found.unavailable_recently,
            not_run_last_30_days=found.not_run_recently,
            pending=found.pending,
            last_screened_at=found.last_screened_at,
            payouts_waiting=waiting,
        )

    @router.get("/payouts")
    async def waiting_payouts(staff: Viewers, database: Db) -> list[WaitingPayoutOut]:
        """Payouts waiting for their client to be screened, oldest first, at most 200. They move
        on by themselves once the client is screened clear; staff may decline one from the
        review queue's decline route."""
        async with database.actor_transaction(staff.actor) as session:
            waiting = await payouts.waiting_for_screening(session, limit=200)
            clients = [payout.client_id for payout in waiting]
            identities = await kyc.identities(session, clients)
            standings = await service.standings(session, clients)
        return [
            WaitingPayoutOut(
                id=payout.id,
                client=_client(payout.client_id, identities),
                kind=payout.kind.value,
                amount=HeldAmountOut(
                    minor=payout.amount.minor, currency=payout.amount.currency.value
                ),
                created_at=payout.created_at,
                standing=_standing(standings.get(payout.client_id)),
            )
            for payout in waiting
        ]

    @router.get("/cases")
    async def list_cases(
        staff: Viewers,
        database: Db,
        status: Annotated[StatusName | None, Query()] = "open",
        source: Annotated[SourceName | None, Query()] = None,
    ) -> list[CaseOut]:
        """Cases, at most 200: open ones oldest first, closed ones newest first."""
        async with database.actor_transaction(staff.actor) as session:
            cases = await service.list_cases(
                session,
                status=CaseStatus(status) if status else None,
                source=CaseSource(source) if source else None,
                limit=200,
            )
            identities = await kyc.identities(session, [case.client_id for case in cases])
        return [_case(case, identities) for case in cases]

    @router.get("/cases/{case_id}")
    async def get_case(case_id: PushId, staff: Viewers, database: Db) -> CaseDetailOut:
        """One case: what raised it, what the screen found, and every proposal to close it."""
        return await _read(database, staff, case_id)

    @router.post("/cases/{case_id}/proposals", status_code=201)
    async def propose(
        case_id: PushId, body: CaseProposeIn, staff: Changers, database: Db, clock: Time
    ) -> CaseDetailOut:
        """Propose how to close an open case, with a reason. A different person decides.

        `409` with `proposal_waiting` if a proposal is already waiting, or `case_closed`. `422`
        with `outcome_does_not_fit_case` for a monitoring outcome on a screening case or the
        other way round.
        """
        async with database.actor_transaction(staff.actor) as session:
            await service.propose(
                session, staff.actor, case_id, CaseOutcome(body.outcome), body.reason, clock
            )
        return await _read(database, staff, case_id)

    @router.post("/proposals/{proposal_id}/approve")
    async def approve(
        proposal_id: PushId,
        staff: Approvers,
        database: Db,
        clock: Time,
        body: ApproveIn | None = None,
    ) -> CaseDetailOut:
        """Approve someone else's proposal: the case closes with its outcome. The proposer gets
        `403`. A `sanctioned` outcome holds the client's payouts from now on."""
        return await _decide(
            database, staff, proposal_id, Decision.APPROVED, body.note if body else None, clock
        )

    @router.post("/proposals/{proposal_id}/send-back")
    async def send_back(
        proposal_id: PushId, body: SendBackIn, staff: Changers, database: Db, clock: Time
    ) -> CaseDetailOut:
        """Decline someone else's proposal, with a note. The case stays open. The proposer gets
        `403`."""
        return await _decide(database, staff, proposal_id, Decision.SENT_BACK, body.note, clock)

    @router.post("/proposals/{proposal_id}/withdraw")
    async def withdraw(
        proposal_id: PushId, staff: Changers, database: Db, clock: Time
    ) -> CaseDetailOut:
        """Take back your own proposal before anyone decides it. Anyone else gets `403`."""
        return await _decide(database, staff, proposal_id, Decision.WITHDRAWN, None, clock)

    @router.get("/clients/{client_id}")
    async def client_screening(
        client_id: PushId, staff: Viewers, database: Db
    ) -> ClientScreeningOut:
        """A client's screening: where they stand, their screens newest first, and their cases."""
        async with database.actor_transaction(staff.actor) as session:
            standing = await service.latest_outcome(session, client_id)
            runs = await service.runs_for_client(session, client_id)
            cases = await service.list_cases(session, client_id=client_id, limit=200)
            identities = await kyc.identities(session, [client_id])
        return ClientScreeningOut(
            client=_client(client_id, identities),
            standing=_standing(standing),
            runs=[_run(run) for run in runs],
            cases=[_case(case, identities) for case in cases],
        )

    @router.post("/clients/{client_id}/screenings", status_code=201)
    async def screen_again(
        client_id: PushId, staff: Changers, key: IdempotencyKey, database: Db, clock: Time
    ) -> RunOut:
        """Screen the client again now, for example after their name changed. One person's
        step: it only reads lists. The same `Idempotency-Key` asks once."""
        async with database.actor_transaction(staff.actor) as session:
            run = await service.request_by_staff(session, staff.actor, client_id, key, clock)
        return _run(run)

    return router


async def _decide(
    database: Database,
    staff: StaffSession,
    proposal_id: str,
    decision: Decision,
    note: str | None,
    clock: Clock,
) -> CaseDetailOut:
    async with database.actor_transaction(staff.actor) as session:
        proposal = await service.decide(session, staff.actor, proposal_id, decision, note, clock)
    return await _read(database, staff, proposal.case_id)


async def _read(database: Database, staff: StaffSession, case_id: str) -> CaseDetailOut:
    async with database.actor_transaction(staff.actor) as session:
        detail = await service.get_case(session, case_id)
        standing = await service.latest_outcome(session, detail.case.client_id)
        identities = await kyc.identities(session, [detail.case.client_id])
    case = detail.case
    waiting = case.waiting
    changer = "compliance_cases.change" in staff.permissions
    yours = waiting is not None and waiting.proposed_by == staff.staff_id
    return CaseDetailOut(
        case=_case(case, identities),
        run=_run(detail.run) if detail.run else None,
        matches=[
            MatchOut(
                entity_id=found.match.entity_id,
                name=found.match.name,
                category=found.match.category.value,
                strength=found.match.strength,
                sources=list(found.match.sources),
                known=found.known,
            )
            for found in detail.matches
        ],
        detail=case.detail,
        proposals=[_proposal(p) for p in detail.proposals],
        standing=_standing(standing),
        outcomes=sorted(outcome.value for outcome in OUTCOMES[case.source]),
        can_propose=changer and case.status is CaseStatus.OPEN and waiting is None,
        can_decide=changer
        and waiting is not None
        and waiting.status is ProposalStatus.PROPOSED
        and not yours,
        yours=yours,
    )
