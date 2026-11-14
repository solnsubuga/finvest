"""Cloudflare Access follows the team.

An approved invite or reactivation puts the address on the list, an approved deactivation takes
it off, and every attempt asks the list before it changes it.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.identity import staff_access
from app.modules.identity.domain.staff_access import StaffAccessList
from app.modules.identity.staff_access_jobs import StaffAccessJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.jobs import run_due
from tests.support.staff_access import FakeAccessList

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


def an_email() -> str:
    return f"access.{push_id().lower().replace('_', 'a').replace('-', 'b')}@finvestug.com"


def worker(database: Database, api: Api, access: StaffAccessList | None) -> Worker:
    return Worker(
        database, StaffAccessJobs(database, access, api.clock).handlers(), WorkerConfig(), api.clock
    )


async def approved(api: Api, approver: Actor, proposer: Actor, **body: object) -> dict[str, object]:
    await api.as_staff(proposer)
    proposed = await api.http.post("/staff/team/changes", json={"reason": "Needed", **body})
    assert proposed.status_code == 201, proposed.text
    await api.as_staff(approver)
    decided = await api.http.post(
        f"/staff/team/changes/{proposed.json()['id']}/decision",
        json={"decision": "approved", "reason": "Checked"},
    )
    assert decided.status_code == 200, decided.text
    change: dict[str, object] = decided.json()
    return change


async def access_of(api: Api, staff_id: str) -> dict[str, object] | None:
    team = (await api.http.get("/staff/team")).json()
    found = next(m for m in team["members"] if m["id"] == staff_id)
    access: dict[str, object] | None = found["access"]
    return access


async def admins(api: Api) -> tuple[Actor, Actor]:
    first = await api.staff("admin", "First Admin")
    second = await api.staff("admin", "Second Admin")
    return first, second


async def invited(api: Api, first: Actor, second: Actor, email: str) -> str:
    change = await approved(
        api, second, first, kind="invite", email=email, name="Ada", role="support"
    )
    return str(change["staff_id"])


async def test_an_approved_invite_puts_the_address_on_the_list(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    email = an_email()
    staff_id = await invited(api, first, second, email)
    waiting = await access_of(api, staff_id)
    assert waiting is not None and waiting["state"] == "waiting"

    access = FakeAccessList()
    await run_due(worker(worker_database, api, access))
    assert email in access.emails
    assert ("add", email) in access.writes
    synced = await access_of(api, staff_id)
    assert synced is not None and synced["state"] == "in_access" and synced["synced_at"]


async def test_deactivating_takes_the_address_off_and_reactivating_puts_it_back(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    email = an_email()
    staff_id = await invited(api, first, second, email)
    access = FakeAccessList()
    run = worker(worker_database, api, access)
    await run_due(run)

    await approved(api, second, first, kind="deactivate", staff_id=staff_id)
    await run_due(run)
    assert email not in access.emails
    assert (await access_of(api, staff_id) or {})["state"] == "removed"

    await approved(api, second, first, kind="reactivate", staff_id=staff_id)
    await run_due(run)
    assert email in access.emails
    assert (await access_of(api, staff_id) or {})["state"] == "in_access"


async def test_a_role_change_does_not_touch_the_list(api: Api, worker_database: Database) -> None:
    first, second = await admins(api)
    email = an_email()
    staff_id = await invited(api, first, second, email)
    access = FakeAccessList()
    run = worker(worker_database, api, access)
    await run_due(run)
    writes = list(access.writes)
    await approved(api, second, first, kind="change_role", staff_id=staff_id, role="finance")
    await run_due(run)
    assert access.writes == writes


async def test_an_address_already_where_it_should_be_is_not_written_again(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    email = an_email()
    access = FakeAccessList(emails={email})
    staff_id = await invited(api, first, second, email)
    await run_due(worker(worker_database, api, access))
    assert all(address != email for _, address in access.writes)
    assert (await access_of(api, staff_id) or {})["state"] == "in_access"


async def test_a_duplicate_refusal_counts_as_done_when_the_list_is_right(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    email = an_email()
    access = FakeAccessList(mode="duplicate_refused")
    staff_id = await invited(api, first, second, email)
    await run_due(worker(worker_database, api, access))
    assert (await access_of(api, staff_id) or {})["state"] == "in_access"


async def test_without_cloudflare_settings_the_team_view_says_not_set_up(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    staff_id = await invited(api, first, second, an_email())
    await run_due(worker(worker_database, api, None))
    assert (await access_of(api, staff_id) or {})["state"] == "not_set_up"


async def test_cloudflare_unavailable_is_tried_again_after_asking_first(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    email = an_email()
    staff_id = await invited(api, first, second, email)
    access = FakeAccessList(mode="unavailable")
    run = worker(worker_database, api, access)
    await run.run_once()
    trying = await access_of(api, staff_id)
    assert trying is not None
    assert trying["state"] == "waiting" and trying["error_code"] == "cloudflare_unavailable"

    access.mode = "ok"
    api.clock.advance(minutes=20)
    await run_due(run)
    assert email in access.emails
    settled = await access_of(api, staff_id)
    assert settled is not None and settled["state"] == "in_access" and settled["error_code"] is None


async def test_an_accepted_write_that_does_not_show_is_not_recorded_as_done(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    staff_id = await invited(api, first, second, an_email())
    await worker(worker_database, api, FakeAccessList(mode="lagging")).run_once()
    state = await access_of(api, staff_id)
    assert state is not None
    assert state["state"] == "waiting" and state["error_code"] == "access_not_confirmed"


async def test_a_refused_token_fails_and_an_admin_can_ask_again(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    email = an_email()
    staff_id = await invited(api, first, second, email)
    access = FakeAccessList(mode="token_refused")
    run = worker(worker_database, api, access)
    await run_due(run)
    failed = await access_of(api, staff_id)
    assert failed is not None
    assert failed["state"] == "failed" and failed["error_code"] == "cloudflare_token_refused"

    access.mode = "ok"
    again = await api.http.post(f"/staff/team/members/{staff_id}/access-sync")
    assert again.status_code == 202, again.text
    assert again.json()["state"] == "waiting" and again.json()["error_code"] is None
    await run_due(run)
    assert email in access.emails
    assert (await access_of(api, staff_id) or {})["state"] == "in_access"


async def test_only_admins_ask_again(api: Api) -> None:
    first, second = await admins(api)
    staff_id = await invited(api, first, second, an_email())
    await api.staff("support")
    refused = await api.http.post(f"/staff/team/members/{staff_id}/access-sync")
    assert refused.status_code == 403
    await api.as_staff(first)
    missing = await api.http.post(f"/staff/team/members/{push_id()}/access-sync")
    assert missing.status_code == 404


class PausingList(FakeAccessList):
    """Holds its first add until the test lets it go, so a change can land in between."""

    def __init__(self) -> None:
        super().__init__()
        self.adding = asyncio.Event()
        self.go = asyncio.Event()

    async def add(self, email: str) -> None:
        if not self.adding.is_set():
            self.adding.set()
            await self.go.wait()
        await super().add(email)


async def test_a_deactivation_approved_during_an_add_still_ends_off_the_list(
    api: Api, worker_database: Database
) -> None:
    first, second = await admins(api)
    email = an_email()
    staff_id = await invited(api, first, second, email)
    access = PausingList()
    run = worker(worker_database, api, access)

    adding = asyncio.create_task(run.run_once())
    await asyncio.wait_for(access.adding.wait(), timeout=10)
    # The deactivation commits in its own transaction while the invite's add is in flight.
    await approved(api, second, first, kind="deactivate", staff_id=staff_id)
    access.go.set()
    await adding
    # The invite's job saw the team change after its add, so it did not record "in access".
    assert (await access_of(api, staff_id) or {})["state"] == "waiting"

    api.clock.advance(minutes=20)
    await run_due(run)
    assert email not in access.emails
    assert (await access_of(api, staff_id) or {})["state"] == "removed"


async def test_the_daily_check_reaches_staff_added_before_syncing_existed(
    api: Api, worker_database: Database
) -> None:
    # Added from the command line, like the first admin: no change, so nothing was asked.
    staff = await api.staff("admin", "Founding Admin")
    assert await access_of(api, staff.id or "") is None
    async with worker_database.actor_transaction(SYSTEM) as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, queue, kind, idempotency_key, payload, max_attempts, "
                "run_at, expires_at, created_at, updated_at) VALUES (:id, 'default', :kind, "
                ":key, '{}'::jsonb, 3, :now, :expires, :now, :now)"
            ),
            {
                "id": push_id(),
                "kind": staff_access.CHECK_KIND,
                "key": push_id(),
                "now": api.clock.now(),
                "expires": api.clock.now() + timedelta(hours=1),
            },
        )
    access = FakeAccessList()
    await run_due(worker(worker_database, api, access))
    found = await access_of(api, staff.id or "")
    assert found is not None and found["state"] == "in_access"


async def test_the_api_may_only_mark_waiting(api: Api, app_database: Database) -> None:
    first, second = await admins(api)
    staff_id = await invited(api, first, second, an_email())
    with pytest.raises(Exception, match="row-level security"):
        async with app_database.actor_transaction(first) as session:
            await session.execute(
                text("UPDATE identity_staff_access SET state = 'in_access' WHERE staff_id = :id"),
                {"id": staff_id},
            )


async def test_the_status_reaches_the_console(api: Api) -> None:
    await api.staff("admin")
    response = await api.http.get("/staff/team")
    assert response.status_code == 200
    assert all("access" in member for member in response.json()["members"])
