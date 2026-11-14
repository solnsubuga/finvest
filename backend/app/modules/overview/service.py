"""Reads the Overview's figures inside the caller's staff transaction.

Each section is read only when the reader's role may see it, so a role never pays for, or
sees, another team's figures.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.approvals import service as approvals
from app.modules.identity.service import StaffSession
from app.modules.investments import accrual, figures
from app.modules.investments import maturities as investment_maturities
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus
from app.modules.ledger import statements as ledger
from app.modules.overview.domain import figures as rules
from app.modules.payouts import payouts
from app.modules.reconciliation import day_close
from app.modules.review_queue import service as review_queue
from app.shared.business_day import KAMPALA, business_date
from app.shared.clock import Clock
from app.shared.money import Currency, Money

# The interest run for a day fires at 00:30 in Kampala. Past this, a missing run is a warning.
INTEREST_RUN_DUE = time(2, 0)


@dataclass(frozen=True, slots=True)
class InterestToday:
    run_date: date
    investments: int
    # Interest clients earned, after tax.
    net: Money
    finished_at: datetime | None
    behind: int


@dataclass(frozen=True, slots=True)
class MoneyFigures:
    client_money_held: Money
    in_transit: Money
    awaiting_start: Money
    clients_invested: int
    clients_verified: int
    interest: InterestToday | None
    days: list[rules.DayFlow]


@dataclass(frozen=True, slots=True)
class Overview:
    today: date
    currency: Currency
    money: MoneyFigures | None
    floats: list[rules.Float] | None
    balancing: rules.Balancing | None
    maturities: list[rules.LadderWeek] | None
    holdings: list[figures.ProductTotal] | None
    attention: list[rules.Attention]


async def overview(
    session: AsyncSession, staff: StaffSession, currency: Currency, clock: Clock
) -> Overview:
    now = clock.now()
    today = business_date(now)
    held = set(staff.permissions)
    found: list[rules.Attention] = []

    money = floats = balancing = ladder = holdings = None
    if "money_movement.view" in held:
        money = await _money(session, staff, currency, today)
        if currency is Currency.UGX:
            floats = rules.floats(
                await ledger.balances_by_reference(session, "iotec.wallet", Currency.UGX)
            )
            found += [
                rules.Attention("float_low", "warning", 1, subject=each.network)
                for each in floats
                if each.low
            ]
        waiting = [
            item
            for item in await payouts.held_up(session, now, limit=100)
            if item.waiting_for == "wallet_short"
        ]
        found.append(
            rules.Attention(
                "payouts_waiting_for_float",
                "warning",
                len(waiting),
                since=min((each.first_tried_at for each in waiting), default=None),
            )
        )
        if money.interest is not None:
            found.append(
                rules.Attention(
                    "interest_run_behind",
                    "warning",
                    money.interest.behind,
                    subject=money.interest.run_date.isoformat(),
                )
            )
        if await _interest_missing(money, now, today):
            found.append(
                rules.Attention("interest_run_missing", "warning", 1, subject=today.isoformat())
            )
    if "reconciliation.view" in held:
        prepared = await day_close.prepared_closes(session)
        balancing = rules.balancing(
            await day_close.last_closed(session),
            min((each.business_date for each in prepared), default=None),
            today=today,
        )
        found.append(
            rules.Attention(
                "books_not_balanced",
                "warning" if balancing.days_behind > 1 else "info",
                balancing.days_behind,
                subject=None
                if balancing.last_closed is None
                else balancing.last_closed.isoformat(),
            )
        )
    if "maturities.view" in held:
        first, last = rules.ladder_window(today)
        ladder = rules.ladder(
            [
                rules.MaturingDay(day.day, day.investments, day.principal, day.pay_out_principal)
                for day in await figures.maturing(
                    session, staff.actor, currency, date_from=first, date_to=last
                )
            ],
            currency,
            today=today,
        )
        not_closed = (
            await investment_maturities.maturities(session, staff.actor, clock)
        ).not_closed
        found.append(
            rules.Attention(
                "maturities_not_closed",
                "warning",
                len(not_closed),
                subject=min(
                    (i.maturity_date for i in not_closed if i.maturity_date is not None),
                    default=today,
                ).isoformat()
                if not_closed
                else None,
            )
        )
    if "investments.view" in held:
        holdings = await figures.running_by_product(session, staff.actor, currency)
    if "review_queue.view" in held:
        queue = await review_queue.waiting_for(session, staff, now)
        found.append(rules.Attention("review_queue", "info", len(queue.items), since=queue.oldest))
    inbox = await approvals.waiting_for(session, staff, now)
    deciding = [item for item in inbox if item.can_decide]
    found.append(
        rules.Attention(
            "approvals_for_you",
            "info",
            len(deciding),
            since=min((item.proposed_at for item in deciding), default=None),
        )
    )
    if balancing is not None and balancing.waiting_for_approval is not None:
        found.append(
            rules.Attention(
                "day_close_to_approve",
                "info",
                1,
                subject=balancing.waiting_for_approval.isoformat(),
            )
        )
    return Overview(
        today=today,
        currency=currency,
        money=money,
        floats=floats,
        balancing=balancing,
        maturities=ladder,
        holdings=holdings,
        attention=rules.attention(found),
    )


async def _money(
    session: AsyncSession, staff: StaffSession, currency: Currency, today: date
) -> MoneyFigures:
    zero = Money.zero(currency)
    owed = {each.code: each.total for each in await ledger.client_liabilities(session, currency)}
    flows = await ledger.day_source_totals(
        session,
        codes=list(rules.CASH_CODES),
        currency=currency,
        source_types=[rules.DEPOSIT_SOURCE, rules.PAYOUT_SOURCE],
        date_from=today - timedelta(days=rules.FLOW_DAYS - 1),
        date_to=today,
    )
    runs = [
        run
        for run in await accrual.interest_runs(session, staff.actor, limit=10)
        if run.currency is currency
    ]
    latest = runs[0] if runs else None
    return MoneyFigures(
        client_money_held=sum(owed.values(), zero),
        in_transit=sum((owed.get(code, zero) for code in rules.IN_TRANSIT_CODES), zero),
        awaiting_start=owed.get(rules.AWAITING_START_CODE, zero),
        clients_invested=await figures.running_clients(session, staff.actor),
        clients_verified=(await kyc.status_counts(session))[CheckStatus.APPROVED],
        interest=None
        if latest is None
        else InterestToday(
            run_date=latest.run_date,
            investments=latest.investments,
            net=latest.gross - latest.tax,
            finished_at=latest.finished_at,
            behind=latest.behind,
        ),
        days=rules.flows(
            [
                rules.SourceDay(row.effective_date, row.source_type, row.debits, row.credits)
                for row in flows
            ],
            currency,
            today=today,
        ),
    )


async def _interest_missing(money: MoneyFigures, now: datetime, today: date) -> bool:
    if money.clients_invested == 0:
        return False
    if now.astimezone(KAMPALA).time() < INTEREST_RUN_DUE:
        return False
    return money.interest is None or money.interest.run_date < today
