"""Reads every waiting item the reader may see, inside the caller's staff transaction.

Only the sources the reader's permissions show are read, so a role never pays for, or sees,
another team's queue.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.approvals.domain.inbox import InboxItem, Kind, Waiting, inbox, visible_kinds
from app.modules.dealing import orders as dealing
from app.modules.dealing.domain.orders import OrderStatus
from app.modules.identity.service import StaffSession
from app.modules.investments import service as investments
from app.modules.investments.domain.rates import ProposalStatus
from app.modules.ledger import corrections
from app.modules.migration import service as migration
from app.modules.notifications import wording_store
from app.modules.payments import bank_transfers
from app.modules.reconciliation import day_close, transfers


async def waiting_for(session: AsyncSession, staff: StaffSession) -> list[InboxItem]:
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
    if "rate_change" in kinds:
        found += await _rates(session, staff)
    if kinds & {"migration_totals", "migration_sign_off"}:
        found += await _migration(session)
    return inbox(found, staff.staff_id, staff.permissions)


async def _wordings(session: AsyncSession) -> list[Waiting]:
    return [
        Waiting("wording_change", p.id, p.event, p.proposed_by, p.proposed_at)
        for w in await wording_store.list_wordings(session)
        if (p := w.proposal) is not None
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


async def _rates(session: AsyncSession, staff: StaffSession) -> list[Waiting]:
    return [
        Waiting("rate_change", p.id, p.product_code, p.proposed_by, p.proposed_at)
        for p in await investments.list_proposals(
            session, staff.actor, status=ProposalStatus.PROPOSED
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
