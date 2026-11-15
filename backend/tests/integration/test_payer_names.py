"""The name behind a number that paid a client's mobile money payment, looked up once it counts.

The owner accepts money from any payer and keeps the payer's number and name on record
(2026-11-15). Real PostgreSQL, the card worker and the fake Pesapal of the card tests, and a fake
ioTec Verify that answers as told.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.modules.payments import payer_names
from app.modules.payments.domain.deposits import DepositStatus as S
from app.modules.payments.payer_name_jobs import PayerNameJobs
from app.modules.payouts.domain.name_lookups import (
    Accepted,
    Found,
    LookupStatus,
    NoName,
    NotReceived,
    Unanswerable,
)
from app.shared.actors import SYSTEM
from tests.integration.test_card_deposits import Setup, as_deposit, until
from tests.integration.test_card_deposits import setup as card_setup  # noqa: F401 - a fixture
from tests.integration.test_hosted_mobile_money import pay_from, start
from tests.integration.test_payout_name_lookups import FakeNames
from tests.support.clock import FakeClock
from tests.support.settings import set_setting

pytestmark = pytest.mark.integration

OTHER = "+256701999999"


@pytest.fixture
async def setup(card_setup: Setup) -> Setup:  # noqa: F811 - the fixture, not the import
    """The card tests' app and worker, with Pesapal collecting mobile money."""
    await set_setting(
        card_setup.database, "mobile_money_collector", {"provider": "pesapal"}, card_setup.clock
    )
    return card_setup


def names_worker(database: Database, names: FakeNames, clock: FakeClock) -> Worker:
    return Worker(
        database,
        PayerNameJobs(database, names, clock).handlers(),
        WorkerConfig(
            lease=timedelta(seconds=30),
            retry=RetryPolicy(base=timedelta(seconds=5), cap=timedelta(seconds=5)),
        ),
        clock,
    )


async def run_all(runner: Worker, clock: FakeClock, rounds: int = 6) -> None:
    for _ in range(rounds):
        while await runner.run_once():
            pass
        clock.advance(seconds=10)


def sent_for(names: FakeNames, deposit_id: str) -> list[str]:
    """The numbers sent for this payment's lookup: its deposit is the lookup's reference."""
    return [number for number, reference in names.sent if reference == deposit_id]


async def lookup(setup: Setup, deposit_id: str) -> payer_names.PayerName | None:
    async with setup.system.actor_transaction(SYSTEM) as session:
        return await payer_names.payer_name(session, deposit_id)


async def paid_from(setup: Setup, phone: str | None) -> str:
    """A mobile money payment on Pesapal's page, paid from `phone` (None: Pesapal names none)."""
    deposit_id, _ = await start(setup)
    deposit = await as_deposit(setup, deposit_id)
    if phone is None:
        setup.fake.pay(deposit_id)
        setup.fake.orders[setup.fake.tracking_id(deposit_id)]["payment_method"] = "MTN"
    else:
        pay_from(setup, deposit_id, phone)
    confirmed = await until(setup, deposit, S.CONFIRMED, S.FAILED, S.UNCERTAIN)
    assert confirmed.status is S.CONFIRMED
    return deposit_id


async def test_another_numbers_payment_gets_the_name_the_network_holds(setup: Setup) -> None:
    deposit_id = await paid_from(setup, OTHER)
    asked = await lookup(setup, deposit_id)
    assert asked is not None and (asked.phone, asked.status) == (OTHER, LookupStatus.QUEUED)

    names = FakeNames(answers=[Found("  Grace Nambi  ")])
    await run_all(names_worker(setup.system, names, setup.clock), setup.clock)
    # Other tests may leave lookups queued in the shared database: count only this payment's.
    assert sent_for(names, deposit_id) == [OTHER.removeprefix("+")]
    found = await lookup(setup, deposit_id)
    assert found is not None
    assert (found.status, found.registered_name) == (LookupStatus.FOUND, "Grace Nambi")
    assert found.answered_at is not None

    # The answer is final: running again asks nothing more.
    await run_all(names_worker(setup.system, names, setup.clock), setup.clock)
    assert len(sent_for(names, deposit_id)) == 1


async def test_the_clients_own_number_is_not_looked_up(setup: Setup) -> None:
    deposit_id, _ = await start(setup)
    deposit = await as_deposit(setup, deposit_id)
    assert deposit.payer_msisdn is not None
    pay_from(setup, deposit_id, deposit.payer_msisdn)
    assert (await until(setup, deposit, S.CONFIRMED, S.FAILED)).status is S.CONFIRMED
    assert await lookup(setup, deposit_id) is None


async def test_nothing_is_looked_up_when_pesapal_names_no_number(setup: Setup) -> None:
    deposit_id = await paid_from(setup, None)
    assert await lookup(setup, deposit_id) is None


class RefusesOne(FakeNames):
    """Refuses the lookup for one payment only, whatever else is queued."""

    def __init__(self, reference: str) -> None:
        super().__init__()
        self.reference = reference

    async def send(self, msisdn: str, reference: str) -> Accepted | NotReceived | Unanswerable:
        if reference == self.reference:
            self.sent.append((msisdn, reference))
            return Unanswerable("refused")
        return await super().send(msisdn, reference)


@pytest.mark.parametrize("status", [LookupStatus.NO_NAME, LookupStatus.UNANSWERED])
async def test_no_name_or_no_answer_is_kept_and_the_payment_stands(
    setup: Setup, status: LookupStatus
) -> None:
    deposit_id = await paid_from(setup, OTHER)
    names = (
        FakeNames(answers=[NoName()]) if status is LookupStatus.NO_NAME else RefusesOne(deposit_id)
    )
    await run_all(names_worker(setup.system, names, setup.clock), setup.clock)
    found = await lookup(setup, deposit_id)
    assert found is not None and (found.status, found.registered_name) == (status, None)
    assert (await as_deposit(setup, deposit_id)).status is S.CONFIRMED


async def test_only_the_worker_records_a_lookup_and_an_answer_is_final(
    setup: Setup, worker_database: Database
) -> None:
    deposit_id = await paid_from(setup, OTHER)
    await run_all(names_worker(setup.system, FakeNames(), setup.clock), setup.clock)
    # Staff may read it; nobody but the worker may change it, and never once answered.
    from tests.support.ledger import staff as new_staff

    async with setup.database.actor_transaction(new_staff()) as session:
        seen = await payer_names.payer_name(session, deposit_id)
    assert seen is not None and seen.status is LookupStatus.FOUND
    for database, statement in (
        (setup.database, "UPDATE payment_payer_names SET registered_name = 'X' "),
        (worker_database, "UPDATE payment_payer_names SET status = 'sent' "),
        (worker_database, "DELETE FROM payment_payer_names "),
    ):
        with pytest.raises(DBAPIError, match=r"payer name lookup|permission denied|never"):
            async with database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(statement + "WHERE deposit_id = :id"), {"id": deposit_id}
                )
    # A client never sees who paid.
    async with setup.http() as http:
        client = await setup.client(http)
    async with setup.database.actor_transaction(client) as session:
        assert await payer_names.payer_name(session, deposit_id) is None
