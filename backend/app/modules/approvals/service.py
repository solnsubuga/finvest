"""Reads every waiting item the reader may see, inside the caller's staff transaction.

Only the sources the reader's permissions show are read, so a role never pays for, or sees,
another team's queue.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.approvals.domain.inbox import InboxItem, Kind, Waiting, inbox, visible_kinds
from app.modules.closures import service as closures
from app.modules.dealing import buffer, capital
from app.modules.dealing import orders as dealing
from app.modules.dealing.domain.capital import Direction
from app.modules.dealing.domain.orders import OrderStatus
from app.modules.finance import month_close
from app.modules.identity import client_locks, phone_changes, team
from app.modules.identity.service import StaffSession
from app.modules.investments import service as investments
from app.modules.investments.domain.rates import ProposalStatus
from app.modules.ledger import corrections
from app.modules.migration import service as migration
from app.modules.notifications import announcements, suppression_lifts, wording_store
from app.modules.organisations import service as organisations
from app.modules.payments import bank_accounts, bank_transfers, reassignments
from app.modules.payouts import destinations, resolutions, reviews
from app.modules.reconciliation import day_close, transfers
from app.modules.reports import schedules as report_schedules
from app.modules.reports.domain.catalogue import REPORTS, may_download
from app.modules.screening import service as screening
from app.modules.support import complaints
from app.modules.tax import service as tax


async def waiting_for(session: AsyncSession, staff: StaffSession, now: datetime) -> list[InboxItem]:
    kinds = visible_kinds(staff.permissions)
    found: list[Waiting] = []
    if "wording_change" in kinds:
        found += await _wordings(session)
    if "ledger_correction" in kinds:
        found += await _corrections(session)
    if "pot_transfer" in kinds:
        found += await _pot_transfers(session)
    if "bank_transfer_match" in kinds:
        found += await _bank_matches(session)
    if "day_close" in kinds:
        found += await _day_closes(session)
    if kinds & {"dealing_order", "dealing_result"}:
        found += await _dealing(session)
    if "buffer_rules" in kinds:
        found += await _buffer_rules(session)
    if "buffer_capital" in kinds:
        found += await _buffer_capital(session)
    if "rate_change" in kinds:
        found += await _rates(session, staff)
    if kinds & {"migration_totals", "migration_sign_off"}:
        found += await _migration(session)
    if "phone_change" in kinds:
        found += await _phone_changes(session, now)
    if "team_change" in kinds:
        found += await _team_changes(session)
    if "client_lock" in kinds:
        found += await _client_locks(session)
    if "suppression_lift" in kinds:
        found += await _suppression_lifts(session)
    if "complaint_outcome" in kinds:
        found += await _complaint_outcomes(session)
    if "announcement" in kinds:
        found += await _announcements(session)
    if "payout_account" in kinds:
        found += await _payout_accounts(session)
    if "investment_correction" in kinds:
        found += await _investment_corrections(session, staff)
    if "payout_resolution" in kinds:
        found += await _payout_resolutions(session)
    if "tax_remittance" in kinds:
        found += await _tax_remittances(session)
    if "deposit_reassignment" in kinds:
        found += await _reassignments(session)
    if "payout_release" in kinds:
        found += await _payout_releases(session)
    if "investment_transfer" in kinds:
        found += await _investment_transfers(session, staff)
    if "organisation" in kinds:
        found += await _organisations(session)
    if "account_closure" in kinds:
        found += await _closures(session)
    if "compliance_case" in kinds:
        found += await _compliance_cases(session)
    if "bank_account_change" in kinds:
        found += await _bank_accounts(session)
    if "report_schedule" in kinds:
        found += await _report_schedules(session, staff)
    if "month_close" in kinds:
        found += await _month_closes(session)
    return inbox(found, staff.staff_id, staff.permissions)


async def _wordings(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("wording_change", p.id, p.event, p.proposed_by, p.proposed_at)
        for w in await wording_store.list_wordings(session)
        if (p := w.proposal) is not None
    ]


async def _bank_accounts(session: AsyncSession) -> list[Waiting]:
    # The currency only. The details are shown on Settings, to people who may see them.
    return [
        Waiting("bank_account_change", p.id, p.currency.value, p.proposed_by, p.proposed_at)
        for a in await bank_accounts.list_accounts(session)
        if (p := a.proposal) is not None
    ]


async def _corrections(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("ledger_correction", c.id, c.entry_type, c.proposed_by, c.proposed_at)
        for c in await corrections.pending_corrections(session)
    ]


async def _pot_transfers(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("pot_transfer", t.id, t.terms.value_date.isoformat(), t.proposed_by, t.proposed_at)
        for t in await transfers.waiting(session)
    ]


async def _bank_matches(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("bank_transfer_match", m.deposit_id, m.deposit_id, m.matched_by, m.matched_at)
        for m in await bank_transfers.matches_awaiting_approval(session)
    ]


async def _day_closes(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("day_close", c.id, c.business_date.isoformat(), c.prepared_by, c.prepared_at)
        for c in await day_close.prepared_closes(session)
    ]


async def _dealing(session: AsyncSession) -> list[Waiting]:
    found: list[Waiting] = []
    for order in await dealing.open_orders(session):
        if order.status is OrderStatus.PREPARED:
            found.append(
                Waiting(
                    "dealing_order",
                    order.id,
                    order.product_code,
                    order.prepared_by,
                    order.prepared_at,
                )
            )
        elif (
            order.status is OrderStatus.RESULT_RECORDED
            and order.result_recorded_by is not None
            and order.result_recorded_at is not None
        ):
            found.append(
                Waiting(
                    "dealing_result",
                    order.id,
                    order.product_code,
                    order.result_recorded_by,
                    order.result_recorded_at,
                )
            )
    return found


async def _buffer_rules(session: AsyncSession) -> list[Waiting]:
    # "UGX from 2026-12-01": the currency and the day the new rules would start.
    return [
        Waiting(
            "buffer_rules",
            p.id,
            f"{p.rules.currency.value} from {p.rules.effective_from.isoformat()}",
            p.proposed_by,
            p.proposed_at,
        )
        for p in await buffer.waiting_proposals(session)
    ]


_MOVED = {Direction.IN: "paid in", Direction.OUT: "taken out"}


async def _buffer_capital(session: AsyncSession) -> list[Waiting]:
    # "UGX paid in" or "UGX taken out". The amount is on the buffer screen, not in the inbox.
    return [
        Waiting(
            "buffer_capital",
            m.id,
            f"{m.amount.currency.value} {_MOVED[m.direction]}",
            m.proposed_by,
            m.proposed_at,
        )
        for m in await capital.waiting(session)
    ]


async def _rates(session: AsyncSession, staff: StaffSession) -> list[Waiting]:
    return [
        Waiting("rate_change", p.id, p.product_code, p.proposed_by, p.proposed_at)
        for p in await investments.list_proposals(
            session, staff.actor, status=ProposalStatus.PROPOSED
        )
    ]


async def _investment_corrections(session: AsyncSession, staff: StaffSession) -> list[Waiting]:
    # The subject is the kind, "unwind" or "refund". Never the client or the amount.
    return [
        Waiting("investment_correction", c.id, c.kind.value, c.proposed_by, c.proposed_at)
        for c in await investments.list_corrections(
            session, staff.actor, status=investments.CorrectionStatus.PROPOSED, limit=200
        )
    ]


async def _migration(session: AsyncSession) -> list[Waiting]:
    view = await migration.overview(session)
    kind: Kind
    found: list[Waiting] = []
    if view.totals is not None and view.totals.confirmed_by is None:
        kind = "migration_totals"
        totals = view.totals
        found.append(
            Waiting(kind, totals.id, totals.as_of.isoformat(), totals.entered_by, totals.entered_at)
        )
    for sign_off in view.sign_offs.values():
        if sign_off.state == "proposed":
            kind = "migration_sign_off"
            found.append(
                Waiting(kind, sign_off.id, sign_off.key, sign_off.proposed_by, sign_off.proposed_at)
            )
    return found


async def _phone_changes(session: AsyncSession, now: datetime) -> list[Waiting]:
    # The subject is the client's ID, never the number.
    return [
        Waiting("phone_change", c.id, c.client_id, c.proposed_by, c.proposed_at)
        for c in await phone_changes.waiting(session, now)
    ]


async def _team_changes(session: AsyncSession) -> list[Waiting]:
    # The subject is the kind of change. The person it changes never decides it.
    return [
        Waiting("team_change", c.id, c.kind, c.proposed_by, c.proposed_at, about_staff=c.staff_id)
        for c in await team.open_changes(session)
    ]


async def _closures(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("account_closure", r.id, r.client_id, r.prepared_by, r.prepared_at)
        for r in await closures.open_requests(session)
        if r.status == "prepared" and r.prepared_by is not None and r.prepared_at is not None
    ]


async def _client_locks(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("client_lock", c.id, f"{c.kind} {c.client_id}", c.proposed_by, c.proposed_at)
        for c in await client_locks.open_locks(session)
    ]


async def _suppression_lifts(session: AsyncSession) -> list[Waiting]:
    # The subject is the start of the address's hash, never the address.
    return [
        Waiting(
            "suppression_lift", lift.id, lift.address_hash[:12], lift.proposed_by, lift.proposed_at
        )
        for lift in await suppression_lifts.open_lifts(session)
    ]


async def _complaint_outcomes(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("complaint_outcome", p.id, reference, p.proposed_by, p.proposed_at)
        for reference, p in await complaints.pending_outcomes(session)
    ]


async def _announcements(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("announcement", a.id, a.title, a.proposed_by, a.proposed_at)
        for a in await announcements.list_announcements(session)
        if a.status == "proposed"
    ]


async def _payout_accounts(session: AsyncSession) -> list[Waiting]:
    # The subject is the kind of account, never the number or the holder's name.
    return [
        Waiting("payout_account", d.id, d.kind.value, d.checked_by, d.checked_at)
        for d in await destinations.checks_awaiting_approval(session)
        if d.checked_by is not None and d.checked_at is not None
    ]


async def _payout_resolutions(session: AsyncSession) -> list[Waiting]:
    # The subject is the outcome proposed, never the client, the number or the amount.
    return [
        Waiting("payout_resolution", r.id, r.outcome.value, r.proposed_by, r.proposed_at)
        for r in await resolutions.waiting(session)
    ]


async def _tax_remittances(session: AsyncSession) -> list[Waiting]:
    # The subject is the month and currency, never an amount.
    return [
        Waiting(
            "tax_remittance",
            r.id,
            f"{r.month.year:04d}-{r.month.month:02d} {r.amount.currency.value}",
            r.proposed_by,
            r.proposed_at,
        )
        for r in await tax.waiting(session)
    ]


async def _reassignments(session: AsyncSession) -> list[Waiting]:
    # The subject is the deposit's ID, never a name or an amount.
    return [
        Waiting("deposit_reassignment", r.id, r.deposit_id, r.proposed_by, r.proposed_at)
        for r in await reassignments.proposals_awaiting_approval(session)
    ]


async def _payout_releases(session: AsyncSession) -> list[Waiting]:
    # The subject is the payout's ID, never the client, the account or the amount.
    return [
        Waiting("payout_release", r.id, r.payout_id, r.proposed_by, r.proposed_at)
        for r in await reviews.waiting(session)
    ]


async def _investment_transfers(session: AsyncSession, staff: StaffSession) -> list[Waiting]:
    # Only those with a release proposed wait for a second person. The subject is the
    # transfer's ID, never the clients or the amount.
    return [
        Waiting("investment_transfer", t.id, t.id, t.release_proposed_by, t.release_proposed_at)
        for t in await investments.held_transfers(session, staff.actor)
        if t.release_proposed_by is not None and t.release_proposed_at is not None
    ]


async def _compliance_cases(session: AsyncSession) -> list[Waiting]:
    # The subject is the proposed outcome, never the client or what the case is about.
    return [
        Waiting("compliance_case", p.id, p.outcome.value, p.proposed_by, p.proposed_at)
        for p in await screening.waiting_proposals(session)
    ]


async def _organisations(session: AsyncSession) -> list[Waiting]:
    # Only checked applications wait for a second person. The subject is the kind, never the
    # organisation's or a person's name.
    return [
        Waiting("organisation", o.id, o.kind.value, o.checked_by, o.checked_at)
        for o in await organisations.waiting(session)
        if o.checked_by is not None and o.checked_at is not None
    ]


async def _report_schedules(session: AsyncSession, staff: StaffSession) -> list[Waiting]:
    # The subject is the report and how often, never who it goes to.
    return [
        Waiting(
            "report_schedule",
            s.id,
            f"{s.report_key} {s.frequency}",
            s.proposed_by,
            s.proposed_at,
        )
        for s in await report_schedules.waiting(session)
        if may_download(REPORTS[s.report_key], staff.permissions)
    ]


async def _month_closes(session: AsyncSession) -> list[Waiting]:
    # The subject is the month.
    return [
        Waiting("month_close", c.id, f"{c.month:%Y-%m}", c.proposed_by, c.proposed_at)
        for c in await month_close.waiting(session)
    ]
