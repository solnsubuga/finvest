"""Withholding tax over HTTP, for staff with `finance.view`.

`GET /staff/tax` is the screen for one currency: what is held for URA now, each of the last
twelve months against what was paid for it and when it is due, and the payments recorded.
`GET /staff/tax/months/{month}` is one month's return, client by client, with names, account
numbers and TINs, and `.csv` is the same as a file for URA's schedule. Reading either is on the
audit trail, since both show clients' tax numbers.

Recording a payment to URA takes `tax_remittances.change`. It is recorded by one member of staff
and approved by a different one, who must have confirmed it is them in the last few minutes.
Every command takes an `Idempotency-Key` and answers with the screen as it now stands.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.sealing import Sealer
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession, staff_names
from app.modules.tax import remittances, withholding
from app.modules.tax.domain.exports import PayeeRow, payee_csv
from app.modules.tax.domain.periods import (
    BadPeriod,
    month_label,
    month_of,
    months_back,
    parse_month,
)
from app.modules.tax.domain.remittances import RemittanceStatus
from app.modules.tax.domain.withholding import MonthSummary
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

MAX_AMOUNT_MINOR = 10**15
MONTHS_SHOWN = 12
_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422)
}

AmountMinor = Annotated[int, Field(ge=1, le=MAX_AMOUNT_MINOR)]
CurrencyIn = Annotated[Currency, Field(strict=False)]
DateIn = Annotated[date, Field(strict=False)]
MonthIn = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}$")]
Note = Annotated[str, Field(min_length=1, max_length=500)]
Reference = Annotated[str, Field(min_length=1, max_length=100)]


class Step(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"


# Contracts --------------------------------------------------------------------------------------


class AmountOut(Contract):
    minor: int
    currency: Currency


class MonthOut(Contract):
    month: str  # YYYY-MM
    withheld: AmountOut
    remitted: AmountOut
    waiting: AmountOut
    # Negative when more was paid than the month now owes: a credit against the next payment.
    outstanding: AmountOut
    due_on: date
    # True once the due date has passed with something still to pay.
    overdue: bool


class RemittanceOut(Contract):
    id: str
    month: str
    amount: AmountOut
    ura_reference: str
    bank_reference: str
    paid_on: date
    note: str
    status: RemittanceStatus
    proposed_by: str
    proposed_by_name: str | None
    proposed_at: datetime
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    allowed: list[Step]


class TaxScreenOut(Contract):
    currency: Currency
    # Withheld and not yet paid to URA, across every month: the ledger's balance.
    held_for_ura: AmountOut
    months: list[MonthOut]
    remittances: list[RemittanceOut]


class PayeeOut(Contract):
    client_id: str
    name: str | None
    account_number: str | None
    tin: str | None
    gross: AmountOut
    tax: AmountOut


class MonthReportOut(Contract):
    month: str
    currency: Currency
    gross: AmountOut
    withheld: AmountOut
    summary: MonthOut
    payees: list[PayeeOut]
    # False when this server cannot open tax numbers, so every `tin` is null.
    tins_shown: bool


class RemittanceIn(Contract):
    currency: CurrencyIn
    month: MonthIn
    amount_minor: AmountMinor
    # URA's payment registration number (PRN) for this payment.
    ura_reference: Reference
    bank_reference: Reference
    paid_on: DateIn
    note: Note


class DeclineIn(Contract):
    reason: Note


# Wiring -----------------------------------------------------------------------------------------


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
FinanceStaff = Annotated[StaffSession, Depends(require_permission("finance.view"))]
Recorder = Annotated[StaffSession, Depends(require_permission("tax_remittances.change"))]
Approver = Annotated[StaffSession, Depends(require_confirmed("tax_remittances.change"))]


def _amount(money: Money) -> AmountOut:
    return AmountOut(minor=money.minor, currency=money.currency)


def _month(text: str) -> date:
    try:
        return parse_month(text)
    except BadPeriod:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["month"], code="bad_month")]
        ) from None


def _month_out(summary: MonthSummary, today: date) -> MonthOut:
    return MonthOut(
        month=month_label(summary.month),
        withheld=_amount(summary.withheld),
        remitted=_amount(summary.remitted),
        waiting=_amount(summary.waiting),
        outstanding=_amount(summary.outstanding),
        due_on=summary.due_on,
        overdue=summary.outstanding.is_positive and today > summary.due_on,
    )


def _steps(staff: StaffSession, view: remittances.RemittanceView) -> list[Step]:
    if view.status is not RemittanceStatus.PROPOSED:
        return []
    if "tax_remittances.change" not in staff.permissions or view.proposed_by == staff.staff_id:
        return []
    return [Step.APPROVE, Step.DECLINE]


async def _screen(
    session: AsyncSession, staff: StaffSession, currency: Currency, clock: Clock
) -> TaxScreenOut:
    today = business_date(clock.now())
    this_month = month_of(today)
    found = await remittances.month_summaries(
        session,
        currency,
        date_from=months_back(this_month, MONTHS_SHOWN - 1),
        date_to=this_month,
    )
    recorded = await remittances.remittances(session, currency)
    names = await staff_names(
        session,
        {r.proposed_by for r in recorded} | {r.decided_by for r in recorded if r.decided_by},
    )
    return TaxScreenOut(
        currency=currency,
        held_for_ura=_amount(await remittances.held_for_ura(session, currency)),
        months=[_month_out(summary, today) for summary in found],
        remittances=[
            RemittanceOut(
                id=r.id,
                month=month_label(r.month),
                amount=_amount(r.amount),
                ura_reference=r.ura_reference,
                bank_reference=r.bank_reference,
                paid_on=r.paid_on,
                note=r.note,
                status=r.status,
                proposed_by=r.proposed_by,
                proposed_by_name=names.get(r.proposed_by),
                proposed_at=r.proposed_at,
                decided_by=r.decided_by,
                decided_by_name=names.get(r.decided_by) if r.decided_by else None,
                decided_at=r.decided_at,
                decision_reason=r.decision_reason,
                allowed=_steps(staff, r),
            )
            for r in recorded
        ],
    )


async def _report(
    session: AsyncSession,
    sealer: Sealer | None,
    staff: StaffSession,
    currency: Currency,
    month: date,
    clock: Clock,
    *,
    action: str,
) -> withholding.MonthReport:
    report = await withholding.month_report(session, sealer, currency, month)
    await audit.record(
        session,
        AuditEvent(
            category="access",
            action=action,
            resource_type="tax_month",
            resource_id=f"{currency.value}:{month_label(month)}",
            actor=AuditActor.staff(staff.staff_id),
            details={"payees": len(report.payees)},
        ),
        clock,
    )
    return report


def create_router(sealer: Sealer | None) -> APIRouter:
    router = APIRouter(prefix="/staff/tax", tags=["tax"], responses=_REFUSALS)

    @router.get("")
    async def get_tax(
        request: Request,
        staff: FinanceStaff,
        clock: AppClock,
        currency: Annotated[Currency, Query()] = Currency.UGX,
    ) -> TaxScreenOut:
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            return await _screen(session, staff, currency, clock)

    @router.get("/months/{month}", summary="One month's withholding, client by client")
    async def get_month(
        request: Request,
        staff: FinanceStaff,
        clock: AppClock,
        month: Annotated[str, Path(pattern=r"^[0-9]{4}-[0-9]{2}$")],
        currency: Annotated[Currency, Query()] = Currency.UGX,
    ) -> MonthReportOut:
        first = _month(month)
        today = business_date(clock.now())
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            report = await _report(
                session, sealer, staff, currency, first, clock, action="tax.month_viewed"
            )
            summary = await remittances.month_summary(session, currency, first)
        return MonthReportOut(
            month=month_label(first),
            currency=currency,
            gross=_amount(report.gross),
            withheld=_amount(report.withheld),
            summary=_month_out(summary, today),
            payees=[
                PayeeOut(
                    client_id=p.client_id,
                    name=p.name,
                    account_number=p.account_number,
                    tin=p.tin,
                    gross=_amount(p.gross),
                    tax=_amount(p.tax),
                )
                for p in report.payees
            ],
            tins_shown=report.tins_shown,
        )

    @router.get(
        "/months/{month}/payees.csv",
        response_class=Response,
        responses={200: {"content": {"text/csv": {}}}},
        summary="One month's withholding as a CSV file for URA's return",
    )
    async def export_month(
        request: Request,
        staff: FinanceStaff,
        clock: AppClock,
        month: Annotated[str, Path(pattern=r"^[0-9]{4}-[0-9]{2}$")],
        currency: Annotated[Currency, Query()] = Currency.UGX,
    ) -> Response:
        first = _month(month)
        database: Database = request.app.state.database
        async with database.actor_transaction(staff.actor) as session:
            report = await _report(
                session, sealer, staff, currency, first, clock, action="tax.month_exported"
            )
        content = payee_csv(
            first,
            [
                PayeeRow(p.name or "", p.tin, p.account_number, p.gross, p.tax)
                for p in report.payees
            ],
        )
        name = f"finvest-withholding-{currency.value.lower()}-{month_label(first)}.csv"
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )

    async def command(
        request: Request,
        staff: StaffSession,
        clock: Clock,
        *,
        operation: str,
        key: str,
        body: dict[str, object],
        run: Callable[[AsyncSession], Awaitable[Currency]],
    ) -> TaxScreenOut:
        database: Database = request.app.state.database
        actor = staff.actor
        async with database.actor_transaction(actor) as session:

            async def once() -> Receipt:
                currency = await run(session)
                return Receipt(200, {"currency": currency.value})

            outcome = await run_once(
                session,
                actor=actor,
                operation=operation,
                key=key,
                request=body,
                now=clock.now(),
                command=once,
            )
            saved = outcome.receipt.response.get("currency")
            currency = Currency(saved) if isinstance(saved, str) else Currency.UGX
            return await _screen(session, staff, currency, clock)

    @router.post("/remittances", summary="Record a payment to URA for a second person to approve")
    async def propose_remittance(
        request: Request, staff: Recorder, clock: AppClock, key: IdempotencyKey, body: RemittanceIn
    ) -> TaxScreenOut:
        month = _month(body.month)

        async def run(session: AsyncSession) -> Currency:
            view = await remittances.propose(
                session,
                staff.actor,
                month=month,
                amount=Money(body.amount_minor, body.currency),
                ura_reference=body.ura_reference,
                bank_reference=body.bank_reference,
                paid_on=body.paid_on,
                note=body.note,
                clock=clock,
            )
            return view.amount.currency

        return await command(
            request, staff, clock, operation="tax.propose_remittance", key=key,
            body=body.model_dump(mode="json"), run=run,
        )  # fmt: skip

    @router.post(
        "/remittances/{remittance_id}/approve",
        summary="Approve a payment to URA as the second person, which posts it",
    )
    async def approve_remittance(
        request: Request, staff: Approver, clock: AppClock, key: IdempotencyKey, remittance_id: str
    ) -> TaxScreenOut:
        async def run(session: AsyncSession) -> Currency:
            view = await remittances.approve(session, staff.actor, remittance_id, clock)
            return view.amount.currency

        return await command(
            request, staff, clock, operation="tax.approve_remittance", key=key,
            body={"remittance_id": remittance_id}, run=run,
        )  # fmt: skip

    @router.post("/remittances/{remittance_id}/decline", summary="Decline a payment to URA")
    async def decline_remittance(
        request: Request,
        staff: Recorder,
        clock: AppClock,
        key: IdempotencyKey,
        remittance_id: str,
        body: DeclineIn,
    ) -> TaxScreenOut:
        async def run(session: AsyncSession) -> Currency:
            view = await remittances.decline(
                session, staff.actor, remittance_id, body.reason, clock
            )
            return view.amount.currency

        return await command(
            request, staff, clock, operation="tax.decline_remittance", key=key,
            body={"remittance_id": remittance_id, "reason": body.reason}, run=run,
        )  # fmt: skip

    return router
