"""A signed-in client changes their own password, mobile number and email address."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.infrastructure.storage import MemoryStorage
from app.modules.kyc.face_confirmation_jobs import FaceConfirmationJobs
from app.modules.kyc.providers import DevelopmentStubFaceMatcher, DevelopmentStubLiveness
from app.shared.actors import Actor
from tests.support.database import TestDatabase
from tests.support.http import HASHER, PASSWORD, Api, running_api
from tests.support.kyc import SEALER, verify

pytestmark = pytest.mark.integration

NEW_PASSWORD = "lantern-meadow-quartz"


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def account(api: Api, client: Actor) -> dict[str, object]:
    async with api.database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT phone, email, email_verified_at, password_hash, restricted_until "
                    "FROM identity_clients WHERE id = :id"
                ),
                {"id": client.id},
            )
        ).one()
        return dict(row._mapping)


async def sent(worker: Database, recipient: str) -> list[tuple[str, str | None]]:
    """The template and code of each message queued for a recipient, oldest first."""
    async with worker.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT template, variables->>'code' AS code FROM notification_messages "
                "WHERE recipient = :recipient ORDER BY id"
            ),
            {"recipient": recipient},
        )
        return [(row.template, row.code) for row in rows]


async def other_session(api: Api, client: Actor) -> str:
    issued = await api.identity.issue_session(client, second_step="passkey", transport="cookie")
    return issued.token


async def live(api: Api, token: str) -> bool:
    return await api.identity.client_session(token) is not None


async def with_code_only(api: Api, client: Actor) -> None:
    """Sign this client in again with a texted code, so no passkey or app was used."""
    issued = await api.identity.issue_session(client, second_step="code", transport="token")
    api.http.headers["Authorization"] = f"Bearer {issued.token}"


def error(response: object) -> str:
    return str(response.json()["error"]["code"])  # type: ignore[attr-defined]


async def test_every_change_needs_a_passkey_or_app_not_a_texted_code(api: Api) -> None:
    client = await api.client()
    await with_code_only(api, client)
    for method, path, body in (
        ("PUT", "/client/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}),
        ("POST", "/client/phone-change", {"phone": "+256701555013"}),
        ("PUT", "/client/email", {"email": "amara@example.com"}),
    ):
        answer = await api.http.request(method, path, json=body)
        assert answer.status_code == 403 and error(answer) == "confirm_it_is_you", path


async def test_a_passkey_sign_in_counts_for_ten_minutes(api: Api) -> None:
    await api.client()
    api.clock.advance(minutes=11)
    answer = await api.http.put("/client/email", json={"email": "late@example.com"})
    assert error(answer) == "confirm_it_is_you"


async def test_changing_the_password_needs_the_current_one_and_ends_other_sessions(
    api: Api, worker_database: Database
) -> None:
    client = await api.client()
    elsewhere = await other_session(api, client)
    wrong = await api.http.put(
        "/client/password", json={"current_password": "not-it-at-all", "new_password": NEW_PASSWORD}
    )
    assert wrong.status_code == 401 and error(wrong) == "invalid_credentials"
    weak = await api.http.put(
        "/client/password", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert weak.status_code == 422
    assert weak.json()["error"]["fields"][0]["field_path"] == ["new_password"]
    assert await live(api, elsewhere)

    changed = await api.http.put(
        "/client/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert changed.status_code == 204, changed.text
    row = await account(api, client)
    assert await HASHER.verify(NEW_PASSWORD, str(row["password_hash"]))
    assert not await live(api, elsewhere)
    assert (await api.http.get("/client/session")).status_code == 200
    assert ("password_changed", None) in await sent(worker_database, str(row["phone"]))


async def test_a_new_number_is_proved_with_a_code_and_then_restricts_withdrawals(
    api: Api, worker_database: Database
) -> None:
    client = await api.client()
    old_phone = str((await account(api, client))["phone"])
    elsewhere = await other_session(api, client)
    new_phone = "+256701555013"
    started = await api.http.post("/client/phone-change", json={"phone": "0701 555013"})
    assert started.status_code == 201, started.text
    challenge = started.json()["challenge_id"]
    [(template, code)] = await sent(worker_database, new_phone)
    assert template == "phone_change_code" and code

    wrong = await api.http.post(
        f"/client/phone-change/{challenge}/confirmation", json={"code": "000000"}
    )
    assert wrong.status_code == 401 and error(wrong) == "invalid_code"
    assert (await account(api, client))["phone"] == old_phone

    moved = await api.http.post(
        f"/client/phone-change/{challenge}/confirmation", json={"code": code}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json() == {"phone": new_phone}
    row = await account(api, client)
    assert row["phone"] == new_phone and row["restricted_until"] is not None
    assert not await live(api, elsewhere)
    assert (await api.http.get("/client/session")).json()["phone"] == new_phone
    assert ("own_phone_changed", None) in await sent(worker_database, old_phone)
    again = await api.http.post(
        f"/client/phone-change/{challenge}/confirmation", json={"code": code}
    )
    assert again.status_code == 410


async def test_a_number_with_an_account_cannot_be_taken(
    api: Api, worker_database: Database
) -> None:
    owner = await api.client()
    taken = str((await account(api, owner))["phone"])
    client = await api.client()
    started = await api.http.post("/client/phone-change", json={"phone": taken})
    challenge = started.json()["challenge_id"]
    code = (await sent(worker_database, taken))[-1][1]
    refused = await api.http.post(
        f"/client/phone-change/{challenge}/confirmation", json={"code": code}
    )
    assert refused.status_code == 409 and error(refused) == "phone_taken"
    assert (await account(api, client))["phone"] != taken


async def test_a_new_email_is_unproved_and_the_old_address_is_told(
    api: Api, worker_database: Database
) -> None:
    client = await api.client()
    first = await api.http.put("/client/email", json={"email": "Amara@Example.com"})
    assert first.status_code == 200, first.text
    assert first.json() == {"email": "amara@example.com"}
    assert [t for t, _ in await sent(worker_database, "amara@example.com")] == [
        "email_verification"
    ]
    second = await api.http.put("/client/email", json={"email": "nakato@example.com"})
    assert second.status_code == 200
    row = await account(api, client)
    assert row["email"] == "nakato@example.com" and row["email_verified_at"] is None
    assert "email_changed" in [t for t, _ in await sent(worker_database, "amara@example.com")]
    same = await api.http.put("/client/email", json={"email": "nakato@example.com"})
    assert same.status_code == 422


async def test_only_the_client_changes_their_own_number_and_email(
    api: Api, app_database: Database
) -> None:
    client = await api.client()
    staff = await api.staff("admin")
    with pytest.raises(DBAPIError, match="only a client changes their own email"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text("SELECT identity_change_own_email('x@example.com')"))
    with pytest.raises(DBAPIError, match="its code spent in the same transaction"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text("SELECT identity_change_own_phone('-Nabc0000000000000C1', NULL, now())")
            )
    for statement in (
        "UPDATE identity_clients SET phone = '+256701555099' WHERE id = :id",
        "UPDATE identity_clients SET email = 'x@example.com' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(text(statement), {"id": client.id})


async def test_a_number_code_is_spent_only_on_the_session_that_asked(
    api: Api, worker_database: Database
) -> None:
    client = await api.client()
    old_phone = str((await account(api, client))["phone"])
    new_phone = "+256701555014"
    started = await api.http.post("/client/phone-change", json={"phone": new_phone})
    challenge = started.json()["challenge_id"]
    [(_, code)] = await sent(worker_database, new_phone)

    # Another session of the same client, also signed in with a passkey just now.
    other = await api.identity.issue_session(client, second_step="passkey", transport="token")
    api.http.headers["Authorization"] = f"Bearer {other.token}"
    elsewhere = await api.http.post(
        f"/client/phone-change/{challenge}/confirmation", json={"code": code}
    )
    assert elsewhere.status_code == 410, elsewhere.text
    assert (await account(api, client))["phone"] == old_phone


@pytest.fixture
async def selfie_api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    """The API with live selfies on, as with ioTec, so a verified client is asked for one."""
    async with running_api(
        test_database,
        app_database,
        identity_sealer=SEALER,
        identity_liveness=DevelopmentStubLiveness(),
        face_matcher=DevelopmentStubFaceMatcher(),
    ) as api:
        yield api


async def pass_selfie(api: Api, worker: Database, client: Actor) -> None:
    """Take a live selfie on the signed-in session, as the app and worker do."""
    started = await api.http.post("/client/session/confirmation/selfie")
    assert started.status_code == 201, started.text
    attempt = started.json()["id"]
    handlers = FaceConfirmationJobs(
        worker,
        SEALER,
        DevelopmentStubLiveness(),
        DevelopmentStubFaceMatcher(),
        MemoryStorage(),
        api.clock,
    ).handlers()
    runner = Worker(worker, handlers, WorkerConfig(), api.clock)
    for _ in range(4):
        while await runner.run_once():
            pass
        api.clock.advance(seconds=5)
    answer = await api.http.get(f"/client/session/confirmation/selfie/{attempt}")
    assert answer.json()["status"] == "passed", answer.text


async def test_the_session_says_until_when_a_change_needs_no_confirmation(api: Api) -> None:
    await api.client()
    view = (await api.http.get("/client/session")).json()
    assert view["confirmed_at"] is not None
    assert view["confirmed_until"] > view["confirmed_at"]
    client = await api.client()
    await with_code_only(api, client)
    view = (await api.http.get("/client/session")).json()
    assert view["confirmed_at"] is None and view["confirmed_until"] is None


async def test_a_verified_client_also_takes_a_live_selfie_for_a_password_or_number(
    selfie_api: Api, worker_database: Database
) -> None:
    api = selfie_api
    client = await api.client()
    await verify(api.database, client, api.clock)
    state = (await api.http.get("/client/session/confirmation/selfie")).json()
    assert state["needed"] is True and state["confirmed"] is False
    for method, path, body in (
        ("PUT", "/client/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}),
        ("POST", "/client/phone-change", {"phone": "+256701555019"}),
    ):
        answer = await api.http.request(method, path, json=body)
        assert answer.status_code == 403 and error(answer) == "confirm_with_selfie", path
    # An email change needs the passkey or app only.
    email = await api.http.put("/client/email", json={"email": "selfie@example.com"})
    assert email.status_code == 200, email.text

    await pass_selfie(api, worker_database, client)
    assert (await api.http.get("/client/session/confirmation/selfie")).json()["confirmed"]
    changed = await api.http.put(
        "/client/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert changed.status_code == 204, changed.text

    # It counts on this session only, and for ten minutes.
    await api.as_client(client)
    again = await api.http.post("/client/phone-change", json={"phone": "+256701555019"})
    assert error(again) == "confirm_with_selfie"


async def test_the_passkey_is_asked_for_before_the_selfie(selfie_api: Api) -> None:
    api = selfie_api
    client = await api.client()
    await verify(api.database, client, api.clock)
    await with_code_only(api, client)
    answer = await api.http.post("/client/phone-change", json={"phone": "+256701555020"})
    assert error(answer) == "confirm_it_is_you"


async def test_a_client_with_no_selfie_on_file_changes_with_the_passkey_alone(
    selfie_api: Api,
) -> None:
    api = selfie_api
    await api.client()
    state = (await api.http.get("/client/session/confirmation/selfie")).json()
    assert state == {
        "needed": False,
        "confirmed": False,
        "problem": "no_selfie_on_file",
        "attempts_left": 5,
        "latest": None,
    }
    started = await api.http.post("/client/session/confirmation/selfie")
    assert started.status_code == 422
    changed = await api.http.put(
        "/client/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert changed.status_code == 204, changed.text
