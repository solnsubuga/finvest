"""The name the network holds for a client's phone number, looked up as they hand in their ID.

Runs the real service, the real worker and the committed migrations against PostgreSQL, with a
fake ioTec that answers as told.
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.modules.kyc import api as kyc_api
from app.modules.kyc import phone_names
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus
from app.modules.kyc.domain.phone_names import MAX_LOOKUPS, PhoneName
from app.modules.kyc.phone_name_jobs import PhoneNameJobs
from app.modules.kyc.providers import ManualProvider
from app.modules.payouts.domain.name_lookups import (
    Found,
    NoName,
    NotReceived,
    Unanswerable,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.integration.test_payout_name_lookups import FakeNames
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.kyc import SEALER, FakeProvider, all_passed, live_selfie, ready_draft
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        yield running


def worker(database: Database, names: FakeNames, clock: FakeClock) -> Worker:
    return Worker(
        database,
        PhoneNameJobs(database, names, clock).handlers(),
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


async def handed_in(
    api: Api,
    *,
    on: bool = True,
    automatic: bool = False,
    live: Database | None = None,
) -> tuple[Actor, str]:
    """A client with a real account and phone number hands in a national ID."""
    client = await api.client()
    async with api.database.actor_transaction(client) as session:
        await ready_draft(session, client, api.clock)
    if live is not None:
        await live_selfie(api.database, live, client, api.clock)
    provider = FakeProvider() if automatic else ManualProvider()
    async with api.database.actor_transaction(client) as session:
        view = await kyc.submit(session, SEALER, client, provider, api.clock, phone_names=on)
    return client, view.id


async def shown(api: Api, check_id: str) -> kyc_api.PhoneNameOut | None:
    async with api.database.actor_transaction(new_staff()) as session:
        view = await kyc.check_for_staff(session, SEALER, check_id)
        return (await kyc_api._staff_check_now(session, view)).phone_name


async def phone_of(api: Api, client: Actor) -> str:
    async with api.database.transaction() as session:
        phone = await session.scalar(
            text("SELECT phone FROM identity_clients WHERE id = :id"), {"id": client.id}
        )
    assert isinstance(phone, str)
    return phone


async def test_without_a_provider_nothing_is_looked_up(api: Api) -> None:
    _, check_id = await handed_in(api, on=False)
    assert await shown(api, check_id) is None


@pytest.mark.parametrize(
    ("network_name", "status"),
    [
        ("Amara Nakato", PhoneName.MATCHES),
        ("NAKATO AMARA GRACE", PhoneName.PARTLY_MATCHES),
        ("Brian Okello", PhoneName.DIFFERENT),
    ],
)
async def test_the_network_name_is_compared_with_the_name_on_the_id(
    api: Api, worker_database: Database, network_name: str, status: PhoneName
) -> None:
    client, check_id = await handed_in(api)
    waiting = await shown(api, check_id)
    assert waiting is not None and waiting.status is PhoneName.CHECKING
    names = FakeNames(answers=[Found(network_name)])
    await run_all(worker(worker_database, names, api.clock), api.clock)

    # The client's own number, as the lookup takes it.
    assert [number for number, _ in names.sent] == [(await phone_of(api, client))[1:]]
    found = await shown(api, check_id)
    assert found is not None
    assert (found.status, found.registered_name) == (status, network_name)
    assert found.answered_at is not None
    # A hint only: the check still waits for a person, whatever the network said.
    async with api.database.actor_transaction(new_staff()) as session:
        view = await kyc.check_for_staff(session, SEALER, check_id)
    assert view.status is CheckStatus.NEEDS_REVIEW


async def test_no_name_and_no_answer(api: Api, worker_database: Database) -> None:
    _, barred = await handed_in(api)
    await run_all(worker(worker_database, FakeNames(answers=[NoName()]), api.clock), api.clock)
    answer = await shown(api, barred)
    assert answer is not None
    assert (answer.status, answer.registered_name) == (PhoneName.NO_NAME, None)

    # A send that may have arrived is never sent again: the lookup is over without an answer.
    _, unsure = await handed_in(api)
    names = FakeNames(sends=[Unanswerable("iotec_uncertain")])
    await run_all(worker(worker_database, names, api.clock), api.clock)
    assert len(names.sent) == 1
    answer = await shown(api, unsure)
    assert answer is not None and answer.status is PhoneName.NOT_ANSWERED


async def test_a_send_that_never_arrived_is_tried_again(
    api: Api, worker_database: Database
) -> None:
    _, check_id = await handed_in(api)
    names = FakeNames(sends=[NotReceived()], answers=[Found("Amara Nakato")])
    await run_all(worker(worker_database, names, api.clock), api.clock)
    assert len(names.sent) == 2
    answer = await shown(api, check_id)
    assert answer is not None and answer.status is PhoneName.MATCHES


async def test_an_unanswered_lookup_is_asked_again_at_most_three_times(
    api: Api, worker_database: Database
) -> None:
    client, check_id = await handed_in(api)
    for _ in range(MAX_LOOKUPS):
        names = FakeNames(sends=[Unanswerable("iotec_refused_400")])
        await run_all(worker(worker_database, names, api.clock), api.clock)
        # Asked again with the next hand-in, in the client's transaction.
        async with api.database.actor_transaction(client) as session:
            await phone_names.ask_at_hand_in(session, check_id, client.id or "", api.clock)
    async with api.database.actor_transaction(client) as session:
        latest = await phone_names.latest_lookup(session, check_id)
    assert latest is not None and latest.number == MAX_LOOKUPS

    # Once the network has answered, nothing is asked again.
    second, answered = await handed_in(api)
    await run_all(worker(worker_database, FakeNames(), api.clock), api.clock)
    async with api.database.actor_transaction(second) as session:
        assert (
            await phone_names.ask_at_hand_in(session, answered, second.id or "", api.clock) is None
        )


async def test_automatic_approval_needs_the_network_name_to_match(
    api: Api, worker_database: Database
) -> None:
    async def decided(network_name: str) -> kyc.CheckView:
        _, check_id = await handed_in(api, automatic=True, live=worker_database)
        names = FakeNames(answers=[Found(network_name)])
        await run_all(worker(worker_database, names, api.clock), api.clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            return await kyc.record_provider_result(
                session,
                SEALER,
                check_id,
                1,
                "fake-reference",
                all_passed(),
                api.clock,
                automatic_approval=True,
            )

    different = await decided("Brian Okello")
    assert different.status is CheckStatus.NEEDS_REVIEW
    assert different.review_flags == ("phone_name_not_confirmed",)
    assert (await decided("Amara Nakato")).status is CheckStatus.APPROVED


async def test_only_the_worker_answers_and_an_answer_is_final(
    api: Api, worker_database: Database
) -> None:
    client, check_id = await handed_in(api)
    other, _ = await handed_in(api)
    # A client cannot write an answer, or ask for a lookup of someone else's check.
    for statement, values in [
        (
            "INSERT INTO kyc_phone_names (id, check_id, number, status, registered_name, "
            "name_match, request_id, answered_at, created_at, updated_at) VALUES (:id, :check, "
            "2, 'found', 'Amara Nakato', 'exact', gen_random_uuid(), now(), now(), now())",
            {"id": push_id(), "check": check_id},
        ),
        (
            "INSERT INTO kyc_phone_names (id, check_id, number, status, created_at, updated_at) "
            "VALUES (:id, :check, 2, 'queued', now(), now())",
            {"id": push_id(), "check": check_id},
        ),
        (
            "UPDATE kyc_phone_names SET status = 'unanswered' WHERE check_id = :check",
            {"check": check_id},
        ),
    ]:
        acting = other if "'queued'" in statement else client
        with pytest.raises(DBAPIError):
            async with api.database.actor_transaction(acting) as session:
                await session.execute(text(statement), values)

    await run_all(worker(worker_database, FakeNames(), api.clock), api.clock)
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE kyc_phone_names SET status = 'unanswered', registered_name = NULL, "
                    "name_match = NULL WHERE check_id = :check"
                ),
                {"check": check_id},
            )
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("DELETE FROM kyc_phone_names WHERE check_id = :check"), {"check": check_id}
            )
