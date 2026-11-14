"""Reading what a statement or a receipt says, from the ledger and the modules that own it.

Runs in the worker's transaction as the system. Nothing here writes. The figures come only
from the ledger; the other modules give names: the product of an investment, how a payment
was made, where a payout went, and whose account it is.
"""

import contextlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.domain import building
from app.modules.documents.domain.building import EntryChange, InvestmentFacts, Names
from app.modules.documents.domain.investment_notes import (
    AtMaturityFigures,
    EndedNote,
    Ending,
    InvestmentNote,
)
from app.modules.documents.domain.receipts import Receipt, masked
from app.modules.documents.domain.statements import (
    Activity,
    ActivityKind,
    Holding,
    Period,
    Statement,
    StatementKind,
    group_interest,
    summarise,
)
from app.modules.investments import holdings as investments
from app.modules.kyc import service as kyc
from app.modules.ledger import statements as ledger
from app.modules.payments import service as payments
from app.modules.payouts import payouts
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money


class NotReady(Exception):
    """The client has no approved account yet, so there is no name or number to print."""


@dataclass(frozen=True, slots=True)
class Holder:
    name: str  # "Amara NAKATO", given names then surname as on the ID
    first_name: str
    account_number: str


async def holder(session: AsyncSession, client_id: str) -> Holder:
    found = await kyc.account_holder(session, client_id)
    if found is None:
        raise NotReady("The client has no approved account")
    given = " ".join(found.given_names.split())
    return Holder(
        name=f"{given} {' '.join(found.surname.split())}".strip(),
        first_name=given.split(" ")[0] if given else found.surname,
        account_number=found.account_number,
    )


async def statement(
    session: AsyncSession,
    *,
    client_id: str,
    kind: StatementKind,
    period: Period,
    reference: str,
    issued_on: date,
    not_final_from: date | None = None,
) -> Statement:
    """The client's statement for `period`. Raises `StatementDoesNotAddUp` if the ledger's
    lines do not explain the change between its balances, and `NotReady` before approval."""
    account = await holder(session, client_id)
    opening = await ledger.client_owed_as_of(session, client_id, period.start - timedelta(days=1))
    closing = await ledger.client_owed_as_of(session, client_id, period.end)
    lines = await ledger.client_lines(
        session, client_id, date_from=period.start, date_to=period.end
    )
    changes = _changes(lines)
    names = await _names(session, changes, {owed.account_reference for owed in closing})
    activity = [
        line for change in changes if (line := building.activity_line(change, names)) is not None
    ]
    activity = group_interest(activity)
    currencies = sorted(
        {owed.owed.currency for owed in [*opening, *closing]}
        | {a.amount.currency for a in activity}
        or {Currency.UGX}
    )
    opening_totals = building.totals_by_currency(owed.owed for owed in opening)
    closing_totals = building.totals_by_currency(owed.owed for owed in closing)
    summaries = tuple(
        summarise(
            currency,
            opening=opening_totals.get(currency, Money.zero(currency)),
            closing=closing_totals.get(currency, Money.zero(currency)),
            activity=activity,
        )
        for currency in currencies
    )
    return Statement(
        reference=reference,
        kind=kind,
        client_name=account.name,
        account_number=account.account_number,
        period=period,
        issued_on=issued_on,
        summaries=summaries,
        holdings=await _holdings(session, period, opening, closing, activity, changes),
        activity=tuple(activity),
        not_final_from=not_final_from,
    )


async def receipt(
    session: AsyncSession, *, deposit_id: str, reference: str
) -> tuple[Receipt, Holder]:
    """The receipt for a confirmed deposit, and whose it is."""
    deposit = await payments.get_deposit(session, deposit_id)
    if deposit.status.value != "confirmed" or deposit.settled_at is None:
        raise ApplicationError("conflict")
    account = await holder(session, deposit.client_id)
    paid_for = "your investment"
    if deposit.purpose.value == "investment":
        # A name that cannot be found leaves a plain description, never a wrong one.
        with contextlib.suppress(ApplicationError):
            paid_for = (await investments.get_investment(session, deposit.purpose_id)).product_name
    return (
        Receipt(
            reference=reference,
            client_name=account.name,
            account_number=account.account_number,
            amount=deposit.amount,
            received_at=deposit.settled_at,
            method=building.method_name(deposit.method.value),
            paid_from=masked(deposit.payer_msisdn) if deposit.payer_msisdn else None,
            provider_reference=deposit.provider_reference,
            payment_reference=deposit.id,
            paid_for=paid_for,
        ),
        account,
    )


async def investment_note(
    session: AsyncSession, *, client_id: str, investment_id: str, reference: str, clock: Clock
) -> tuple[InvestmentNote, Holder]:
    """The note that confirms an investment started, read as its client sees it: after tax."""
    account = await holder(session, client_id)
    found = await investments.client_investment(
        session, Actor("client", client_id), investment_id, clock
    )
    if found.start_date is None:
        raise ApplicationError("conflict")  # it has not started, so there is nothing to confirm
    returns = found.at_maturity_returns
    fixed = found.term_days is not None and found.maturity_date is not None
    return (
        InvestmentNote(
            reference=reference,
            client_name=account.name,
            account_number=account.account_number,
            investment_id=found.id,
            product_name=found.product_name,
            amount=found.amount,
            rate_percent=found.rate_percent,
            start_date=found.start_date,
            term_days=found.term_days if fixed else None,
            maturity_date=found.maturity_date if fixed else None,
            at_maturity=AtMaturityFigures(returns.interest, returns.total)
            if fixed and returns is not None
            else None,
            then=building.at_maturity_wording(found.at_maturity) if fixed else None,
        ),
        account,
    )


async def ended_note(
    session: AsyncSession, *, client_id: str, investment_id: str, reference: str, clock: Clock
) -> tuple[EndedNote, Holder]:
    """The note that confirms an investment ended, read as its client sees it: after tax."""
    account = await holder(session, client_id)
    found = await investments.client_investment(
        session, Actor("client", client_id), investment_id, clock
    )
    status = found.status.value
    if found.start_date is None or status not in ("matured", "withdrawn"):
        raise ApplicationError("conflict")  # it has not ended, so there is nothing to confirm
    if status == "withdrawn":
        assert found.withdrawal_requested_at is not None  # a withdrawal is always asked for
        # Interest stops on the day the client asks, so that is the day it ended.
        ending, ended_on = Ending.WITHDRAWN, business_date(found.withdrawal_requested_at)
    else:
        assert found.maturity_date is not None  # only a fixed term matures
        ended_on = found.maturity_date
        if found.rolled_into_id is not None:
            ending = Ending.ROLLED_OVER
        elif found.payout_id is not None:
            ending = Ending.PAID_OUT
        else:
            ending = Ending.WAITING
    return (
        EndedNote(
            reference=reference,
            client_name=account.name,
            account_number=account.account_number,
            investment_id=found.id,
            product_name=found.product_name,
            amount=found.amount,
            interest=found.interest_so_far,
            start_date=found.start_date,
            ended_on=ended_on,
            ending=ending,
            rolled_into_id=found.rolled_into_id,
        ),
        account,
    )


def _changes(lines: list[ledger.ClientLine]) -> list[EntryChange]:
    """One change per entry and currency, in the order the entries were posted."""
    order: list[tuple[str, Currency]] = []
    owed: dict[tuple[str, Currency], Money] = {}
    refs: dict[tuple[str, Currency], list[str]] = defaultdict(list)
    first: dict[tuple[str, Currency], ledger.ClientLine] = {}
    for line in lines:
        key = (line.entry_id, line.owed.currency)
        if key not in owed:
            order.append(key)
            owed[key] = Money.zero(line.owed.currency)
            first[key] = line
        owed[key] = owed[key] + line.owed
        if line.account_reference and line.account_reference not in refs[key]:
            refs[key].append(line.account_reference)
    return [
        EntryChange(
            entry_id=first[key].entry_id,
            on=first[key].effective_date,
            entry_type=first[key].entry_type,
            source_type=first[key].source_type,
            source_id=first[key].source_id,
            owed=owed[key],
            investment_ids=tuple(refs[key]),
        )
        for key in order
    ]


async def _names(session: AsyncSession, changes: list[EntryChange], held: set[str | None]) -> Names:
    investment_ids = {ref for change in changes for ref in change.investment_ids} | {
        ref for ref in held if ref
    }
    products: dict[str, str] = {}
    for investment_id in sorted(investment_ids):
        try:
            products[investment_id] = (
                await investments.get_investment(session, investment_id)
            ).product_name
        except ApplicationError:
            continue  # an account reference that is not an investment
    methods: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for change in changes:
        if change.source_id is None or change.owed.is_zero:
            continue
        # A name that cannot be found leaves a plain description, never a wrong one.
        try:
            if change.entry_type == building.DEPOSIT_CONFIRMED and change.source_id not in methods:
                methods[change.source_id] = (
                    await payments.get_deposit(session, change.source_id)
                ).method.value
            if (
                change.entry_type.startswith(building.PAYOUT_PREFIX)
                and change.source_id not in kinds
            ):
                kinds[change.source_id] = (
                    await payouts.get_payout(session, change.source_id)
                ).kind.value
        except ApplicationError:
            continue
    return Names(products=products, deposit_methods=methods, payout_kinds=kinds)


async def _holdings(
    session: AsyncSession,
    period: Period,
    opening: list[ledger.ClientOwed],
    closing: list[ledger.ClientOwed],
    activity: list[Activity],
    changes: list[EntryChange],
) -> tuple[Holding, ...]:
    """Every investment the client held at either end of the period or that moved during it."""
    at_end: dict[str, Money] = {}
    for owed in closing:
        if owed.account_reference:
            reference = owed.account_reference
            at_end[reference] = at_end.get(reference, Money.zero(owed.owed.currency)) + owed.owed
    involved = (
        {
            owed.account_reference
            for owed in opening
            if owed.account_reference and not owed.owed.is_zero
        }
        | {reference for reference, value in at_end.items() if not value.is_zero}
        | {ref for change in changes if not change.owed.is_zero for ref in change.investment_ids}
    )
    earned: dict[str, Money] = {}
    for line in activity:
        if line.kind is ActivityKind.EARNED:
            earned[line.reference] = (
                earned.get(line.reference, Money.zero(line.amount.currency)) + line.amount
            )
    found: list[Holding] = []
    for reference in sorted(involved):
        try:
            investment = await investments.get_investment(session, reference)
        except ApplicationError:
            continue
        currency = investment.amount.currency
        found.append(
            building.holding(
                InvestmentFacts(
                    id=investment.id,
                    product=investment.product_name,
                    principal=investment.amount,
                    status=investment.status,
                    start_date=investment.start_date,
                    maturity_date=investment.maturity_date,
                    rolled_into_id=investment.rolled_into_id,
                    payout_id=investment.payout_id,
                    withdrawn_on=(
                        business_date(investment.withdrawn_at)
                        if investment.withdrawn_at is not None
                        else None
                    ),
                    withdrawal_payout_id=investment.withdrawal_payout_id,
                ),
                period=period,
                value_at_end=at_end.get(reference, Money.zero(currency)),
                earned=earned.get(reference, Money.zero(currency)),
            )
        )
    # Running ones first, newest start first; paid out ones last.
    found.sort(
        key=lambda item: (item.value_at_end.is_zero, -(item.started_on or date.max).toordinal())
    )
    return tuple(found)
