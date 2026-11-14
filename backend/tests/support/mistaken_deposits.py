"""Investments paid for by a deposit the provider confirmed, for the mistaken credit tests."""

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.modules.payments import service
from app.modules.payments.domain.deposits import DepositStatus
from app.modules.payments.domain.outcomes import Accepted, Confirmed
from app.shared.actors import SYSTEM, Actor
from app.shared.clock import Clock
from app.shared.push_ids import push_id
from tests.support.investments import PRESENT, approved_client, request, today
from tests.support.payments import FakeCollections, deposit_worker, drain, phone, start_deposit, ugx

MINOR = 1_000_000


async def paid(
    app_database: Database, worker_database: Database, clock: Clock = PRESENT
) -> tuple[investments.Investment, service.Deposit]:
    """An investment waiting to start, with a mobile money deposit the provider confirmed."""
    client = await approved_client(app_database, clock)
    investment = await request(app_database, client, clock, amount=ugx(MINOR))
    deposit = await pay_by_phone(app_database, worker_database, client, investment, clock)
    return investment, deposit


async def pay_by_phone(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    investment: investments.Investment,
    clock: Clock,
) -> service.Deposit:
    provider, msisdn, ref = FakeCollections(), phone(), f"ref-{push_id()}"
    provider.on_send(msisdn, Accepted(ref))
    deposit = await start_deposit(
        app_database,
        clock,
        actor=client,
        msisdn=msisdn,
        amount=investment.amount.minor,
        purpose_id=investment.id,
    )
    await drain(deposit_worker(worker_database, provider, clock))
    async with worker_database.actor_transaction(SYSTEM) as session:
        confirmed = await service.settle_deposit(
            session, deposit.id, Confirmed(ref, investment.amount), clock, cause="status_read"
        )
    assert confirmed.status is DepositStatus.CONFIRMED
    return confirmed


async def start(database: Database, investment_id: str) -> investments.Investment:
    async with database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment_id, confirmed_on=today(), clock=PRESENT
        )
