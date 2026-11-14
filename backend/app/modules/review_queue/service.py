"""Reads every piece of waiting work the reader may see, inside the caller's staff transaction.

Only the sources the reader's permissions show are read, so a role never pays for, or sees,
another team's work.
"""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.service import StaffSession
from app.modules.investments import transfers
from app.modules.investments.domain.transfers import TransferStatus
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus
from app.modules.organisations import service as organisations
from app.modules.organisations.domain.applications import Status as OrganisationStatus
from app.modules.payments import bank_transfers
from app.modules.payments import service as payments
from app.modules.payments.domain.deposits import DepositStatus
from app.modules.payouts import destinations, payouts, resolutions, reviews
from app.modules.review_queue.domain.queue import Queue, QueueItem, queue, visible_kinds
from app.shared.business_day import KAMPALA, business_date

# Each source is read up to this many items. Past it, the owning screen lists the rest.
PER_SOURCE = 100
# Identity checks a person must decide. A check waiting for the client is not waiting for us.
_PERSON_DECIDES = frozenset({CheckStatus.NEEDS_REVIEW, CheckStatus.WITH_COMPLIANCE})


async def waiting_for(session: AsyncSession, staff: StaffSession, now: datetime) -> Queue:
    kinds = set(visible_kinds(staff.permissions))
    found: list[QueueItem] = []
    if "bank_transfer" in kinds:
        found += await _bank_transfers(session, staff)
    if "identity_check" in kinds:
        found += await _identity_checks(session)
    if "payout_account" in kinds:
        found += await _payout_accounts(session)
    if "held_payout" in kinds:
        found += await _held_payouts(session)
    if "payout_held_up" in kinds:
        found += await _held_up(session, now)
    if "payout_unanswered" in kinds:
        found += await _unanswered(session, now)
    if "investment_transfer" in kinds:
        found += await _investment_transfers(session, staff)
    if "organisation" in kinds:
        found += await _organisations(session)
    return queue(found, staff.permissions, await _cleared_today(session, now))


async def _cleared_today(session: AsyncSession, now: datetime) -> int:
    today = business_date(now)
    since = datetime(today.year, today.month, today.day, tzinfo=KAMPALA)
    until = since + timedelta(days=1)
    return await payments.confirmed_automatically(
        session, since=since, until=until
    ) + await payouts.paid_without_review(session, since=since, until=until)


async def _bank_transfers(session: AsyncSession, staff: StaffSession) -> list[QueueItem]:
    found: list[QueueItem] = []
    for deposit in await payments.list_deposits(
        session, status=DepositStatus.IN_REVIEW, limit=PER_SOURCE
    ):
        review = await bank_transfers.get_review(session, staff.actor, deposit.id)
        if review is None:
            reason, proposed_by = "find_on_statement", None
        elif review.outcome == "matched" and review.approved_by is None:
            reason, proposed_by = "match_to_approve", review.reviewed_by
        else:
            # Decided, and the posting is on its way.
            continue
        found.append(
            QueueItem(
                "bank_transfer",
                deposit.id,
                deposit.client_id,
                deposit.amount,
                reason,
                deposit.created_at,
                proposed_by,
            )
        )
    return found


async def _identity_checks(session: AsyncSession) -> list[QueueItem]:
    return [
        QueueItem(
            "identity_check",
            check.id,
            check.client_id,
            None,
            check.status.value,
            check.submitted_at,
        )
        for check in await kyc.review_queue(session, limit=PER_SOURCE)
        if check.status in _PERSON_DECIDES and check.submitted_at is not None
    ]


async def _payout_accounts(session: AsyncSession) -> list[QueueItem]:
    return [
        QueueItem(
            "payout_account",
            account.id,
            account.client_id,
            None,
            account.proof_kind.value if account.proof_kind else "no_proof",
            account.created_at,
            account.checked_by,
        )
        for account in await destinations.review_queue(session, limit=PER_SOURCE)
    ]


async def _held_payouts(session: AsyncSession) -> list[QueueItem]:
    return [
        QueueItem(
            "held_payout",
            held.payout.id,
            held.payout.client_id,
            held.payout.amount,
            "above_review_limit",
            held.payout.created_at,
            held.release.proposed_by if held.release else None,
        )
        for held in await reviews.held(session, limit=PER_SOURCE)
    ]


async def _held_up(session: AsyncSession, now: datetime) -> list[QueueItem]:
    return [
        QueueItem(
            "payout_held_up",
            item.payout.id,
            item.payout.client_id,
            item.payout.amount,
            "wallet_short" if item.waiting_for == "wallet_short" else "provider_took_nothing",
            item.first_tried_at,
        )
        for item in await payouts.held_up(session, now, limit=PER_SOURCE)
    ]


async def _unanswered(session: AsyncSession, now: datetime) -> list[QueueItem]:
    return [
        QueueItem(
            "payout_unanswered",
            item.payout.id,
            item.payout.client_id,
            item.payout.amount,
            "no_answer",
            item.first_sent_at,
            item.resolution.proposed_by if item.resolution else None,
        )
        for item in await resolutions.stranded(session, now, limit=PER_SOURCE)
    ]


async def _investment_transfers(session: AsyncSession, staff: StaffSession) -> list[QueueItem]:
    return [
        QueueItem(
            "investment_transfer",
            transfer.id,
            transfer.sender_client_id,
            transfer.amount,
            transfer.held_for.value if transfer.held_for else "size",
            transfer.requested_at,
            transfer.release_proposed_by
            if transfer.status is TransferStatus.RELEASE_PROPOSED
            else None,
        )
        for transfer in await transfers.held_transfers(session, staff.actor, limit=PER_SOURCE)
    ]


async def _organisations(session: AsyncSession) -> list[QueueItem]:
    return [
        QueueItem(
            "organisation",
            application.id,
            application.applicant_id,
            None,
            application.kind.value,
            application.submitted_at,
            application.checked_by if application.status is OrganisationStatus.CHECKED else None,
        )
        for application in await organisations.waiting(session)
        if application.submitted_at is not None
    ][:PER_SOURCE]
