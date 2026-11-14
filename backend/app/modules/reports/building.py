"""Builds each report's table from the owning modules' read functions, inside the caller's
staff transaction. Read only."""

from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents import service as documents
from app.modules.identity import client_locks
from app.modules.identity.service import StaffSession
from app.modules.investments import figures
from app.modules.kyc import service as kyc
from app.modules.ledger import statements as ledger
from app.modules.payments import service as payments
from app.modules.payouts import payouts
from app.modules.reports.domain import complaints as complaint_lines
from app.modules.reports.domain.catalogue import MAX_ROWS, ReportKey, Table, TooManyRows
from app.modules.screening import service as screening
from app.modules.support import complaints
from app.modules.tax import withholding
from app.shared.business_day import KAMPALA, business_date
from app.shared.money import Currency, Money


def _start_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=KAMPALA)


def _minutes(start: datetime, end: datetime | None) -> int | None:
    return None if end is None else int((end - start).total_seconds() // 60)


async def build(
    session: AsyncSession,
    staff: StaffSession,
    key: ReportKey,
    currency: Currency,
    *,
    date_from: date,
    date_to: date,
    now: datetime,
) -> Table:
    if key == "daily_position":
        return await _daily_position(session, currency, date_from)
    if key == "deposits_and_withdrawals":
        return await _deposits_and_withdrawals(session, currency, date_from, date_to)
    if key == "interest_and_tax":
        return await _interest_and_tax(session, currency, date_from, date_to)
    if key == "maturity_ladder":
        return await _maturity_ladder(session, staff, currency, date_from, date_to)
    if key == "identity_and_risk":
        return await _identity_and_risk(session, now)
    if key == "client_statements":
        return await _client_statements(session, date_from)
    return await _complaints(session, date_from, date_to, now)


async def _daily_position(session: AsyncSession, currency: Currency, day: date) -> Table:
    found = await ledger.totals_by_code(session, currency, date_from=day, date_to=day)
    return Table(
        ("Date", "Currency", "Account code", "Account name", "Kind", "Balance at end of day"),
        [(day, currency.value, row.code, row.name, row.kind, row.closing) for row in found],
    )


async def _deposits_and_withdrawals(
    session: AsyncSession, currency: Currency, date_from: date, date_to: date
) -> Table:
    since, until = _start_of(date_from), _start_of(date_to + timedelta(days=1))
    deposits = await payments.deposits_requested_between(
        session, since=since, until=until, limit=MAX_ROWS + 1
    )
    paid = await payouts.payouts_requested_between(
        session, since=since, until=until, limit=MAX_ROWS + 1
    )
    rows: list[tuple[object, ...]] = []
    for deposit in deposits:
        if deposit.amount.currency is not currency:
            continue
        rows.append(
            (
                "deposit",
                deposit.id,
                deposit.client_id,
                deposit.method.value,
                currency.value,
                deposit.amount,
                deposit.status.value,
                deposit.code,
                deposit.created_at.astimezone(KAMPALA).isoformat(timespec="minutes"),
                None
                if deposit.settled_at is None
                else deposit.settled_at.astimezone(KAMPALA).isoformat(timespec="minutes"),
                _minutes(deposit.created_at, deposit.settled_at),
            )
        )
    for record in paid:
        payout = record.payout
        if payout.amount.currency is not currency:
            continue
        ended = (
            record.updated_at if payout.status.value in ("paid", "failed", "cancelled") else None
        )
        rows.append(
            (
                "payout",
                payout.id,
                payout.client_id,
                payout.kind.value,
                currency.value,
                payout.amount,
                payout.status.value,
                payout.failure_code,
                payout.created_at.astimezone(KAMPALA).isoformat(timespec="minutes"),
                None if ended is None else ended.astimezone(KAMPALA).isoformat(timespec="minutes"),
                _minutes(payout.created_at, ended),
            )
        )
    if len(rows) > MAX_ROWS:
        raise TooManyRows
    rows.sort(key=lambda row: (str(row[8]), str(row[1])))
    return Table(
        (
            "Type",
            "ID",
            "Client ID",
            "Channel",
            "Currency",
            "Amount",
            "Status",
            "Failure code",
            "Asked at",
            "Ended at",
            "Minutes to end",
        ),
        rows,
    )


async def _interest_and_tax(
    session: AsyncSession, currency: Currency, date_from: date, date_to: date
) -> Table:
    found = await withholding.by_investment(session, currency, date_from=date_from, date_to=date_to)
    zero = Money.zero(currency)
    rows: list[tuple[object, ...]] = [
        (
            item.investment_id,
            item.client_id,
            item.product_name,
            currency.value,
            item.gross,
            item.tax,
            item.gross - item.tax,
        )
        for item in found
    ]
    gross = sum((item.gross for item in found), zero)
    tax = sum((item.tax for item in found), zero)
    rows.append(("Total", "", "", currency.value, gross, tax, gross - tax))
    return Table(
        (
            "Investment ID",
            "Client ID",
            "Product",
            "Currency",
            "Interest before tax",
            "Tax withheld",
            "Interest after tax",
        ),
        rows,
    )


async def _maturity_ladder(
    session: AsyncSession, staff: StaffSession, currency: Currency, date_from: date, date_to: date
) -> Table:
    found = await figures.maturing(
        session, staff.actor, currency, date_from=date_from, date_to=date_to
    )
    return Table(
        ("Date", "Currency", "Investments", "Principal", "Set to pay out", "Set to roll over"),
        [
            (
                day.day,
                currency.value,
                day.investments,
                day.principal,
                day.pay_out_principal,
                day.principal - day.pay_out_principal,
            )
            for day in found
        ],
    )


async def _identity_and_risk(session: AsyncSession, now: datetime) -> Table:
    checks = await kyc.status_counts(session)
    accounts = await client_locks.status_counts(session)
    year_start = datetime(now.astimezone(KAMPALA).year, 1, 1, tzinfo=KAMPALA)
    cases = await screening.summary(session, now, year_start=year_start)
    rows: list[tuple[object, ...]] = [
        ("Identity checks", status.value, count) for status, count in checks.items()
    ]
    rows += [("Client accounts", status, count) for status, count in accounts.items()]
    rows += [
        ("Screening and monitoring", "open cases", cases.open_cases),
        ("Screening and monitoring", "open screening cases", cases.open_screening_cases),
        ("Screening and monitoring", "open monitoring cases", cases.open_monitoring_cases),
        (
            "Screening and monitoring",
            "waiting for a second person",
            cases.waiting_for_second_person,
        ),
        ("Screening and monitoring", "screened in the last 30 days", cases.screened_recently),
        (
            "Screening and monitoring",
            "could not be screened, last 30 days",
            cases.unavailable_recently,
        ),
        ("Screening and monitoring", "screens waiting to run", cases.pending),
        ("Screening and monitoring", "reported to the FIA this year", cases.reported_this_year),
    ]
    return Table(("Group", "Measure", "Count"), rows)


async def _client_statements(session: AsyncSession, first: date) -> Table:
    label = f"{first.year:04d}-{first.month:02d}"
    found = await documents.monthly_statements(session, label, limit=MAX_ROWS + 1)
    if len(found) > MAX_ROWS:
        raise TooManyRows
    return Table(
        ("Month", "Client ID", "Statement ID", "Status", "Failure code", "Asked at", "Finished at"),
        [
            (
                label,
                doc.client_id,
                doc.id,
                doc.status.value,
                doc.failure_code,
                doc.requested_at.astimezone(KAMPALA).isoformat(timespec="minutes"),
                None
                if doc.finished_at is None
                else doc.finished_at.astimezone(KAMPALA).isoformat(timespec="minutes"),
            )
            for doc in found
        ],
    )


async def _complaints(session: AsyncSession, first: date, last: date, now: datetime) -> Table:
    found = await complaints.in_period(
        session,
        since=_start_of(first),
        until=_start_of(last + timedelta(days=1)),
        today=business_date(now),
    )
    lines = complaint_lines.lines(
        [
            complaint_lines.Complaint(
                category=c.category,
                received_on=business_date(c.received_at),
                due_on=c.due_on,
                closed_on=None if c.closed_at is None else business_date(c.closed_at),
                outcome=c.outcome,
            )
            for c in found
        ],
        first=first,
        last=last,
    )
    return Table(
        (
            "Quarter from",
            "Quarter to",
            "Category",
            "Received",
            "Resolved",
            "Upheld",
            "Partly upheld",
            "Not upheld",
            "Open at end",
            "Past deadline",
            "Average working days to resolve",
        ),
        [
            (
                first,
                last,
                line.category,
                line.received,
                line.resolved,
                line.upheld,
                line.partly_upheld,
                line.not_upheld,
                line.open_at_end,
                line.past_deadline,
                line.average_days,
            )
            for line in lines
        ],
    )
