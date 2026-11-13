"""Early withdrawal: a client closes a running investment before its term ends.

The client asks once, naming an approved payout destination. Interest stops at that moment, so
the amount they were shown, principal plus the interest earned after tax so far, is the amount
paid (decided for now in docs/decisions.md). Nothing moves in the client's transaction: it
saves the request and a job.

The worker then moves the principal and the interest to the client's payout pending account in
one ledger entry, marks the investment withdrawn, and publishes `investments.withdrawn_early`
for the dealing desk, which funds it from the liquidity buffer or a sale of bills. When the desk
publishes `dealing.withdrawal_funded`, the worker creates the payout that sends the money. No
provider is called here: payouts sends it.
"""

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import JobRequest, enqueue, publish
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.dealing.events import FUND_EARLY_WITHDRAWAL
from app.modules.investments.catalogue import invalid
from app.modules.investments.domain.investments import InvestmentStatus, can_withdraw_early
from app.modules.investments.events import WITHDRAW_KIND
from app.modules.investments.holdings import (
    Investment,
    get_investment,
    investment_from_row,
    select_investments,
)
from app.modules.investments.maturity import out_of_holding
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import EntryDraft, credit
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain import posting as payout_posting
from app.modules.payouts.domain.limits import check_withdrawal
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money

WITHDRAWN_EARLY_TOPIC = "investments.withdrawn_early"
REQUEST_OPERATION = "investments.withdraw_early"
RESOURCE = "investment"
logger = structlog.get_logger()

_LOCKED = select_investments("WHERE i.id = :id FOR UPDATE OF i")
_REQUEST = text(
    # Stamped by PostgreSQL's own clock, which the database checks the request against.
    "UPDATE investments SET withdrawal_requested_at = clock_timestamp(), "
    "withdrawal_destination_id = :destination_id, updated_at = :now WHERE id = :id"
)
_WITHDRAW = text(
    "UPDATE investments SET status = 'withdrawn', withdrawn_at = :now, "
    "withdrawal_entry_id = :entry_id, updated_at = :now WHERE id = :id"
)
_RECORD_PAYOUT = text(
    "UPDATE investments SET withdrawal_payout_id = :payout_id, updated_at = :now WHERE id = :id"
)


@dataclass(frozen=True, slots=True)
class Funded:
    """What the dealing desk reports once it has the cash for a withdrawal."""

    funding_id: str
    investment_id: str
    amount: Money


def proceeds(investment: Investment) -> Money:
    """What an early withdrawal pays: the principal and the interest earned after tax."""
    return investment.amount + investment.accrued_net


# The client's command ---------------------------------------------------------------------------


async def request_withdrawal(
    session: AsyncSession,
    client: Actor,
    investment_id: str,
    *,
    destination_id: str,
    idempotency_key: str,
    clock: Clock,
) -> Investment:
    """Ask to withdraw a running investment early, to one of the client's payout destinations.

    A retry with the same key returns the same answer. The same key for another destination is
    refused. `conflict` if the investment is not running, was already asked for, or matures
    today or earlier.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")

    async def command() -> Receipt:
        investment = await _own_locked(session, client, investment_id)
        today = business_date(clock.now())
        if not can_withdraw_early(
            investment.status,
            investment.maturity_date,
            today,
            requested=investment.withdrawal_requested_at is not None,
        ):
            raise ApplicationError("conflict")
        now = clock.now()
        await _usable(
            session,
            investment.client_id,
            destination_id,
            investment.terms.principal + investment.accrued_net,
            now,
        )
        await session.execute(
            _REQUEST, {"id": investment.id, "destination_id": destination_id, "now": now}
        )
        await enqueue(
            session,
            JobRequest(
                kind=WITHDRAW_KIND,
                idempotency_key=investment.id,
                payload={"investment_id": investment.id},
            ),
            now=now,
        )
        await audit.record(
            session,
            AuditEvent(
                category="change",
                action="investments.withdrawal_requested",
                resource_type=RESOURCE,
                resource_id=investment.id,
                actor=AuditActor.client(investment.client_id),
                details={"destination_id": destination_id},
            ),
            clock,
        )
        logger.info("investment_withdrawal_requested", investment_id=investment.id)
        return Receipt(200, {"investment_id": investment.id})

    await run_once(
        session,
        actor=client,
        operation=REQUEST_OPERATION,
        key=idempotency_key,
        request={"investment_id": investment_id, "destination_id": destination_id},
        now=clock.now(),
        command=command,
    )
    return await get_investment(session, investment_id)


# The worker -------------------------------------------------------------------------------------


async def withdraw(session: AsyncSession, investment_id: str, clock: Clock) -> Investment:
    """Move a requested withdrawal's money to payouts and hand it to the dealing desk.

    Worker only. Calling again for one already withdrawn changes nothing and returns it.
    """
    investment = await _locked(session, investment_id)
    if investment.status is InvestmentStatus.WITHDRAWN:
        return investment
    if (
        investment.status is not InvestmentStatus.ACTIVE
        or investment.withdrawal_requested_at is None
    ):
        raise ApplicationError("conflict")
    now = clock.now()
    amount = proceeds(investment)
    handover = await ledger.open_account(
        session,
        payout_posting.funding_account(investment.client_id, investment.id, amount.currency),
        clock,
    )
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="investment.withdrawn_early",
            effective_date=business_date(now),
            lines=(*await out_of_holding(session, investment, clock), credit(handover.id, amount)),
            idempotency_key=f"investment-withdrawn:{investment.id}",
            source_type="investment",
            source_id=investment.id,
        ),
        clock,
    )
    await session.execute(_WITHDRAW, {"id": investment.id, "entry_id": entry.id, "now": now})
    await publish(
        session,
        topic=WITHDRAWN_EARLY_TOPIC,
        idempotency_key=investment.id,
        payload={
            "investment_id": investment.id,
            "client_id": investment.client_id,
            "product_code": investment.product_code,
            "principal_minor": investment.amount.minor,
            "currency": amount.currency.value,
            "amount_minor": amount.minor,
        },
        # The dealing desk funds it from the buffer or a sale, then announces
        # `dealing.withdrawal_funded`, which pays the client.
        consumers=[FUND_EARLY_WITHDRAWAL],
        now=now,
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="investments.withdrawn_early",
            resource_type=RESOURCE,
            resource_id=investment.id,
            actor=AuditActor.system(),
            details={"ledger_entry_id": entry.id},
        ),
        clock,
    )
    logger.info("investment_withdrawn_early", investment_id=investment.id)
    return await get_investment(session, investment.id)


async def pay_funded(session: AsyncSession, funded: Funded, clock: Clock) -> Investment:
    """The dealing desk has the cash: create the payout to the destination the client chose.

    Worker only. Safe to repeat: the payout's key is `early:<funding id>`, and one already
    recorded is left as it is. `conflict` if the investment was not withdrawn or the amount is
    not exactly what was withdrawn. A payout that payouts refuses is raised as `PayoutRefused`
    and the money stays in the client's payout pending account, for staff.
    """
    investment = await _locked(session, funded.investment_id)
    if investment.status is not InvestmentStatus.WITHDRAWN:
        raise ApplicationError("conflict")
    if investment.withdrawal_payout_id is not None:
        return investment
    amount = proceeds(investment)
    if funded.amount != amount or investment.withdrawal_destination_id is None:
        raise ApplicationError("conflict")
    payout = await payouts.create_payout(
        session,
        SYSTEM,
        client_id=investment.client_id,
        destination_id=investment.withdrawal_destination_id,
        amount=amount,
        source_reference=investment.id,
        request_key=f"early:{funded.funding_id}",
        clock=clock,
    )
    await session.execute(
        _RECORD_PAYOUT, {"id": investment.id, "payout_id": payout.id, "now": clock.now()}
    )
    logger.info("investment_withdrawal_paid", investment_id=investment.id, payout_id=payout.id)
    return await get_investment(session, investment.id)


def funded_from(data: dict[str, object]) -> Funded:
    """Read the dealing desk's `dealing.withdrawal_funded` payload. `ValueError` if it is not."""
    funding_id, investment_id = data.get("funding_id"), data.get("investment_id")
    amount_minor, currency = data.get("amount_minor"), data.get("currency")
    if not (
        isinstance(funding_id, str)
        and isinstance(investment_id, str)
        and isinstance(amount_minor, int)
        and not isinstance(amount_minor, bool)
        and isinstance(currency, str)
    ):
        raise ValueError("withdrawal_funded payload is incomplete")
    return Funded(funding_id, investment_id, Money(amount_minor, Currency(currency)))


async def _locked(session: AsyncSession, investment_id: str) -> Investment:
    row = (await session.execute(_LOCKED, {"id": investment_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return investment_from_row(row)


async def _own_locked(session: AsyncSession, client: Actor, investment_id: str) -> Investment:
    # Row policies show a client only their own: someone else's is simply not found.
    investment = await _locked(session, investment_id)
    if investment.client_id != client.id:
        raise ApplicationError("not_found")
    return investment


async def _usable(
    session: AsyncSession, client_id: str, destination_id: str, amount: Money, now: datetime
) -> None:
    try:
        view = await destinations.usable_destination(session, client_id, destination_id)
    except ApplicationError as error:
        if error.code != "not_found":
            raise
        raise invalid("destination_id", "not_usable") from error
    # The money may be ready at once, so a destination still in its first day's safety wait
    # is refused here rather than leaving the payout to fail when the desk has funded it.
    if view.payable_from is None or view.payable_from > now:
        raise invalid("destination_id", "destination_in_safety_wait")
    # Nor one that can never carry this amount in one payout: more than the account kind takes
    # at once or in a day, or a currency payouts does not send. Interest has stopped, so the
    # amount is final. Without this the money would move to the payout pending account and the
    # payout would then be refused for good. What else the client withdraws that day is
    # counted when the payout is made.
    limits = await payouts.current_limits(session, business_date(now))
    decision = check_withdrawal(limits, view.kind, amount, Money.zero(amount.currency))
    if decision.refusal is not None:
        raise invalid("destination_id", decision.refusal.value)
