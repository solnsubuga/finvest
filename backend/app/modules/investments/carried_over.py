"""Investments carried over from Finvest 1.0 at the switch.

Each running investment in the old system is opened here once, already running: its original
start date, the old system's rate and tax, and the interest earned up to the trial's "as of"
day, all in one ledger entry:

    debit   the migration opening balances account   principal plus interest before tax
    credit  client.holding                           the principal
    credit  client.interest_accrued                  the interest after tax
    credit  tax.withholding_payable                  the tax held back

The interest is worked out here with the same `posting_due` the daily run uses, never taken
from the caller, so there is one formula. The investment's running totals are set to match, so
the daily run carries on from the next day and never posts a day twice.

The legacy reference is the command's idempotency key: it is unique on the table, and a second
call with the same reference returns the investment the first one opened. Only the worker
opens one, and the database holds it to that (migration 0065). See docs/investing.md.
"""

from datetime import date

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.investments.accrual import INTEREST_ACCRUED, TAX_PAYABLE
from app.modules.investments.catalogue import invalid, product_from_row, product_row
from app.modules.investments.domain.interest import (
    Accrual,
    InvalidTerms,
    Terms,
    check_rate,
    posting_due,
)
from app.modules.investments.domain.investments import AtMaturity, pay_by, start
from app.modules.investments.domain.products import ProductStatus
from app.modules.investments.holdings import (
    HOLDING,
    INVESTMENT_RESOURCE,
    Investment,
    get_investment,
    investment_from_row,
    select_investments,
)
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Money
from app.shared.push_ids import push_id

ENTRY_TYPE = "investment.carried_over"
MAX_LEGACY_REFERENCE_LENGTH = 64
logger = structlog.get_logger()

_BY_LEGACY_REFERENCE = select_investments("WHERE i.legacy_reference = :reference")
# One reference is opened by one transaction at a time, so a retry racing the first call waits
# for it and then finds the investment it opened.
_REFERENCE_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")
_INSERT = text(
    "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
    "annual_rate_bps, tax_bps, term_days, at_maturity, status, created_at, pay_by, start_date, "
    "maturity_date, started_at, start_entry_id, accrued_through, accrued_gross_minor, "
    "accrued_tax_minor, legacy_reference, updated_at) VALUES "
    "(:id, :client_id, :product_id, :currency, :principal_minor, :annual_rate_bps, :tax_bps, "
    ":term_days, :at_maturity, 'active', :now, :pay_by, :start_date, :maturity_date, :now, "
    ":entry_id, :accrued_through, :gross, :tax, :legacy_reference, :now)"
)


async def open_existing_investment(
    session: AsyncSession,
    actor: Actor,
    *,
    client_id: str,
    product_code: str,
    principal: Money,
    start_date: date,
    annual_rate_bps: int,
    tax_bps: int,
    interest_through: date,
    opened_on: date,
    legacy_reference: str,
    opening_account: AccountSpec,
    at_maturity: AtMaturity | None = None,
    clock: Clock,
) -> Investment:
    """Open a running Finvest 1.0 investment, with its interest up to `interest_through`.

    Worker only. `opened_on` is the business day the entry is dated. `interest_through` is the
    last day of interest already earned (the trial's "as of" day): it is before today, on or
    after the start date, and, for a fixed term, before the maturity date, since only running
    investments are carried over. A fixed term's choice for maturity defaults to rolling over,
    as for a new one.

    Refused with `invalid` for terms or dates that cannot be right, `not_found` for an unknown
    product, and `conflict` if the reference was already opened with different terms.
    """
    if actor.kind != "system":
        raise ApplicationError("forbidden")
    if (
        not legacy_reference
        or len(legacy_reference) > MAX_LEGACY_REFERENCE_LENGTH
        or not legacy_reference[0].isalnum()
        or not all(c.isalnum() or c in "._/-" for c in legacy_reference)
        or not legacy_reference.isascii()
    ):
        raise invalid("legacy_reference", "invalid")
    await session.execute(
        _REFERENCE_LOCK, {"lock": f"finvest.investments.carried_over:{legacy_reference}"}
    )
    found = (
        await session.execute(_BY_LEGACY_REFERENCE, {"reference": legacy_reference})
    ).one_or_none()
    if found is not None:
        existing = investment_from_row(found)
        if (
            existing.client_id != client_id
            or existing.product_code != product_code
            or existing.amount != principal
            or existing.start_date != start_date
            or existing.terms.annual_rate_bps != annual_rate_bps
            or existing.terms.tax_bps != tax_bps
        ):
            raise ApplicationError("conflict")
        return existing

    row = await product_row(session, product_code)
    product = product_from_row(row)
    if product.status is ProductStatus.DRAFT:
        raise ApplicationError("not_found")
    if principal.currency is not product.currency or not principal.is_positive:
        raise invalid("principal", "invalid")
    try:
        check_rate(annual_rate_bps, tax_bps)
        terms = Terms(principal, annual_rate_bps=annual_rate_bps, tax_bps=tax_bps)
    except InvalidTerms as error:
        raise invalid("annual_rate_bps", "invalid") from error
    if product.has_fixed_term:
        at_maturity = at_maturity or AtMaturity.ROLL_OVER
    elif at_maturity is not None:
        raise invalid("at_maturity", "open_term_has_no_maturity")
    if (
        opening_account.kind is not AccountKind.ASSET
        or opening_account.currency is not principal.currency
        or opening_account.owner_client_id is not None
    ):
        raise invalid("opening_account", "invalid")

    now = clock.now()
    today = business_date(now)
    dates = start(start_date, product.term_days)
    if not start_date <= interest_through < today:
        raise invalid("interest_through", "invalid")
    if dates.maturity_date is not None and interest_through >= dates.maturity_date:
        raise invalid("interest_through", "already_matured")
    if opened_on > today:
        raise invalid("opened_on", "invalid")

    nothing = Money.zero(principal.currency)
    posting = posting_due(
        terms,
        start=start_date,
        term_days=product.term_days,
        posted=Accrual(gross=nothing, tax=nothing),
        posted_through=None,
        run_date=interest_through,
    )
    earned = posting.total if posting is not None else Accrual(gross=nothing, tax=nothing)

    investment_id = push_id()
    opening = await ledger.open_account(session, opening_account, clock)
    holding = await ledger.open_account(
        session,
        AccountSpec(
            HOLDING,
            AccountKind.LIABILITY,
            principal.currency,
            "Client holding",
            owner_client_id=client_id,
            reference=investment_id,
        ),
        clock,
    )
    lines = [debit(opening.id, principal + earned.gross), credit(holding.id, principal)]
    if earned.net.is_positive:
        accrued = await ledger.open_account(
            session,
            AccountSpec(
                INTEREST_ACCRUED,
                AccountKind.LIABILITY,
                principal.currency,
                "Interest earned, not yet paid",
                owner_client_id=client_id,
                reference=investment_id,
            ),
            clock,
        )
        lines.append(credit(accrued.id, earned.net))
    if earned.tax.is_positive:
        payable = await ledger.open_account(
            session,
            AccountSpec(
                TAX_PAYABLE, AccountKind.LIABILITY, principal.currency, "Tax withheld from interest"
            ),
            clock,
        )
        lines.append(credit(payable.id, earned.tax))
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type=ENTRY_TYPE,
            effective_date=opened_on,
            lines=tuple(lines),
            idempotency_key=f"investment-carried-over:{investment_id}",
            source_type="investment",
            source_id=investment_id,
        ),
        clock,
    )
    await session.execute(
        _INSERT,
        {
            "id": investment_id,
            "client_id": client_id,
            "product_id": row.id,
            "currency": principal.currency.value,
            "principal_minor": principal.minor,
            "annual_rate_bps": annual_rate_bps,
            "tax_bps": tax_bps,
            "term_days": product.term_days,
            "at_maturity": at_maturity.value if at_maturity else None,
            "now": now,
            # Never paid for: the window is only there because every row has one.
            "pay_by": pay_by(now),
            "start_date": dates.start_date,
            "maturity_date": dates.maturity_date,
            "entry_id": entry.id,
            "accrued_through": interest_through,
            "gross": earned.gross.minor,
            "tax": earned.tax.minor,
            "legacy_reference": legacy_reference,
        },
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="investments.carried_over",
            resource_type=INVESTMENT_RESOURCE,
            resource_id=investment_id,
            actor=AuditActor.system(),
            details={
                "legacy_reference": legacy_reference,
                "start_date": start_date.isoformat(),
                "interest_through": interest_through.isoformat(),
                "ledger_entry_id": entry.id,
            },
        ),
        clock,
    )
    logger.info("investment_carried_over", investment_id=investment_id)
    return await get_investment(session, investment_id)
