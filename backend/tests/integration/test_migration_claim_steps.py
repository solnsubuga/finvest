"""A migrated client hands in their identity step, hears the decision and confirms, over HTTP."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, enqueue
from app.modules.kyc import service as kyc
from app.modules.migration import claims
from app.modules.migration.jobs import IDENTITY_CHECK_DECIDED, MigrationJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.integration.test_claims import HASHER, PASSWORD, Api, imported, texted
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.jobs import run_due
from tests.support.kyc import SEALER, ready_draft

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(
    test_database: TestDatabase,
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
) -> AsyncIterator[Api]:
    # With the identity data key, as deployed, so a full ID check can be handed in.
    clock = FakeClock()
    app = create_app(
        test_database.settings("finvest_app"), app_database, clock, HASHER, identity_sealer=SEALER
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(
            http,
            app.state.identity,
            app.state.codes,
            clock,
            app_database,
            worker_database,
            migrator_database,
        )


async def signed_in(api: Api) -> tuple[Actor, dict[str, str]]:
    """A migrated client who claimed the account from the link. Returns them and a bearer."""
    phone, client_id = await imported(api)
    client = Actor("client", client_id)
    async with api.worker.actor_transaction(SYSTEM) as session:
        await claims.create_claim(
            session,
            SYSTEM,
            api.clock,
            client_id=client_id,
            legacy_client_ref=f"CL-{client_id[-8:]}",
            needs_id_check=True,
        )
        token = await claims.send_link(session, SYSTEM, api.clock, client_id)
    started = await api.http.post("/client/claim/start", json={"token": token})
    [code] = await texted(api, phone)
    body = {
        "token": token,
        "challenge_id": started.json()["challenge_id"],
        "code": code,
        "password": PASSWORD,
        "accepted_terms": True,
    }
    done = await api.http.post("/client/claim/complete", json=body)
    assert done.status_code == 200, done.text
    return client, {"Authorization": f"Bearer {done.json()['token']}"}


async def decided(api: Api, client: Actor, *, approved: bool) -> None:
    """The identity checks module's decision on the client's full ID check, as it publishes it."""
    assert client.id is not None
    async with api.worker.transaction() as session:
        await enqueue(
            session,
            JobRequest(
                kind=IDENTITY_CHECK_DECIDED,
                idempotency_key=push_id(),
                payload={"data": {"client_id": client.id, "approved": approved}},
            ),
            now=api.clock.now(),
        )
    await drain(api)


async def drain(api: Api) -> None:
    """Run the migration worker until nothing is left. Other tests' decisions may be queued too,
    since the integration database is shared, so the count is not this test's to assert."""
    worker = Worker(
        api.worker, MigrationJobs(api.worker, SEALER, api.clock, None).handlers(), clock=api.clock
    )
    await run_due(worker)


async def state(api: Api, bearer: dict[str, str]) -> Any:
    mine = await api.http.get("/client/claim", headers=bearer)
    assert mine.status_code == 200, mine.text
    return mine.json()["status"]


async def test_a_client_hands_the_full_check_in_with_the_claim_and_then_confirms(
    api: Api,
) -> None:
    client, bearer = await signed_in(api)
    # Nothing to confirm before who they are is confirmed.
    assert (await api.http.post("/client/claim/confirm", headers=bearer)).status_code == 409
    mine = await api.http.get("/client/claim", headers=bearer)
    assert mine.json()["identity_step"] == "full_id_check"

    async with api.database.actor_transaction(client) as session:
        await ready_draft(session, client, api.clock)
    handed = await api.http.post("/client/claim/identity", headers=bearer)
    assert handed.status_code == 200, handed.text
    assert handed.json()["status"] == "identity_submitted"
    async with api.database.actor_transaction(client) as session:
        check = await kyc.check_for_client(session, SEALER, client)
    assert check is not None and check.submitted_at is not None
    # Handed in once: the check waits for a person.
    assert (await api.http.post("/client/claim/identity", headers=bearer)).status_code == 409

    await decided(api, client, approved=True)
    assert await state(api, bearer) == "identity_confirmed"
    confirmed = await api.http.post("/client/claim/confirm", headers=bearer)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert (await api.http.post("/client/claim/confirm", headers=bearer)).status_code == 409


async def test_a_check_that_is_not_approved_sends_the_client_back_a_step(api: Api) -> None:
    client, bearer = await signed_in(api)
    async with api.database.actor_transaction(client) as session:
        await ready_draft(session, client, api.clock)
    assert (await api.http.post("/client/claim/identity", headers=bearer)).status_code == 200
    await decided(api, client, approved=False)
    assert await state(api, bearer) == "signed_in"


async def test_a_decision_for_anyone_else_changes_nothing(api: Api) -> None:
    # A check decided for a client who signed up in Finvest 2.0, and one for a migrated client
    # who has not handed in their identity step yet.
    client, bearer = await signed_in(api)
    keys = [push_id(), push_id()]
    async with api.worker.transaction() as session:
        for key, data in [
            (keys[0], {"client_id": push_id(), "approved": True}),
            (keys[1], {"client_id": client.id, "approved": True}),
        ]:
            kind = IDENTITY_CHECK_DECIDED
            await enqueue(
                session,
                JobRequest(kind=kind, idempotency_key=key, payload={"data": data}),
                now=api.clock.now(),
            )
    await drain(api)
    async with api.owner.transaction() as session:
        done = await session.execute(
            text("SELECT status FROM jobs WHERE idempotency_key = ANY(:keys)"), {"keys": keys}
        )
        assert sorted(done.scalars()) == ["succeeded", "succeeded"]
    assert await state(api, bearer) == "signed_in"
