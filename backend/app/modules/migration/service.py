"""Trial imports, the old system's totals and sign-offs. Every function works inside the
caller's transaction.

The caller opens `Database.actor_transaction(staff)` for the same member of staff it passes
here. The policies and triggers of migration 0070 then hold whatever this code does: each row
names the person whose transaction wrote it, nothing is ever changed afterwards, and a sign-off
is decided by someone other than its proposer.

Which staff may run a trial, enter totals or sign off is a permission, checked by the route
before it calls in.

Nothing from a spreadsheet is stored. A trial keeps counts, problem codes with their sheet, row
and column, the new system's totals, and the figures of the investments that differ.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments import service as investments
from app.modules.migration.domain.finance import (
    MAX_REASON_LENGTH,
    ControlTotals,
    InvalidFinanceCheck,
    TotalLine,
    check_investments,
    new_totals,
    total_lines,
)
from app.modules.migration.domain.sheet import Problem, Severity, Sheet
from app.modules.migration.domain.trial import run_trial
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

# The problems kept with a trial, errors first. The counts are always complete.
MAX_STORED_PROBLEMS = 500
# About 100,000 rows of a full clients sheet. Larger uploads are refused before parsing.
MAX_SHEET_BYTES = 40_000_000

BlockerCode = Literal[
    "no_trial",
    "import_has_errors",
    "totals_not_entered",
    "totals_not_confirmed",
    "difference_not_signed_off",
]
SignOffState = Literal["proposed", "approved", "rejected"]


@dataclass(frozen=True, slots=True)
class TrialView:
    id: str
    as_of: date
    ran_by: str
    ran_at: datetime
    error_count: int
    warning_count: int
    problems: tuple[Problem, ...]
    new_totals: dict[str, int]
    # Investments whose interest or tax rate differs, by key, with the figures that differ.
    differing: dict[str, tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class SignOffView:
    id: str
    key: str
    figures: tuple[int, ...]
    reason: str
    proposed_by: str
    proposed_at: datetime
    state: SignOffState
    decided_by: str | None
    # Why it was rejected. A rejection always gives one; an approval may.
    decision_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TotalsView:
    """The latest entry of the old system's totals for a date. It counts once confirmed."""

    id: str
    as_of: date
    entered_by: str
    entered_at: datetime
    confirmed_by: str | None
    totals: ControlTotals


@dataclass(frozen=True, slots=True)
class TrialSummary:
    """One trial run, for following a parallel run day by day. No problems and no figures.

    `totals_differing` is None while that day's Finvest 1.0 totals are not yet confirmed.
    """

    id: str
    as_of: date
    ran_by: str
    ran_at: datetime
    error_count: int
    warning_count: int
    clients_sha256: str
    investments_sha256: str
    totals_confirmed: bool
    totals_differing: int | None
    investments_differing: int


@dataclass(frozen=True, slots=True)
class Overview:
    trials_run: int
    trial: TrialView | None
    # The latest entry of the old system's totals for the trial's "as of" date, if any.
    totals: TotalsView | None
    # Empty until that entry has been confirmed by a second person.
    lines: tuple[TotalLine, ...]
    # Everything that differs now, by key, with its figures: total lines, then investments.
    differing: dict[str, tuple[int, ...]]
    # The latest sign-off for each key that differs, when it was written for today's figures.
    sign_offs: dict[str, SignOffView]
    blockers: tuple[BlockerCode, ...]


_INSERT_TRIAL = text(
    "INSERT INTO migration_trials (id, as_of, clients_sha256, investments_sha256, error_count, "
    "warning_count, problems, summary, new_totals, differing, ran_by, ran_at) VALUES (:id, "
    ":as_of, :clients_sha256, :investments_sha256, :error_count, :warning_count, "
    "CAST(:problems AS jsonb), CAST(:summary AS jsonb), CAST(:new_totals AS jsonb), "
    "CAST(:differing AS jsonb), :ran_by, :ran_at)"
)
_LATEST_TRIAL = text(
    "SELECT id, as_of, error_count, warning_count, problems, new_totals, differing, ran_by, "
    "ran_at FROM migration_trials ORDER BY ran_at DESC, id DESC LIMIT 1"
)
_COUNT_TRIALS = text("SELECT count(*) FROM migration_trials")
_TRIALS_BETWEEN = text(
    "SELECT id, as_of, error_count, warning_count, problems, new_totals, differing, ran_by, "
    "ran_at, clients_sha256, investments_sha256 FROM migration_trials "
    "WHERE as_of BETWEEN :date_from AND :date_to ORDER BY ran_at, id LIMIT :limit"
)
# About a quarter of daily runs, more than any parallel run needs.
MAX_HISTORY = 400
_INSERT_TOTALS = text(
    "INSERT INTO migration_control_totals (id, as_of, totals, entered_by, entered_at) "
    "VALUES (:id, :as_of, CAST(:totals AS jsonb), :entered_by, :entered_at)"
)
_LATEST_TOTALS = text(
    "SELECT t.id, t.as_of, t.totals, t.entered_by, t.entered_at, c.confirmed_by "
    "FROM migration_control_totals AS t "
    "LEFT JOIN migration_control_totals_confirmations AS c ON c.totals_id = t.id "
    "WHERE t.as_of = :as_of ORDER BY t.entered_at DESC, t.id DESC LIMIT 1"
)
_TOTALS_DATE = text("SELECT as_of FROM migration_control_totals WHERE id = :id")
_INSERT_CONFIRMATION = text(
    "INSERT INTO migration_control_totals_confirmations (totals_id, confirmed_by, confirmed_at) "
    "VALUES (:totals_id, :confirmed_by, :confirmed_at)"
)
# One confirmation or new entry at a time for a date, so a confirmation is always of the
# entry that is the latest when it commits.
_LOCK_TOTALS = text("SELECT pg_advisory_xact_lock(hashtextextended(:as_of, 71))")
_INSERT_SIGN_OFF = text(
    "INSERT INTO migration_sign_offs (id, key, figures, reason, proposed_by, proposed_at) "
    "VALUES (:id, :key, :figures, :reason, :proposed_by, :proposed_at)"
)
_SIGN_OFF_COLUMNS = (
    "s.id, s.key, s.figures, s.reason, s.proposed_by, s.proposed_at, d.outcome, d.decided_by, "
    "d.reason AS decision_reason"
)
_SIGN_OFFS = text(
    f"SELECT {_SIGN_OFF_COLUMNS} FROM migration_sign_offs AS s "  # noqa: S608
    "LEFT JOIN migration_sign_off_decisions AS d ON d.sign_off_id = s.id "
    "ORDER BY s.proposed_at, s.id"
)
_SIGN_OFF = text(
    f"SELECT {_SIGN_OFF_COLUMNS} FROM migration_sign_offs AS s "  # noqa: S608
    "LEFT JOIN migration_sign_off_decisions AS d ON d.sign_off_id = s.id WHERE s.id = :id"
)
_INSERT_DECISION = text(
    "INSERT INTO migration_sign_off_decisions (sign_off_id, outcome, reason, decided_by, "
    "decided_at) VALUES (:sign_off_id, :outcome, :reason, :decided_by, :decided_at)"
)
# One proposal at a time for a key, so two people cannot both propose and cross-approve.
_LOCK_KEY = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 70))")


async def run_trial_import(
    session: AsyncSession,
    staff: Actor,
    clock: Clock,
    *,
    clients_csv: str,
    investments_csv: str,
    as_of: date,
) -> TrialView:
    """Check both sheets and keep the result. No client or investment is created."""
    _staff_only(staff)
    if as_of > business_date(clock.now()):
        raise _invalid("as_of", "in_the_future")
    if max(len(clients_csv.encode()), len(investments_csv.encode())) > MAX_SHEET_BYTES:
        raise ApplicationError("payload_too_large")
    views = await investments.list_products(session, clock)
    products = {view.product.code: view.product for view in views}
    tax = {view.product.code: view.rate.tax_bps for view in views if view.rate is not None}
    report = run_trial(clients_csv, investments_csv, as_of=as_of, products=products)
    check = check_investments(report, products, tax)

    kept = sorted(report.problems, key=lambda p: p.severity is not Severity.ERROR)
    view = TrialView(
        id=push_id(),
        as_of=as_of,
        ran_by=_id(staff),
        ran_at=clock.now(),
        error_count=len(report.errors),
        warning_count=len(report.warnings),
        problems=tuple(kept[:MAX_STORED_PROBLEMS]),
        new_totals=new_totals(report, check),
        differing=check.differing,
    )
    summary = report.summary
    await session.execute(
        _INSERT_TRIAL,
        {
            "id": view.id,
            "as_of": view.as_of,
            "clients_sha256": hashlib.sha256(clients_csv.encode()).hexdigest(),
            "investments_sha256": hashlib.sha256(investments_csv.encode()).hexdigest(),
            "error_count": view.error_count,
            "warning_count": view.warning_count,
            "problems": json.dumps(
                [
                    {
                        "sheet": p.sheet.value,
                        "row": p.row,
                        "column": p.column,
                        "code": p.code,
                        "severity": p.severity.value,
                    }
                    for p in view.problems
                ]
            ),
            "summary": json.dumps(
                {
                    "clients_to_recheck": summary.clients_to_recheck,
                    "clients_without_phone": summary.clients_without_phone,
                }
            ),
            "new_totals": json.dumps(view.new_totals),
            "differing": json.dumps(view.differing),
            "ran_by": view.ran_by,
            "ran_at": view.ran_at,
        },
    )
    await _audit(
        session,
        staff,
        "change",
        "migration.trial_run",
        "migration_trial",
        view.id,
        clock,
        details={
            "as_of": as_of.isoformat(),
            "error_count": view.error_count,
            "warning_count": view.warning_count,
            "differing_count": len(view.differing),
        },
    )
    return view


async def enter_control_totals(
    session: AsyncSession, staff: Actor, clock: Clock, *, as_of: date, totals: ControlTotals
) -> TotalsView:
    """Record what the old system says it holds on `as_of`. A correction is a new entry.

    The entry is not used until a second person confirms it, and a new entry replaces the one
    before it, confirmed or not. One person can therefore never change what the trial is
    compared with.
    """
    _staff_only(staff)
    totals_id = push_id()
    await session.execute(_LOCK_TOTALS, {"as_of": as_of.isoformat()})
    await session.execute(
        _INSERT_TOTALS,
        {
            "id": totals_id,
            "as_of": as_of,
            "totals": json.dumps(
                {
                    "clients": totals.clients,
                    "verified_identities": totals.verified_identities,
                    "running_investments": totals.running_investments,
                    "pending_orders": totals.pending_orders,
                    "invested": {a.currency.value: a.minor for a in totals.invested},
                    "interest_before_tax": {
                        a.currency.value: a.minor for a in totals.interest_before_tax
                    },
                }
            ),
            "entered_by": _id(staff),
            "entered_at": clock.now(),
        },
    )
    await _audit(
        session,
        staff,
        "change",
        "migration.control_totals_entered",
        "migration_control_totals",
        totals_id,
        clock,
        details={"as_of": as_of.isoformat()},
    )
    return TotalsView(totals_id, as_of, _id(staff), clock.now(), None, totals)


async def confirm_control_totals(
    session: AsyncSession, staff: Actor, clock: Clock, totals_id: str
) -> TotalsView:
    """Confirm an entry of the old system's totals, as someone other than who entered it.

    Only the latest entry for its date can be confirmed: an older one has been corrected.
    """
    _staff_only(staff)
    as_of = (await session.execute(_TOTALS_DATE, {"id": totals_id})).scalar_one_or_none()
    if as_of is None:
        raise ApplicationError("not_found")
    await session.execute(_LOCK_TOTALS, {"as_of": as_of.isoformat()})
    entry = await _control_totals(session, as_of)
    if entry is None or entry.id != totals_id or entry.confirmed_by is not None:
        raise ApplicationError("conflict")
    if entry.entered_by == _id(staff):
        raise ApplicationError("forbidden")
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_CONFIRMATION,
                {
                    "totals_id": totals_id,
                    "confirmed_by": _id(staff),
                    "confirmed_at": clock.now(),
                },
            )
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        if constraint == "pk_migration_control_totals_confirmations":
            raise ApplicationError("conflict") from None
        if constraint == "migration_totals_second_person":
            raise ApplicationError("forbidden") from None
        raise
    await _audit(
        session,
        staff,
        "approval",
        "migration.control_totals_confirmed",
        "migration_control_totals",
        totals_id,
        clock,
        reason="Checked against the Finvest 1.0 report",
        details={"as_of": as_of.isoformat()},
    )
    return replace(entry, confirmed_by=_id(staff))


async def overview(session: AsyncSession) -> Overview:
    """The latest trial against the old system's totals, and what still blocks the switch."""
    trials_run = int((await session.execute(_COUNT_TRIALS)).scalar_one())
    row = (await session.execute(_LATEST_TRIAL)).one_or_none()
    if row is None:
        return Overview(0, None, None, (), {}, {}, ("no_trial",))
    trial = _trial(row)
    entry = await _control_totals(session, trial.as_of)
    confirmed = entry is not None and entry.confirmed_by is not None
    lines = total_lines(entry.totals, trial.new_totals) if entry and confirmed else ()
    differing = {line.key: line.figures for line in lines if not line.agrees} | trial.differing

    # The latest sign-off per key is shown, and only for exactly the figures that stand now.
    # A rejected one stays so its proposer can read why; only an approved one counts.
    latest: dict[str, SignOffView] = {}
    for sign_off_row in (await session.execute(_SIGN_OFFS)).all():
        sign_off = _sign_off(sign_off_row)
        latest[sign_off.key] = sign_off
    current = {
        key: sign_off for key, sign_off in latest.items() if differing.get(key) == sign_off.figures
    }
    blockers: list[BlockerCode] = []
    if trial.error_count:
        blockers.append("import_has_errors")
    if entry is None:
        blockers.append("totals_not_entered")
    elif not confirmed:
        blockers.append("totals_not_confirmed")
    if any(key not in current or current[key].state != "approved" for key in differing):
        blockers.append("difference_not_signed_off")
    return Overview(trials_run, trial, entry, lines, differing, current, tuple(blockers))


async def trial_history(
    session: AsyncSession, *, date_from: date, date_to: date
) -> tuple[TrialSummary, ...]:
    """Every trial whose "as of" day falls in the range, oldest first, at most `MAX_HISTORY`.

    Each is compared with the latest Finvest 1.0 totals for its own day, once confirmed.
    Whether a difference was signed off is only judged for the newest trial, by `overview`.
    """
    if date_from > date_to:
        raise _invalid("date_from", "after_date_to")
    rows = (
        await session.execute(
            _TRIALS_BETWEEN, {"date_from": date_from, "date_to": date_to, "limit": MAX_HISTORY}
        )
    ).all()
    entries: dict[date, TotalsView | None] = {}
    summaries: list[TrialSummary] = []
    for row in rows:
        trial = _trial(row)
        if trial.as_of not in entries:
            entries[trial.as_of] = await _control_totals(session, trial.as_of)
        entry = entries[trial.as_of]
        confirmed = entry is not None and entry.confirmed_by is not None
        totals_differing = None
        if entry is not None and confirmed:
            lines = total_lines(entry.totals, trial.new_totals)
            totals_differing = sum(1 for line in lines if not line.agrees)
        summaries.append(
            TrialSummary(
                id=trial.id,
                as_of=trial.as_of,
                ran_by=trial.ran_by,
                ran_at=trial.ran_at,
                error_count=trial.error_count,
                warning_count=trial.warning_count,
                clients_sha256=row.clients_sha256,
                investments_sha256=row.investments_sha256,
                totals_confirmed=confirmed,
                totals_differing=totals_differing,
                investments_differing=len(trial.differing),
            )
        )
    return tuple(summaries)


async def propose_sign_off(
    session: AsyncSession, staff: Actor, clock: Clock, *, key: str, reason: str
) -> SignOffView:
    """Ask for a difference to be accepted, with the reason. A second person approves it.

    The figures are read from the latest trial here, never taken from the caller, so a sign-off
    always records what the system itself says differs.
    """
    _staff_only(staff)
    cleaned = " ".join(reason.split())
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        raise _invalid("reason", "invalid_reason")
    await session.execute(_LOCK_KEY, {"key": key})
    state = await overview(session)
    figures = state.differing.get(key)
    if figures is None:
        raise _invalid("key", "does_not_differ")
    if key in state.sign_offs and state.sign_offs[key].state != "rejected":
        # Already proposed or approved for these figures. A rejected one may be proposed again.
        raise ApplicationError("conflict")
    view = SignOffView(
        id=push_id(),
        key=key,
        figures=figures,
        reason=cleaned,
        proposed_by=_id(staff),
        proposed_at=clock.now(),
        state="proposed",
        decided_by=None,
    )
    await session.execute(
        _INSERT_SIGN_OFF,
        {
            "id": view.id,
            "key": view.key,
            "figures": list(view.figures),
            "reason": view.reason,
            "proposed_by": view.proposed_by,
            "proposed_at": view.proposed_at,
        },
    )
    await _audit(
        session,
        staff,
        "change",
        "migration.sign_off_proposed",
        "migration_sign_off",
        view.id,
        clock,
        reason=view.reason,
        details={"figure_count": len(figures)},
    )
    return view


async def decide_sign_off(
    session: AsyncSession,
    staff: Actor,
    clock: Clock,
    sign_off_id: str,
    *,
    approve: bool,
    reason: str | None = None,
) -> SignOffView:
    """Approve or reject a proposed sign-off, as a member of staff other than its proposer.

    An approval is refused with `conflict` when the figures have changed since the proposal:
    the reason was written for a difference that no longer exists.
    """
    _staff_only(staff)
    row = (await session.execute(_SIGN_OFF, {"id": sign_off_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    sign_off = _sign_off(row)
    if sign_off.proposed_by == _id(staff):
        raise ApplicationError("forbidden")
    note = " ".join((reason or "").split()) or None
    if note is not None and len(note) > MAX_REASON_LENGTH:
        raise _invalid("reason", "invalid_reason")
    if not approve and note is None:
        raise _invalid("reason", "invalid_reason")
    if sign_off.state != "proposed":
        raise ApplicationError("conflict")
    await session.execute(_LOCK_KEY, {"key": sign_off.key})
    if approve and (await overview(session)).differing.get(sign_off.key) != sign_off.figures:
        raise ApplicationError("conflict")
    outcome: SignOffState = "approved" if approve else "rejected"
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_DECISION,
                {
                    "sign_off_id": sign_off.id,
                    "outcome": outcome,
                    "reason": note,
                    "decided_by": _id(staff),
                    "decided_at": clock.now(),
                },
            )
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        if constraint == "pk_migration_sign_off_decisions":
            raise ApplicationError("conflict") from None
        if constraint == "migration_sign_off_second_person":
            raise ApplicationError("forbidden") from None
        raise
    await _audit(
        session,
        staff,
        "approval",
        f"migration.sign_off_{outcome}",
        "migration_sign_off",
        sign_off.id,
        clock,
        reason=note or sign_off.reason,
    )
    return SignOffView(
        sign_off.id,
        sign_off.key,
        sign_off.figures,
        sign_off.reason,
        sign_off.proposed_by,
        sign_off.proposed_at,
        outcome,
        _id(staff),
        note,
    )


# Internals --------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _trial(row: Row[Any]) -> TrialView:
    return TrialView(
        id=row.id,
        as_of=row.as_of,
        ran_by=row.ran_by,
        ran_at=row.ran_at,
        error_count=row.error_count,
        warning_count=row.warning_count,
        problems=tuple(
            Problem(
                Sheet(item["sheet"]),
                item["row"],
                item["column"],
                str(item["code"]),
                Severity(item["severity"]),
            )
            for item in row.problems
        ),
        new_totals={str(key): int(value) for key, value in row.new_totals.items()},
        differing={
            str(key): tuple(int(figure) for figure in figures)
            for key, figures in row.differing.items()
        },
    )


def _sign_off(row: Row[Any]) -> SignOffView:
    return SignOffView(
        id=row.id,
        key=row.key,
        figures=tuple(int(figure) for figure in row.figures),
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        state=row.outcome or "proposed",
        decided_by=row.decided_by,
        decision_reason=row.decision_reason,
    )


async def _control_totals(session: AsyncSession, as_of: date) -> TotalsView | None:
    row = (await session.execute(_LATEST_TOTALS, {"as_of": as_of})).one_or_none()
    if row is None:
        return None
    totals = row.totals

    def amounts(name: str) -> tuple[Money, ...]:
        return tuple(
            Money(int(totals[name][currency.value]), currency)
            for currency in Currency
            if currency.value in totals[name]
        )

    try:
        stored = ControlTotals(
            clients=int(totals["clients"]),
            verified_identities=int(totals["verified_identities"]),
            running_investments=int(totals["running_investments"]),
            pending_orders=int(totals["pending_orders"]),
            invested=amounts("invested"),
            interest_before_tax=amounts("interest_before_tax"),
        )
    except (KeyError, TypeError, ValueError, InvalidFinanceCheck):
        return None
    return TotalsView(row.id, row.as_of, row.entered_by, row.entered_at, row.confirmed_by, stored)


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    resource_type: str,
    resource_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details or {},
        ),
        clock,
    )
