"""Carry each client over from Finvest 1.0 once the switch is approved. Worker only.

The approval created every client's account. The worker then carries each client over in one
transaction of its own, acting as the system:

1. Their identity, approved, with a new account number, when Finvest 1.0 checked a document that
   has not expired. Otherwise they do the full ID check when they claim.
2. Each running investment, opened in the ledger with its original start date, rate and tax and
   the interest earned up to the trial's "as of" day. That interest must be what the finance
   check showed, or nothing of the client is kept.
3. Their claim, and the one-time link, sent by text and, when there is an address, by email.

A client whose claim exists is done, so a run that stops part way carries on where it was. When
one of those steps is refused, nothing of that client is kept, the refusal's code is noted in
`migration_carry_problems`, and the run moves on. Orders still awaiting payment are never opened.

Nothing here logs or audits anything from the sheets. The token of a link goes only into the
message that carries it.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer
from app.modules.investments import carried_over
from app.modules.investments import service as investments
from app.modules.investments.domain.products import Product
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import IdentityDetails
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.migration import claims, service, switch
from app.modules.migration.domain.claim import LINK_LIFETIME
from app.modules.migration.domain.finance import opening_for
from app.modules.migration.domain.sheet import ClientRow, InvestmentRow, InvestmentStatus
from app.modules.migration.domain.trial import run_trial
from app.modules.notifications.service import queue_message
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency

OPENING_BALANCES = "migration.opening_balances"
# The client app's page that opens a claim link. The token follows the `#`, so it never reaches
# a server log.
CLAIM_PATH = "/claim"
DOCUMENT_COUNTRY = "UG"
CARRIED_OVER_REASON = "Identity document checked by Finvest 1.0"

logger = structlog.get_logger()

_CARRIED = text(
    "SELECT legacy_client_ref, client_id FROM migration_carried_clients WHERE switch_id = :id"
)
_PROBLEMS = text(
    "SELECT legacy_client_ref, code FROM migration_carry_problems WHERE switch_id = :id "
    "ORDER BY legacy_client_ref"
)
_NOTE_PROBLEM = text(
    "INSERT INTO migration_carry_problems (switch_id, legacy_client_ref, code, noted_at) "
    "VALUES (:switch_id, :ref, :code, :now) ON CONFLICT DO NOTHING"
)
_REMOVE_SHEETS = text("DELETE FROM migration_switch_files WHERE switch_id = :id")


Fence = Callable[[AsyncSession], Awaitable[None]]


async def _unfenced(session: AsyncSession) -> None:
    return None


class NotCarried(Exception):
    """One client could not be carried over. `code` says why and holds nothing personal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CarryResult:
    carried: int
    problems: int


def opening_account(currency: Currency) -> AccountSpec:
    """What every carried-over balance is opened against, one per currency."""
    return AccountSpec(
        OPENING_BALANCES, AccountKind.ASSET, currency, "Finvest 1.0 balances carried over"
    )


async def carry_over(
    database: Database,
    sealer: Sealer,
    clock: Clock,
    switch_id: str,
    *,
    client_app: str,
    fence: Fence = _unfenced,
) -> CarryResult:
    """Carry over every client of an approved switch who is not done yet.

    `client_app` is the client app's origin, which the links point to. `fence` runs first in
    each transaction that changes something: the worker passes its job's lease check, so a
    worker that lost the job stops. Safe to run again: done clients and noted problems are
    skipped.
    """
    async with database.actor_transaction(SYSTEM) as session:
        proposal = await switch.switch_by_id(session, switch_id)
        if proposal.state != "approved":
            raise ApplicationError("conflict")
        sheets = await switch.open_sheets(session, sealer, switch_id)
        carried = {
            row.legacy_client_ref: row.client_id
            for row in await session.execute(_CARRIED, {"id": switch_id})
        }
        noted = {
            row.legacy_client_ref for row in await session.execute(_PROBLEMS, {"id": switch_id})
        }
        views = await investments.list_products(session, clock)
    products = {view.product.code: view.product for view in views}
    report = run_trial(
        sheets.clients_csv, sheets.investments_csv, as_of=proposal.as_of, products=products
    )
    held: dict[str, list[InvestmentRow]] = {}
    for investment in report.investments:
        held.setdefault(investment.legacy_client_ref, []).append(investment)

    for client in report.clients:
        if client.legacy_client_ref in noted:
            continue
        client_id = carried[client.legacy_client_ref]
        try:
            async with database.actor_transaction(SYSTEM) as session:
                await fence(session)
                await _carry_client(
                    session,
                    sealer,
                    clock,
                    client,
                    client_id,
                    held.get(client.legacy_client_ref, []),
                    products,
                    as_of=proposal.as_of,
                    client_app=client_app,
                )
        except NotCarried as refused:
            await _note(database, clock, fence, switch_id, client.legacy_client_ref, refused.code)
        except ApplicationError as refused:
            await _note(database, clock, fence, switch_id, client.legacy_client_ref, refused.code)
    async with database.actor_transaction(SYSTEM) as session:
        problems = len((await session.execute(_PROBLEMS, {"id": switch_id})).all())
    return CarryResult(len(report.clients) - problems, problems)


async def remove_sheets(session: AsyncSession, switch_id: str) -> None:
    """Every client is carried over or noted: the sealed sheets are no longer needed."""
    await session.execute(_REMOVE_SHEETS, {"id": switch_id})


async def carry_problems(session: AsyncSession, switch_id: str) -> list[tuple[str, str]]:
    """Each client the switch could not carry over, by Finvest 1.0 reference, with the code."""
    rows = await session.execute(_PROBLEMS, {"id": switch_id})
    return [(row.legacy_client_ref, row.code) for row in rows]


async def _carry_client(
    session: AsyncSession,
    sealer: Sealer,
    clock: Clock,
    client: ClientRow,
    client_id: str,
    held: list[InvestmentRow],
    products: Mapping[str, Product],
    *,
    as_of: date,
    client_app: str,
) -> None:
    if await claims.claim_for(session, client_id) is not None:
        return  # carried over by an earlier run
    today = business_date(clock.now())
    needs_id_check = client.needs_id_check(today) or client.date_of_birth is None
    if not needs_id_check:
        assert client.id_type is not None and client.id_number is not None
        assert client.date_of_birth is not None
        try:
            await kyc.record_carried_over_check(
                session,
                sealer,
                SYSTEM,
                client_id=client_id,
                document_type=client.id_type,
                document_country=DOCUMENT_COUNTRY,
                details=IdentityDetails(
                    surname=client.surname,
                    given_names=client.given_names,
                    document_number=client.id_number,
                    date_of_birth=client.date_of_birth,
                    document_expires_on=client.id_expires_on,
                ),
                legacy_reference=client.legacy_client_ref,
                reason=CARRIED_OVER_REASON,
                clock=clock,
            )
        except ApplicationError as error:
            codes = {problem.code for problem in error.fields}
            if error.code == "validation_failed" and codes == {"document_expired"}:
                # Expired since the trial: the client does the full check when they claim.
                needs_id_check = True
            else:
                raise NotCarried(next(iter(sorted(codes)), error.code)) from None

    for investment in held:
        if investment.status is not InvestmentStatus.RUNNING:
            continue  # an order awaiting payment is placed again after claiming
        await _open(session, clock, client_id, investment, products, as_of=as_of, today=today)

    await claims.create_claim(
        session,
        SYSTEM,
        clock,
        client_id=client_id,
        legacy_client_ref=client.legacy_client_ref,
        needs_id_check=needs_id_check,
    )
    token = await claims.send_link(session, SYSTEM, clock, client_id)
    await _invite(session, clock, client, client_id, f"{client_app}{CLAIM_PATH}#{token}")


async def _open(
    session: AsyncSession,
    clock: Clock,
    client_id: str,
    investment: InvestmentRow,
    products: Mapping[str, Product],
    *,
    as_of: date,
    today: date,
) -> None:
    assert investment.start_date is not None
    assert investment.annual_rate_bps is not None and investment.tax_bps is not None
    expected = opening_for(investment, products[investment.product_code], opened_on=as_of)
    try:
        opened = await carried_over.open_existing_investment(
            session,
            SYSTEM,
            client_id=client_id,
            product_code=investment.product_code,
            principal=investment.principal,
            start_date=investment.start_date,
            annual_rate_bps=investment.annual_rate_bps,
            tax_bps=investment.tax_bps,
            interest_through=as_of,
            opened_on=today,
            legacy_reference=investment.legacy_investment_ref,
            opening_account=opening_account(investment.principal.currency),
            clock=clock,
        )
    except ApplicationError as error:
        codes = sorted(problem.code for problem in error.fields)
        raise NotCarried(
            f"investment_{codes[0]}" if codes else f"investment_{error.code}"
        ) from None
    # The ledger opens with what the finance check showed and was signed off against.
    if (opened.accrued_gross, opened.accrued_tax) != (
        expected.interest.gross,
        expected.interest.tax,
    ):
        raise NotCarried("investment_interest_differs")


async def _invite(
    session: AsyncSession, clock: Clock, client: ClientRow, client_id: str, link: str
) -> None:
    now = clock.now()
    variables = {"link": link, "hours": int(LINK_LIFETIME.total_seconds() // 3600)}
    assert client.phone is not None  # the switch refuses a client without one
    # The link expires with the message: an invitation is never sent after it stops working.
    await queue_message(
        session,
        channel="sms",
        recipient=client.phone,
        template="claim_invitation",
        variables=variables,
        idempotency_key=f"migration-invitation:{client_id}:sms",
        now=now,
        deliver_within=LINK_LIFETIME,
    )
    if client.email:
        await queue_message(
            session,
            channel="email",
            recipient=client.email,
            template="claim_invitation",
            variables=variables,
            idempotency_key=f"migration-invitation:{client_id}:email",
            now=now,
            deliver_within=LINK_LIFETIME,
        )


async def _note(
    database: Database,
    clock: Clock,
    fence: Fence,
    switch_id: str,
    legacy_client_ref: str,
    code: str,
) -> None:
    safe = code if code.replace("_", "").isalpha() and code.islower() else "refused"
    async with database.actor_transaction(SYSTEM) as session:
        await fence(session)
        await session.execute(
            _NOTE_PROBLEM,
            {
                "switch_id": switch_id,
                "ref": legacy_client_ref,
                "code": safe[:64],
                "now": clock.now(),
            },
        )
        await service._audit(
            session,
            SYSTEM,
            "change",
            "migration.client_not_carried_over",
            "migration_switch",
            switch_id,
            clock,
            details={"code": safe[:64]},
        )
    logger.warning("migration_client_not_carried_over", error_code=safe[:64])
