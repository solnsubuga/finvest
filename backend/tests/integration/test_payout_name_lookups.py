"""A mobile money number proved by the name its network holds, read through ioTec Verify.

Runs the real routes, the real worker and the committed migrations against PostgreSQL, with a
fake ioTec that answers as told.
"""

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.infrastructure.storage import MemoryStorage
from app.modules.payouts import destinations, name_lookups
from app.modules.payouts.domain.destinations import (
    BankAccount,
    DestinationError,
    MobileMoneyNumber,
    ProofKind,
)
from app.modules.payouts.domain.name_lookups import (
    MAX_LOOKUPS,
    Accepted,
    Found,
    NoAnswer,
    NoName,
    NotReceived,
    Pending,
    Unanswerable,
)
from app.modules.payouts.name_lookup_jobs import NameLookupJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.kyc import VERIFIED_NAME, verify

pytestmark = pytest.mark.integration

Answer = Pending | Found | NoName | NoAnswer


class FakeNames:
    """ioTec Verify's phone lookup, answering from a script. Records every number it is sent."""

    name = "fake"

    def __init__(
        self,
        sends: list[Accepted | NotReceived | Unanswerable] | None = None,
        answers: list[Answer] | None = None,
    ) -> None:
        self._sends = list(sends or [])
        self._answers = list(answers or [])
        self.sent: list[tuple[str, str]] = []
        self.read_ids: list[str] = []

    async def send(self, msisdn: str, reference: str) -> Accepted | NotReceived | Unanswerable:
        self.sent.append((msisdn, reference))
        if self._sends:
            return self._sends.pop(0)
        return Accepted(str(uuid.uuid4()))

    async def read(self, request_id: str) -> Answer:
        self.read_ids.append(request_id)
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0] if self._answers else Found(VERIFIED_NAME)


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, storage=MemoryStorage()) as running:
        # Staff checks are stamped against the database clock, so these tests run at real time.
        running.clock.current = datetime.now(UTC)
        yield running


def worker(database: Database, names: FakeNames, clock: FakeClock) -> Worker:
    return Worker(
        database,
        NameLookupJobs(database, names, clock).handlers(),
        WorkerConfig(
            lease=timedelta(seconds=30),
            retry=RetryPolicy(base=timedelta(seconds=5), cap=timedelta(seconds=5)),
        ),
        clock,
    )


async def run_all(runner: Worker, clock: FakeClock, rounds: int = 6) -> None:
    """Run every due job, letting retries come due, as the real worker would."""
    for _ in range(rounds):
        while await runner.run_once():
            pass
        clock.advance(seconds=10)


def phone() -> str:
    return f"077{secrets.randbelow(10**7):07d}"


async def texted(worker_database: Database, recipient: str) -> str:
    async with worker_database.transaction() as session:
        code = await session.scalar(
            text(
                "SELECT variables->>'code' FROM notification_messages "
                "WHERE recipient = :recipient ORDER BY id DESC LIMIT 1"
            ),
            {"recipient": recipient},
        )
    assert isinstance(code, str)
    return code


async def confirmed_number(
    api: Api, worker_database: Database, *, verified: bool = True
) -> tuple[Actor, str, str]:
    """A client adds a number and enters the code texted to it, through the routes."""
    owner = await api.client()
    if verified:
        await verify(api.database, owner, api.clock)
    number = phone()
    added = await api.http.post(
        "/client/payout-destinations",
        json={"kind": "mobile_money", "holder_name": "Sarah Nakato", "phone": number},
    )
    assert added.status_code == 201, added.text
    destination_id = added.json()["id"]
    path = f"/client/payout-destinations/{destination_id}/code"
    assert (await api.http.post(path)).status_code == 204
    code = await texted(worker_database, "+256" + number[1:])
    entered = await api.http.post(f"{path}/verify", json={"code": code})
    assert entered.status_code == 200, entered.text
    body = entered.json()
    assert body["status"] == "awaiting_proof"
    assert body["name_check"] == "checking"
    return owner, destination_id, "256" + number[1:]


async def client_view(api: Api, destination_id: str) -> dict[str, object]:
    response = await api.http.get(f"/client/payout-destinations/{destination_id}")
    assert response.status_code == 200, response.text
    data: dict[str, object] = response.json()
    return data


async def lookups(worker_database: Database, destination_id: str) -> list[dict[str, object]]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT id, number, status, registered_name, error_code FROM payout_name_lookups "
                "WHERE destination_id = :id ORDER BY number"
            ),
            {"id": destination_id},
        )
        return [dict(row._mapping) for row in rows]


async def staff_view(api: Api, destination_id: str) -> destinations.DestinationView:
    async with api.database.actor_transaction(Actor("staff", push_id())) as session:
        return await destinations.get_destination(session, destination_id)


async def ask_again(api: Api, destination_id: str) -> tuple[int, dict[str, object]]:
    response = await api.http.post(f"/client/payout-destinations/{destination_id}/name-check")
    data: dict[str, object] = response.json()
    return response.status_code, data


def problem(body: dict[str, object]) -> str:
    error = body["error"]
    assert isinstance(error, dict)
    fields = error.get("fields") or []
    return str(fields[0]["code"]) if fields else str(error["code"])


# --- The client's number matches its ID ----------------------------------------------------


async def test_a_number_in_the_clients_own_name_goes_to_staff_with_the_networks_name(
    api: Api, worker_database: Database
) -> None:
    _, destination_id, msisdn = await confirmed_number(api, worker_database)
    names = FakeNames(answers=[Pending(), Found("AMARA NAKATO")])
    await run_all(worker(worker_database, names, api.clock), api.clock)

    # Sent once, for this number, with the lookup's own ID as the reference.
    [(sent_to, reference)] = names.sent
    [lookup] = await lookups(worker_database, destination_id)
    assert sent_to == msisdn and reference == lookup["id"]
    assert lookup["status"] == "found" and lookup["registered_name"] == "AMARA NAKATO"
    assert len(names.read_ids) == 2  # pending, then answered

    view = await staff_view(api, destination_id)
    assert view.status.value == "in_review"
    assert view.proof_kind is not None and view.proof_kind.value == "network_registered_name"
    assert view.proof_name == "AMARA NAKATO" and view.proof_document_id == lookup["id"]
    assert view.name_match is not None and view.name_match.value == "exact"
    assert view.checked_by is None  # still two people to come
    seen = await client_view(api, destination_id)
    assert seen["status"] == "in_review" and seen["name_check"] is None
    assert "AMARA" not in str(seen).upper()  # the client never sees the network's name


async def test_staff_check_a_number_with_the_networks_name_and_cannot_type_another(
    api: Api, worker_database: Database
) -> None:
    _, destination_id, _ = await confirmed_number(api, worker_database)
    await run_all(worker(worker_database, FakeNames(), api.clock), api.clock)
    checker = await api.staff("operations")
    opened = await api.http.get(f"/staff/payout-destinations/{destination_id}")
    assert opened.status_code == 200, opened.text
    assert opened.json()["network_name"] == VERIFIED_NAME

    typed = await api.http.post(
        f"/staff/payout-destinations/{destination_id}/check",
        json={"proof_name": VERIFIED_NAME, "reason": "Network name matches"},
    )
    assert typed.status_code == 422
    assert problem(typed.json()) == "proof_name_from_network"
    checked = await api.http.post(
        f"/staff/payout-destinations/{destination_id}/check",
        json={"reason": "Network name matches"},
    )
    assert checked.status_code == 200, checked.text
    check = checked.json()["check"]
    assert check["proof_name"] == VERIFIED_NAME and check["checked_by"] == checker.id

    # Even a direct write cannot change the name a check of a number records.
    async with api.database.actor_transaction(checker) as session:
        with pytest.raises(DBAPIError, match="name its network gave"):
            await session.execute(
                text(
                    "UPDATE payout_destinations SET proof_name = 'OKELLO Peter', "
                    "checked_at = now() WHERE id = :id"
                ),
                {"id": destination_id},
            )

    second = Actor("staff", push_id())
    async with api.database.actor_transaction(second) as session:
        approved = await destinations.approve(
            session, second, destination_id, reason="Second check", clock=api.clock
        )
    assert approved.status.value == "approved"


async def test_a_bank_check_still_needs_the_name_read_on_the_proof(
    api: Api, worker_database: Database
) -> None:
    owner = await api.client()
    await verify(api.database, owner, api.clock)
    async with api.database.actor_transaction(owner) as session:
        view = await destinations.add_destination(
            session,
            owner,
            BankAccount("stanbic", str(secrets.randbelow(10**12) + 10**12)),
            "Sarah Nakato",
            api.clock,
        )
        await destinations.confirm_code(session, owner, view.id, api.clock)
        await destinations.submit_proof(
            session, owner, view.id, ProofKind.BANK_LETTER, "doc-1", api.clock
        )
    checker = Actor("staff", push_id())
    async with api.database.actor_transaction(checker) as session:
        with pytest.raises(DestinationError) as missing:
            await destinations.check(
                session,
                checker,
                view.id,
                proof_name=None,
                statement_date=None,
                reason="Letter",
                clock=api.clock,
            )
    assert missing.value.code == "proof_name_required"


# --- Refused at once ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "reason", "kept_name"),
    [
        (Found("OKELLO Peter"), "name_different", "OKELLO Peter"),
        (Found("NAKATE Amara"), "name_different", "NAKATE Amara"),  # spelt differently
        (NoName(), "network_name_unavailable", None),
    ],
)
async def test_a_number_in_another_name_or_none_is_refused_for_good(
    api: Api, worker_database: Database, answer: Answer, reason: str, kept_name: str | None
) -> None:
    owner, destination_id, _ = await confirmed_number(api, worker_database)
    await run_all(worker(worker_database, FakeNames(answers=[answer]), api.clock), api.clock)

    view = await staff_view(api, destination_id)
    assert view.status.value == "rejected"
    assert view.rejection_reason is not None and view.rejection_reason.value == reason
    assert view.checked_by is None and view.proof_name == kept_name
    seen = await client_view(api, destination_id)
    assert seen["status"] == "rejected" and seen["rejection_reason"] == reason

    # Nobody can take it further: not staff, not the client.
    checker = Actor("staff", push_id())
    async with api.database.actor_transaction(checker) as session:
        with pytest.raises(DestinationError):
            await destinations.check(
                session,
                checker,
                destination_id,
                proof_name=None,
                statement_date=None,
                reason="Try",
                clock=api.clock,
            )
    async with api.database.actor_transaction(owner) as session:
        with pytest.raises(DestinationError) as final:
            await destinations.submit_proof(
                session,
                owner,
                destination_id,
                ProofKind.NETWORK_REGISTERED_NAME,
                "doc-1",
                api.clock,
            )
    assert final.value.code == "destination_rejected_final"
    status, body = await ask_again(api, destination_id)
    assert status == 409 and problem(body) == "destination_not_awaiting_proof"


# --- No answer ----------------------------------------------------------------------------


async def test_a_send_that_never_arrived_is_tried_again_and_one_that_may_have_is_not(
    api: Api, worker_database: Database
) -> None:
    _, destination_id, _ = await confirmed_number(api, worker_database)
    names = FakeNames(sends=[NotReceived(), NotReceived(), Unanswerable("iotec_uncertain")])
    await run_all(worker(worker_database, names, api.clock), api.clock)

    # Twice certainly not received, then perhaps received: never sent a fourth time.
    assert len(names.sent) == 3
    [lookup] = await lookups(worker_database, destination_id)
    assert lookup["status"] == "unanswered" and lookup["error_code"] == "iotec_uncertain"
    seen = await client_view(api, destination_id)
    assert seen["status"] == "awaiting_proof" and seen["name_check"] == "not_answered"

    # The client asks again: a new lookup, with a new reference.
    status, body = await ask_again(api, destination_id)
    assert status == 200, body
    assert body["name_check"] == "checking"
    status, body = await ask_again(api, destination_id)
    assert status == 409 and problem(body) == "name_check_in_progress"
    await run_all(worker(worker_database, names, api.clock), api.clock)
    first, second = await lookups(worker_database, destination_id)
    assert second["number"] == 2 and second["status"] == "found"
    assert names.sent[-1][1] == second["id"] != first["id"]
    assert (await staff_view(api, destination_id)).status.value == "in_review"


async def test_a_lookup_ioTec_could_not_finish_can_be_asked_again_up_to_the_limit(
    api: Api, worker_database: Database
) -> None:
    _, destination_id, _ = await confirmed_number(api, worker_database)
    names = FakeNames(answers=[NoAnswer("iotec_request_ended")])
    runner = worker(worker_database, names, api.clock)
    await run_all(runner, api.clock)
    for _ in range(MAX_LOOKUPS - 1):
        status, body = await ask_again(api, destination_id)
        assert status == 200, body
        await run_all(runner, api.clock)
    assert [row["status"] for row in await lookups(worker_database, destination_id)] == [
        "unanswered"
    ] * MAX_LOOKUPS
    assert (await client_view(api, destination_id))["name_check"] == "limit_reached"
    status, body = await ask_again(api, destination_id)
    assert status == 409 and problem(body) == "name_check_limit_reached"
    # The database holds the limit too.
    owner = Actor("client", (await staff_view(api, destination_id)).client_id)
    async with api.database.actor_transaction(owner) as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO payout_name_lookups (id, destination_id, number, status, "
                    "created_at, updated_at) VALUES (:id, :d, 4, 'queued', now(), now())"
                ),
                {"id": push_id(), "d": destination_id},
            )


async def test_a_name_found_before_the_id_is_approved_is_decided_once_it_is(
    api: Api, worker_database: Database
) -> None:
    owner, destination_id, _ = await confirmed_number(api, worker_database, verified=False)
    names = FakeNames()
    runner = worker(worker_database, names, api.clock)
    await run_all(runner, api.clock)
    assert (await client_view(api, destination_id))["name_check"] == "waiting_for_id"
    status, body = await ask_again(api, destination_id)
    assert status == 409 and problem(body) == "client_not_verified"

    await verify(api.database, owner, api.clock)
    await api.as_client(owner)
    status, body = await ask_again(api, destination_id)
    assert status == 200, body
    await run_all(runner, api.clock)
    assert (await staff_view(api, destination_id)).status.value == "in_review"
    assert len(names.sent) == 1  # decided from the same answer: nothing sent again


async def test_two_asks_at_once_make_one_lookup(api: Api, worker_database: Database) -> None:
    owner, destination_id, _ = await confirmed_number(api, worker_database)
    names = FakeNames(sends=[Unanswerable("iotec_uncertain")])
    await run_all(worker(worker_database, names, api.clock), api.clock)

    async def ask() -> str:
        try:
            async with api.database.actor_transaction(owner) as session:
                await name_lookups.check_again(session, owner, destination_id, api.clock)
        except ApplicationError as error:
            return problem(
                {
                    "error": {
                        "code": error.code,
                        "fields": [{"code": field.code} for field in error.fields],
                    }
                }
            )
        return "asked"

    outcomes = sorted(await asyncio.gather(ask(), ask()))
    assert outcomes == ["asked", "name_check_in_progress"]
    assert len(await lookups(worker_database, destination_id)) == 2


# --- What the database refuses -------------------------------------------------------------


async def test_only_the_network_moves_a_number_on(api: Api, worker_database: Database) -> None:
    owner, destination_id, _ = await confirmed_number(api, worker_database)
    # The client cannot send it to staff themselves.
    async with api.database.actor_transaction(owner) as session:
        with pytest.raises(DBAPIError, match="proved by its network"):
            await session.execute(
                text(
                    "UPDATE payout_destinations SET status = 'in_review', "
                    "proof_kind = 'network_registered_name', proof_document_id = 'doc-1' "
                    "WHERE id = :id"
                ),
                {"id": destination_id},
            )
    # Nor record an answer.
    [lookup] = await lookups(worker_database, destination_id)
    async with api.database.actor_transaction(owner) as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "UPDATE payout_name_lookups SET status = 'unanswered', answered_at = now(), "
                    "error_code = 'x' WHERE id = :id"
                ),
                {"id": lookup["id"]},
            )
    # The worker cannot move it on without an answer, or with a name the answer did not give.
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(DBAPIError, match="as the network's answer says"):
            await session.execute(
                text(
                    "UPDATE payout_destinations SET status = 'in_review', "
                    "proof_kind = 'network_registered_name', proof_document_id = :lookup, "
                    "proof_name = 'SARAH NAKATO', name_match = 'exact' WHERE id = :id"
                ),
                {"id": destination_id, "lookup": lookup["id"]},
            )
    await run_all(
        worker(worker_database, FakeNames(answers=[Found("OKELLO Peter")]), api.clock), api.clock
    )
    # The answer is final, and never deleted.
    with pytest.raises(DBAPIError, match="final"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE payout_name_lookups SET status = 'unanswered' WHERE id = :id"),
                {"id": lookup["id"]},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("DELETE FROM payout_name_lookups WHERE id = :id"), {"id": lookup["id"]}
            )


async def test_a_client_asks_only_for_their_own_waiting_number(
    api: Api, worker_database: Database
) -> None:
    _, destination_id, _ = await confirmed_number(api, worker_database)
    stranger = await api.client()
    status, _ = await ask_again(api, destination_id)
    assert status == 404
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(stranger) as session:
            await session.execute(
                text(
                    "INSERT INTO payout_name_lookups (id, destination_id, number, status, "
                    "created_at, updated_at) VALUES (:id, :d, 2, 'queued', now(), now())"
                ),
                {"id": push_id(), "d": destination_id},
            )
    async with api.database.actor_transaction(stranger) as session:
        seen = await session.scalar(
            text("SELECT count(*) FROM payout_name_lookups WHERE destination_id = :d"),
            {"d": destination_id},
        )
    assert seen == 0


async def test_a_bank_account_is_never_looked_up(api: Api) -> None:
    owner = await api.client()
    async with api.database.actor_transaction(owner) as session:
        view = await destinations.add_destination(
            session,
            owner,
            MobileMoneyNumber("25677" + f"{secrets.randbelow(10**7):07d}"),
            "Sarah Nakato",
            api.clock,
        )
        bank = await destinations.add_destination(
            session,
            owner,
            BankAccount("stanbic", str(secrets.randbelow(10**12) + 10**12)),
            "Sarah Nakato",
            api.clock,
        )
        await destinations.confirm_code(session, owner, bank.id, api.clock)
    status, body = await ask_again(api, bank.id)
    assert status == 409 and problem(body) == "name_check_not_for_this_account"
    # A number still waiting for its code is not looked up either.
    status, body = await ask_again(api, view.id)
    assert status == 409 and problem(body) == "destination_not_awaiting_proof"
