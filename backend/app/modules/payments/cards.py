"""Card payments, and mobile money on the same hosted page: starting one, and settling it from
the provider's own record.

A mobile money payment that Pesapal collects (Settings, `mobile_money_collector`) is an order
on the same page, with no charge, and the number the client gave as its payer. Any number may
pay on the page; the one the provider names as having paid is kept on the order.

Nothing here calls the card provider. `request_card_deposit` saves the deposit, its order and a
job to open the order, in the client's transaction. The job handlers in `card_jobs.py` call the
provider with no transaction open and come back here to record what it said. A card deposit is
confirmed only by `settle_card_deposit`, from a status read, and the ledger entry is posted in
the same transaction: the card provider's balance receives the whole payment, the client's
investment the amount, and Finvest's card charges income any charge the client paid on top.

Every function takes the caller's open session and commits nothing itself.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.payments import service
from app.modules.payments.domain.cards import (
    CARD_POLL_DELAYS,
    CARD_WINDOW,
    CardDetails,
    CardQuote,
    CardTerms,
    after_card_status,
    check_limits,
    quote,
)
from app.modules.payments.domain.deposits import (
    DepositStatus,
    InvalidDeposit,
    Method,
    Move,
    Purpose,
)
from app.modules.payments.domain.outcomes import StatusReading
from app.modules.platform_settings import service as platform_settings
from app.modules.platform_settings.domain.catalogue import PAY_IN_BY_NETWORK, Channel
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id, validate_push_id

SEND_KIND = "payments.open_card_order"
VERIFY_KIND = "payments.verify_card_deposit"
CANCEL_KIND = "payments.cancel_card_order"
SWEEP_KIND = "payments.sweep_card_deposits"
REQUEST_OPERATION = "payments.request_card_deposit"
HOSTED_MOBILE_MONEY_OPERATION = service.REQUEST_OPERATION
CHANNEL: Channel = "card_pay_in"

# The payer has a browser open and is waiting, so opening the order is tried for minutes.
SEND_ATTEMPTS = 5
SEND_MAX_AGE = timedelta(minutes=8)
LIMIT_PERIOD = timedelta(days=30)
logger = structlog.get_logger()

_INSERT_ORDER = text(
    "INSERT INTO payment_card_orders (deposit_id, client_id, currency, charge_minor, "
    "total_minor, created_at) VALUES (:deposit_id, :client_id, :currency, :charge_minor, "
    ":total_minor, :now)"
)
_SELECT_ORDER = text(
    "SELECT deposit_id, charge_minor, total_minor, currency, page_url, card_brand, "
    "card_last_four, confirmation_code, paid_from_msisdn FROM payment_card_orders "
    "WHERE deposit_id = :id"
)
_SAVE_PAGE = text(
    "UPDATE payment_card_orders SET page_url = :page_url "
    "WHERE deposit_id = :id AND page_url IS NULL"
)
_SAVE_CARD = text(
    "UPDATE payment_card_orders SET card_brand = coalesce(card_brand, :brand), "
    "card_last_four = coalesce(card_last_four, :last_four), "
    "confirmation_code = coalesce(confirmation_code, :confirmation_code), "
    "paid_from_msisdn = coalesce(paid_from_msisdn, :paid_from) WHERE deposit_id = :id"
)
# Every card payment of the client's in the period that has not failed, at what the card paid.
_PAID_BY_CARD = text(
    "SELECT coalesce(sum(o.total_minor), 0) FROM payment_card_orders AS o "
    "JOIN payment_deposits AS d ON d.id = o.deposit_id "
    "WHERE o.client_id = :client_id AND d.method = 'card' AND d.status <> 'failed' "
    "AND d.reversal_id IS NULL "
    "AND d.created_at >= :since"
)


@dataclass(frozen=True, slots=True)
class CardOrder:
    deposit_id: str
    charge: Money
    total: Money
    # The provider's page, while the payer may still pay on it.
    page_url: str | None
    card: CardDetails | None
    # For mobile money, the number the provider says paid, once confirmed. It may be anyone's.
    paid_from: str | None = None


def provider_balance(provider: str, currency: Currency = Currency.UGX) -> AccountSpec:
    """The card provider's balance: card money it holds for Finvest until it settles to the
    trust account. A pot of its own at reconciliation."""
    names = {"pesapal": "Pesapal balance"}
    return AccountSpec(
        f"{provider}.balance",
        AccountKind.ASSET,
        currency,
        names.get(provider, f"{provider} balance"),
    )


async def card_terms(session: AsyncSession, now: datetime) -> CardTerms:
    setting = await platform_settings.card_payments(session, now)
    return CardTerms(
        charge_basis_points=setting.charge_basis_points,
        max_payment=Money(setting.max_payment_minor, Currency.UGX),
        max_30_days=Money(setting.max_30_days_minor, Currency.UGX),
    )


async def paid_by_card_since(session: AsyncSession, client_id: str, since: datetime) -> Money:
    total = await session.scalar(_PAID_BY_CARD, {"client_id": client_id, "since": since})
    return Money(int(total or 0), Currency.UGX)


@dataclass(frozen=True, slots=True)
class CardOffer:
    """What paying for an investment by card would cost the client, and whether they may."""

    quote: CardQuote
    terms: CardTerms
    # None when the client may pay this by card now, otherwise why not, as a safe code.
    refused: str | None


async def card_offer(
    session: AsyncSession, client_id: str, amount: Money, clock: Clock
) -> CardOffer:
    """The charge and total for paying `amount` by card, for the client to see before paying.

    `refused` says why the client may not pay this by card now: the channel is paused, or the
    total is over a limit. It never raises for those, so the app can explain.
    """
    now = clock.now()
    terms = await card_terms(session, now)
    try:
        quoted = quote(amount, terms)
    except InvalidDeposit as error:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["amount"], code=error.code)]
        ) from None
    channels = await platform_settings.payment_channels(session, now)
    refused: str | None = None
    if not channels.is_open(CHANNEL):
        refused = "channel_paused"
    else:
        try:
            check_limits(
                quoted,
                terms,
                paid_in_30_days=await paid_by_card_since(session, client_id, now - LIMIT_PERIOD),
            )
        except InvalidDeposit as error:
            refused = error.code
    return CardOffer(quoted, terms, refused)


async def request_card_deposit(
    session: AsyncSession,
    actor: Actor,
    *,
    purpose: Purpose,
    purpose_id: str,
    amount: Money,
    idempotency_key: str,
    provider: str,
    clock: Clock,
) -> service.Deposit:
    """Save a client's card payment for one investment, its order and a job to open it.

    `session` is the client's own transaction. Nothing is sent here: the worker opens the order
    at the card provider and saves the page the client pays on. Calling again with the same key
    returns the same deposit. The charge is worked out now from Settings and saved with the
    order, so the client pays exactly what they were shown.

    Refused with 503 `channel_paused` while card payments are paused, and with 422 when the
    total is over the card limit for one payment or for the client's last 30 days.
    """
    if actor.kind != "client" or actor.id is None:
        raise ApplicationError("forbidden")
    try:
        validate_push_id(purpose_id)
    except ValueError:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["purpose_id"], code="invalid_id")],
        ) from None
    client_id = actor.id
    now = clock.now()

    async def command() -> Receipt:
        offer = await card_offer(session, client_id, amount, clock)
        if offer.refused == "channel_paused":
            raise ApplicationError(
                "unavailable",
                fields=[FieldProblem(field_path=["method"], code="channel_paused")],
            )
        if offer.refused is not None:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["amount"], code=offer.refused)],
            )
        deposit_id = push_id()
        await service.insert_deposit(
            session,
            {
                "id": deposit_id,
                "client_id": client_id,
                "purpose_type": purpose.value,
                "purpose_id": purpose_id,
                "method": Method.CARD.value,
                "provider": provider,
                "currency": amount.currency.value,
                "amount_minor": amount.minor,
                "payer_msisdn": None,
                "status": DepositStatus.CREATED.value,
                "now": now,
            },
        )
        await session.execute(
            _INSERT_ORDER,
            {
                "deposit_id": deposit_id,
                "client_id": client_id,
                "currency": amount.currency.value,
                "charge_minor": offer.quote.charge.minor,
                "total_minor": offer.quote.total.minor,
                "now": now,
            },
        )
        await service.record_event(
            session, deposit_id, None, DepositStatus.CREATED, None, "request", now
        )
        await service.record_requested(session, actor, deposit_id, Method.CARD.value, clock)
        await enqueue(
            session,
            JobRequest(
                kind=SEND_KIND,
                idempotency_key=deposit_id,
                payload={"deposit_id": deposit_id},
                max_attempts=SEND_ATTEMPTS,
                max_age=SEND_MAX_AGE,
            ),
            now=now,
        )
        return Receipt(201, {"deposit_id": deposit_id})

    outcome = await run_once(
        session,
        actor=actor,
        operation=REQUEST_OPERATION,
        key=idempotency_key,
        request={
            "purpose": purpose.value,
            "purpose_id": purpose_id,
            "currency": amount.currency.value,
            "amount_minor": amount.minor,
        },
        now=now,
        command=command,
    )
    deposit_id = str(outcome.receipt.response["deposit_id"])
    if not outcome.replayed:
        logger.info("card_deposit_requested", deposit_id=deposit_id)
    return await service.get_deposit(session, deposit_id)


async def request_hosted_mobile_money(
    session: AsyncSession,
    actor: Actor,
    request: service.DepositRequest,
    *,
    idempotency_key: str,
    provider: str,
    clock: Clock,
) -> service.Deposit:
    """Save a client's mobile money payment to be paid on the provider's hosted page.

    The same checks as `service.request_deposit`: the payer's number is the one the client gave
    (on the page any number may pay), the network's pay in must be open, and the investment
    takes one live deposit. The order has no charge: Finvest bears the provider's mobile money
    fee. The worker opens it and saves the page, as for a card. It shares the mobile money
    request's idempotency operation, so a key repeated after the collector changed on Settings
    returns the deposit it made.
    """
    if actor.kind != "client" or actor.id is None:
        raise ApplicationError("forbidden")
    msisdn, network = service.checked_mobile_money(request)
    client_id = actor.id
    now = clock.now()

    async def command() -> Receipt:
        channels = await platform_settings.payment_channels(session, now)
        if not channels.is_open(PAY_IN_BY_NETWORK[network]):
            raise ApplicationError(
                "unavailable", fields=[FieldProblem(field_path=["network"], code="channel_paused")]
            )
        deposit_id = push_id()
        await service.insert_deposit(
            session,
            {
                "id": deposit_id,
                "client_id": client_id,
                "purpose_type": request.purpose.value,
                "purpose_id": request.purpose_id,
                "method": network,
                "provider": provider,
                "currency": request.amount.currency.value,
                "amount_minor": request.amount.minor,
                "payer_msisdn": msisdn,
                "status": DepositStatus.CREATED.value,
                "now": now,
            },
        )
        await session.execute(
            _INSERT_ORDER,
            {
                "deposit_id": deposit_id,
                "client_id": client_id,
                "currency": request.amount.currency.value,
                "charge_minor": 0,
                "total_minor": request.amount.minor,
                "now": now,
            },
        )
        await service.record_event(
            session, deposit_id, None, DepositStatus.CREATED, None, "request", now
        )
        await service.record_requested(session, actor, deposit_id, network, clock)
        await enqueue(
            session,
            JobRequest(
                kind=SEND_KIND,
                idempotency_key=deposit_id,
                payload={"deposit_id": deposit_id},
                max_attempts=SEND_ATTEMPTS,
                max_age=SEND_MAX_AGE,
            ),
            now=now,
        )
        return Receipt(201, {"deposit_id": deposit_id})

    outcome = await run_once(
        session,
        actor=actor,
        operation=HOSTED_MOBILE_MONEY_OPERATION,
        key=idempotency_key,
        request=service.request_fingerprint(request, msisdn),
        now=now,
        command=command,
    )
    deposit_id = str(outcome.receipt.response["deposit_id"])
    if not outcome.replayed:
        logger.info("hosted_deposit_requested", deposit_id=deposit_id)
    return await service.get_deposit(session, deposit_id)


async def find_order(session: AsyncSession, deposit_id: str) -> CardOrder | None:
    """The card order of a deposit, if this transaction may see it."""
    row = (await session.execute(_SELECT_ORDER, {"id": deposit_id})).one_or_none()
    if row is None:
        return None
    currency = Currency(row.currency)
    card = None
    if row.card_brand or row.card_last_four or row.confirmation_code:
        card = CardDetails(row.card_brand, row.card_last_four, row.confirmation_code)
    return CardOrder(
        deposit_id=row.deposit_id,
        charge=Money(int(row.charge_minor), currency),
        total=Money(int(row.total_minor), currency),
        page_url=row.page_url,
        card=card,
        paid_from=row.paid_from_msisdn,
    )


async def save_page(session: AsyncSession, deposit_id: str, page_url: str) -> None:
    """Keep the page the payer pays on. Set once: a second page never replaces the first."""
    await session.execute(_SAVE_PAGE, {"id": deposit_id, "page_url": page_url})


async def schedule_card_read(
    session: AsyncSession, deposit_id: str, clock: Clock, *, attempt: int
) -> None:
    """Queue the next status read while the payer has the page. The sweep reads after these."""
    if attempt >= len(CARD_POLL_DELAYS):
        return
    await enqueue(
        session,
        JobRequest(
            kind=VERIFY_KIND,
            idempotency_key=f"{deposit_id}:card-poll:{attempt}",
            payload={"deposit_id": deposit_id, "attempt": attempt},
            delay=timedelta(seconds=CARD_POLL_DELAYS[attempt]),
            max_age=CARD_WINDOW,
        ),
        now=clock.now(),
    )


async def hurry_read(session: AsyncSession, deposit_id: str, clock: Clock) -> None:
    """Queue a status read now, because the payer came back from the provider's page. At most
    one a minute per deposit, however often the page is reloaded."""
    now = clock.now()
    await enqueue(
        session,
        JobRequest(
            kind=VERIFY_KIND,
            idempotency_key=f"{deposit_id}:card-return:{now.strftime('%Y%m%d%H%M')}",
            payload={"deposit_id": deposit_id},
            max_age=CARD_WINDOW,
        ),
        now=now,
    )


async def settle_card_deposit(
    session: AsyncSession,
    deposit_id: str,
    reading: StatusReading,
    details: CardDetails | None,
    clock: Clock,
    *,
    cause: service.Cause,
    paid_from: str | None = None,
) -> service.Deposit:
    """Move a card deposit on from a status read, in a system transaction.

    It locks the deposit, so two callers cannot both act. A confirmation for exactly the order's
    total posts the ledger entry and marks the deposit `confirmed` together, and keeps the card's
    brand and last four digits. Calling it again, or for a deposit that is final, changes nothing.
    """
    deposit = await service.find_deposit(session, deposit_id, lock=True)
    if deposit is None:
        raise ApplicationError("not_found")
    order = await find_order(session, deposit_id)
    if order is None:
        raise ApplicationError("not_found")
    move = after_card_status(deposit.status, reading, expected=order.total, credited=deposit.amount)
    if move is None:
        return deposit
    return await apply_card_move(
        session, deposit, order, move, details, clock, cause=cause, paid_from=paid_from
    )


async def apply_card_move(
    session: AsyncSession,
    deposit: service.Deposit,
    order: CardOrder,
    move: Move,
    details: CardDetails | None,
    clock: Clock,
    *,
    cause: service.Cause,
    paid_from: str | None = None,
) -> service.Deposit:
    card = deposit.method is Method.CARD
    if move.status is DepositStatus.CONFIRMED and (details is not None or not card):
        # Mobile money keeps only the provider's code, which a refund is asked for by: its
        # "brand" and "last four" would be a network and part of a phone number. It keeps the
        # number that paid instead, for screening and audit, which may not be the client's.
        await session.execute(
            _SAVE_CARD,
            {
                "id": deposit.id,
                "brand": details.brand if card and details else None,
                "last_four": details.last_four if card and details else None,
                "confirmation_code": details.confirmation_code if details else None,
                "paid_from": None if card else paid_from,
            },
        )
    return await service.apply_move(
        session,
        deposit,
        move,
        clock,
        cause=cause,
        paid_into=provider_balance(deposit.provider, deposit.amount.currency)
        if move.post is not None
        else None,
        recovered_charge=order.charge if move.post is not None else None,
    )
