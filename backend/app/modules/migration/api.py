"""Staff routes for the Finvest 1.0 migration checks. Nothing here creates a client or money.

Operations run trial imports. Finance enters the old system's totals and signs off what
differs, and a sign-off takes a second person. Every route checks a permission; the service
and the database check the rest.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BeforeValidator, Field

from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.migration import claims, service
from app.modules.migration.domain.claim import Progress
from app.modules.migration.domain.finance import ControlTotals, Figure, InvalidFinanceCheck
from app.modules.migration.domain.sheet import Severity, Sheet
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

# Counts and amounts are whole numbers in the smallest unit. This bound keeps every one of them
# exact in a browser, which holds integers exactly only up to 2**53 - 1.
MAX_FIGURE = 2**53 - 1
Whole = Annotated[int, Field(ge=0, le=MAX_FIGURE)]


def _iso_date(value: object) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("A date is written YYYY-MM-DD")
    return date.fromisoformat(value)


# Contracts are strict, and JSON has no date or enum of its own: these two arrive as text.
IsoDate = Annotated[date, BeforeValidator(_iso_date)]
CurrencyCode = Annotated[Currency, Field(strict=False)]
Allowed = Literal["run_trial", "enter_totals", "sign_off"]

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    413: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}
router = APIRouter(prefix="/staff/migration", tags=["migration"], responses=_REFUSALS)

Viewer = Annotated[StaffSession, Depends(require_permission("migration.view"))]
Importer = Annotated[StaffSession, Depends(require_permission("migration.import"))]
Signer = Annotated[StaffSession, Depends(require_permission("migration.sign_off"))]
# Deciding needs a passkey or app code from the last few minutes.
SignOffDecider = Annotated[StaffSession, Depends(require_confirmed("migration.sign_off"))]


class ProblemView(Contract):
    """One thing wrong with a sheet. It never carries the cell's value."""

    sheet: Sheet
    row: int | None
    column: str | None
    code: str
    severity: Severity


class TrialView(Contract):
    id: PushId
    as_of: date
    ran_by: PushId
    ran_at: datetime
    error_count: int
    warning_count: int
    # The first problems, errors first. The two counts are always complete.
    problems: list[ProblemView]


class SignOffView(Contract):
    id: PushId
    key: str
    figures: list[int]
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    state: service.SignOffState
    decided_by: PushId | None
    # Why it was rejected, shown to the person who proposed it.
    decision_reason: str | None


class TotalLineView(Contract):
    key: str
    figure: Figure
    currency: Currency | None
    old: int
    new: int
    agrees: bool


class AmountView(Contract):
    currency: Currency
    minor: int


class TotalsView(Contract):
    """The latest entry of the old system's totals. It is used once `confirmed_by` is set."""

    id: PushId
    as_of: date
    entered_by: PushId
    entered_at: datetime
    confirmed_by: PushId | None
    clients: int
    verified_identities: int
    running_investments: int
    pending_orders: int
    invested: list[AmountView]
    interest_before_tax: list[AmountView]


class DifferenceView(Contract):
    """Something that differs now. `figures` is exactly what a sign-off for it accepts.

    `row` is the investments sheet row, null for a total line or a trial run before rows were
    kept. `currency` is that of the figures, null for a count or a tax rate (basis points).

    `sign_off` is the latest one for these figures. A rejected one stays until it is proposed
    again, so its proposer can read why.
    """

    key: str
    figures: list[int]
    row: int | None
    currency: Currency | None
    sign_off: SignOffView | None


class ClaimProgressView(Contract):
    invited: int
    claimed_and_confirmed: int
    said_something_is_wrong: int
    not_yet_claimed: int
    cannot_be_invited: int


class OverviewView(Contract):
    trials_run: int
    trial: TrialView | None
    totals: TotalsView | None
    # Empty until the old system's totals are entered and confirmed by a second person.
    lines: list[TotalLineView]
    differences: list[DifferenceView]
    blockers: list[service.BlockerCode]
    # Null until the switch has created the claims.
    claims: ClaimProgressView | None
    # What the signed-in person may do here. Each route checks it again.
    allowed: list[Allowed]


class TrialSummaryView(Contract):
    id: PushId
    as_of: date
    ran_by: PushId
    ran_at: datetime
    error_count: int
    warning_count: int
    clients_sha256: str
    investments_sha256: str
    totals_confirmed: bool
    # Null while that day's Finvest 1.0 totals are not confirmed.
    totals_differing: int | None
    investments_differing: int


class TrialHistoryView(Contract):
    trials: list[TrialSummaryView]


class TrialRequest(Contract):
    as_of: IsoDate
    clients_csv: str = Field(max_length=service.MAX_SHEET_BYTES)
    investments_csv: str = Field(max_length=service.MAX_SHEET_BYTES)


class Amount(Contract):
    currency: CurrencyCode
    minor: Whole


class ControlTotalsRequest(Contract):
    as_of: IsoDate
    clients: Whole
    verified_identities: Whole
    running_investments: Whole
    pending_orders: Whole
    invested: list[Amount] = Field(max_length=len(Currency))
    interest_before_tax: list[Amount] = Field(max_length=len(Currency))


class SignOffRequest(Contract):
    key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class DecisionRequest(Contract):
    approve: bool
    reason: str | None = Field(default=None, min_length=1, max_length=500)


def _actor(staff: StaffSession) -> Actor:
    return Actor("staff", staff.staff_id)


def _state(request: Request) -> tuple[Database, Clock]:
    return request.app.state.database, request.app.state.clock


def _sign_off(view: service.SignOffView) -> SignOffView:
    return SignOffView(
        id=view.id,
        key=view.key,
        figures=list(view.figures),
        reason=view.reason,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        state=view.state,
        decided_by=view.decided_by,
        decision_reason=view.decision_reason,
    )


def _totals(view: service.TotalsView | None) -> TotalsView | None:
    if view is None:
        return None
    old = view.totals
    return TotalsView(
        id=view.id,
        as_of=view.as_of,
        entered_by=view.entered_by,
        entered_at=view.entered_at,
        confirmed_by=view.confirmed_by,
        clients=old.clients,
        verified_identities=old.verified_identities,
        running_investments=old.running_investments,
        pending_orders=old.pending_orders,
        invested=[AmountView(currency=a.currency, minor=a.minor) for a in old.invested],
        interest_before_tax=[
            AmountView(currency=a.currency, minor=a.minor) for a in old.interest_before_tax
        ],
    )


def _allowed(staff: StaffSession) -> list[Allowed]:
    allowed: list[Allowed] = []
    if "migration.import" in staff.permissions:
        allowed.append("run_trial")
    if "migration.sign_off" in staff.permissions:
        allowed += ["enter_totals", "sign_off"]
    return allowed


def _claims(progress: Progress) -> ClaimProgressView | None:
    if progress.invited + progress.cannot_be_invited == 0:
        return None
    return ClaimProgressView(
        invited=progress.invited,
        claimed_and_confirmed=progress.claimed_and_confirmed,
        said_something_is_wrong=progress.said_something_is_wrong,
        not_yet_claimed=progress.not_yet_claimed,
        cannot_be_invited=progress.cannot_be_invited,
    )


def _overview(view: service.Overview, progress: Progress, staff: StaffSession) -> OverviewView:
    trial = view.trial
    placed = trial.placed if trial is not None else {}
    line_currency = {line.key: line.currency for line in view.lines}
    return OverviewView(
        trials_run=view.trials_run,
        trial=None
        if trial is None
        else TrialView(
            id=trial.id,
            as_of=trial.as_of,
            ran_by=trial.ran_by,
            ran_at=trial.ran_at,
            error_count=trial.error_count,
            warning_count=trial.warning_count,
            problems=[
                ProblemView(
                    sheet=problem.sheet,
                    row=problem.row,
                    column=problem.column,
                    code=problem.code,
                    severity=problem.severity,
                )
                for problem in trial.problems
            ],
        ),
        totals=_totals(view.totals),
        lines=[
            TotalLineView(
                key=line.key,
                figure=line.figure,
                currency=line.currency,
                old=line.old,
                new=line.new,
                agrees=line.agrees,
            )
            for line in view.lines
        ],
        differences=[
            DifferenceView(
                key=key,
                figures=list(figures),
                row=placed[key].row if key in placed else None,
                currency=placed[key].currency if key in placed else line_currency.get(key),
                sign_off=_sign_off(view.sign_offs[key]) if key in view.sign_offs else None,
            )
            for key, figures in view.differing.items()
        ],
        blockers=list(view.blockers),
        claims=_claims(progress),
        allowed=_allowed(staff),
    )


async def _read(request: Request, staff: StaffSession) -> OverviewView:
    database, _ = _state(request)
    async with database.actor_transaction(_actor(staff)) as session:
        return _overview(
            await service.overview(session), await claims.claim_progress(session), staff
        )


@router.get("")
async def migration_overview(request: Request, staff: Viewer) -> OverviewView:
    """The latest trial import, the finance check and what still blocks the switch."""
    return await _read(request, staff)


@router.get("/trials")
async def trial_history(
    request: Request, staff: Viewer, date_from: date, date_to: date
) -> TrialHistoryView:
    """Every trial run for "as of" days in the range, oldest first, for a parallel run."""
    database, _ = _state(request)
    async with database.actor_transaction(_actor(staff)) as session:
        trials = await service.trial_history(session, date_from=date_from, date_to=date_to)
    return TrialHistoryView(
        trials=[
            TrialSummaryView(
                id=t.id,
                as_of=t.as_of,
                ran_by=t.ran_by,
                ran_at=t.ran_at,
                error_count=t.error_count,
                warning_count=t.warning_count,
                clients_sha256=t.clients_sha256,
                investments_sha256=t.investments_sha256,
                totals_confirmed=t.totals_confirmed,
                totals_differing=t.totals_differing,
                investments_differing=t.investments_differing,
            )
            for t in trials
        ]
    )


@router.post("/trials")
async def run_trial(body: TrialRequest, request: Request, staff: Importer) -> OverviewView:
    """Check both sheets and keep the result. The sheets themselves are not kept."""
    database, clock = _state(request)
    async with database.actor_transaction(_actor(staff)) as session:
        await service.run_trial_import(
            session,
            _actor(staff),
            clock,
            clients_csv=body.clients_csv,
            investments_csv=body.investments_csv,
            as_of=body.as_of,
        )
    return await _read(request, staff)


@router.put("/control-totals")
async def enter_control_totals(
    body: ControlTotalsRequest, request: Request, staff: Signer
) -> OverviewView:
    """Record what Finvest 1.0 says it holds on a date. Entering it again is a correction.

    The entry is used only once someone else has confirmed it.
    """
    try:
        totals = ControlTotals(
            clients=body.clients,
            verified_identities=body.verified_identities,
            running_investments=body.running_investments,
            pending_orders=body.pending_orders,
            invested=tuple(Money(a.minor, a.currency) for a in body.invested),
            interest_before_tax=tuple(Money(a.minor, a.currency) for a in body.interest_before_tax),
        )
    except InvalidFinanceCheck:
        # The same currency given twice for one figure.
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["totals"], code="invalid")]
        ) from None
    database, clock = _state(request)
    async with database.actor_transaction(_actor(staff)) as session:
        await service.enter_control_totals(
            session, _actor(staff), clock, as_of=body.as_of, totals=totals
        )
    return await _read(request, staff)


@router.post("/control-totals/{totals_id}/confirmation")
async def confirm_control_totals(
    totals_id: PushId, request: Request, staff: SignOffDecider
) -> OverviewView:
    """Confirm the totals someone else entered, after checking them against the old system."""
    database, clock = _state(request)
    async with database.actor_transaction(_actor(staff)) as session:
        await service.confirm_control_totals(session, _actor(staff), clock, totals_id)
    return await _read(request, staff)


@router.post("/sign-offs", status_code=201)
async def propose_sign_off(body: SignOffRequest, request: Request, staff: Signer) -> SignOffView:
    """Ask for a difference to be accepted. The figures are the ones that stand now."""
    database, clock = _state(request)
    async with database.actor_transaction(_actor(staff)) as session:
        view = await service.propose_sign_off(
            session, _actor(staff), clock, key=body.key, reason=body.reason
        )
    return _sign_off(view)


@router.post("/sign-offs/{sign_off_id}/decision")
async def decide_sign_off(
    sign_off_id: PushId, body: DecisionRequest, request: Request, staff: SignOffDecider
) -> SignOffView:
    """Approve or reject a sign-off someone else proposed."""
    database, clock = _state(request)
    async with database.actor_transaction(_actor(staff)) as session:
        view = await service.decide_sign_off(
            session, _actor(staff), clock, sign_off_id, approve=body.approve, reason=body.reason
        )
    return _sign_off(view)
