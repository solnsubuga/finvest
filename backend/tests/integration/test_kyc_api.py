"""Identity check routes against real PostgreSQL as finvest_app, with real sessions."""

import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.storage import DisabledStorage, FileStorage, MemoryStorage, sha256_hex
from app.modules.audit.service import events_for_resource
from app.modules.identity.api import COOKIES
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import Actor
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.kyc import SEALER
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

PASSWORD = "copper-harbour-violin"
CLIENT_ORIGIN = "https://app.finvestug.com"
STAFF_ORIGIN = "https://admin.finvestug.com"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2044
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4088
_numbers = itertools.count(790_000_000)


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    clock: FakeClock
    database: Database
    storage: MemoryStorage

    async def client(self) -> dict[str, str]:
        """A new client, signed in on the phone app. Returns the headers to call as them."""
        client_id = await self.identity.create_client(
            phone=f"+256{next(_numbers)}", email=None, password=PASSWORD
        )
        issued = await self.identity.issue_session(
            Actor("client", client_id), second_step="passkey", transport="token"
        )
        return {"Authorization": f"Bearer {issued.token}"}

    async def staff(self, role: str = "compliance") -> dict[str, str]:
        """A new staff member, signed in on the console."""
        staff_id = await self.identity.invite_staff(
            email=f"staff{next(_numbers)}@finvestug.com",
            name="Miriam Atim",
            role=role,  # type: ignore[arg-type]
            created_by=None,
        )
        # Test set-up only, as in test_identity: an active account. Enrolment, the only way the
        # application activates staff, is covered in test_second_steps.
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                    "password_changed_at = created_at WHERE id = :id"
                ),
                {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
            )
        issued = await self.identity.issue_session(
            Actor("staff", staff_id), second_step="passkey", transport="cookie"
        )
        return {"Cookie": f"{COOKIES['staff']}={issued.token}", "Origin": STAFF_ORIGIN}


def an_app(
    test_database: TestDatabase,
    database: Database,
    *,
    sealed: bool = True,
    storage: FileStorage,
) -> tuple[httpx.AsyncClient, IdentityService, FakeClock]:
    settings = test_database.settings("finvest_app").model_copy(
        update={
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
            "trusted_proxies": ["127.0.0.1"],
        }
    )
    clock = FakeClock()
    app = create_app(
        settings,
        database,
        clock,
        HASHER,
        identity_sealer=SEALER if sealed else None,
        identity_provider=ManualProvider(),
        storage=storage,
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    return httpx.AsyncClient(transport=transport, base_url="https://api"), app.state.identity, clock


@pytest.fixture
async def api(
    test_database: TestDatabase, app_database: Database, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Api]:
    for name in ("IDENTITY_DATA_KEY", "IDENTITY_PROVIDER"):
        monkeypatch.delenv(f"FINVEST_{name}", raising=False)
    storage = MemoryStorage()
    http, identity, clock = an_app(test_database, app_database, storage=storage)
    async with http:
        yield Api(http, identity, clock, app_database, storage)


def photo(
    data: bytes = JPEG, content_type: str = "image/jpeg"
) -> dict[str, tuple[str, bytes, str]]:
    return {"photo": ("photo", data, content_type)}


def details(number: str) -> dict[str, str]:
    return {
        "surname": "NAKATO",
        "given_names": "Amara",
        "document_number": number,
        "date_of_birth": "1994-06-22",
        "document_expires_on": "2030-09-18",
    }


def a_number() -> str:
    return f"CM{next(_numbers)}XYZ"


async def hand_in(api: Api, number: str | None = None) -> tuple[dict[str, str], str]:
    """A client hands in a national ID. Returns their headers and the check's id."""
    client = await api.client()
    base = "/client/identity-check"
    document = {"document_type": "national_id", "document_country": "UG"}
    assert (
        await api.http.put(f"{base}/document", json=document, headers=client)
    ).status_code == 200
    for kind in ("front", "back", "selfie"):
        assert (
            await api.http.put(f"{base}/photos/{kind}", files=photo(), headers=client)
        ).status_code == 200
    saved = await api.http.put(
        f"{base}/details", json=details(number or a_number()), headers=client
    )
    assert saved.status_code == 200
    submitted = await api.http.post(f"{base}/submit", headers=client)
    assert submitted.status_code == 200
    return client, submitted.json()["id"]


def error(response: httpx.Response) -> tuple[int, str, list[tuple[str, str]]]:
    body = response.json()["error"]
    return (
        response.status_code,
        body["code"],
        [(str(f["field_path"][-1]), f["code"]) for f in body["fields"]],
    )


async def actions(api: Api, check_id: str) -> list[str]:
    async with api.database.actor_transaction(new_staff()) as session:
        events = await events_for_resource(session, kyc.RESOURCE, check_id)
    return [event.action for event in reversed(events)]


# The client ---------------------------------------------------------------------------------------


async def test_a_client_goes_from_nothing_to_a_check_waiting_for_a_person(api: Api) -> None:
    client = await api.client()
    base = "/client/identity-check"
    assert (await api.http.get(base, headers=client)).json() == {"check": None}

    started = await api.http.put(
        f"{base}/document",
        json={"document_type": "passport", "document_country": "UG"},
        headers=client,
    )
    assert started.json()["status"] == "draft"
    assert started.json()["missing"] == ["front", "selfie", "details"]

    not_ready = await api.http.post(f"{base}/submit", headers=client)
    assert error(not_ready) == (
        422,
        "validation_failed",
        [("front", "missing"), ("selfie", "missing"), ("details", "missing")],
    )

    for kind in ("front", "selfie"):
        sent = await api.http.put(f"{base}/photos/{kind}", files=photo(), headers=client)
        assert sent.status_code == 200
        assert {"kind": kind, "content_type": "image/jpeg", "byte_size": len(JPEG)} in sent.json()[
            "photos"
        ]
    # The API stored each file under the check, with the digest the record holds.
    stored = sorted(
        key for key in api.storage.files if key.startswith(f"kyc/{started.json()['id']}/")
    )
    assert len(stored) == 2
    async with api.database.actor_transaction(new_staff()) as session:
        digests = await session.execute(
            text("SELECT storage_key, sha256 FROM kyc_photos WHERE check_id = :id"),
            {"id": started.json()["id"]},
        )
        assert sorted(digests.tuples()) == [(key, sha256_hex(JPEG)) for key in stored]

    number = a_number()
    saved = await api.http.put(f"{base}/details", json=details(number), headers=client)
    assert saved.json()["details"] == {**details(number), "tin": None}
    assert saved.json()["missing"] == []

    submitted = await api.http.post(f"{base}/submit", headers=client)
    assert submitted.json()["status"] == "needs_review"
    seen = (await api.http.get(base, headers=client)).json()["check"]
    assert seen["status"] == "needs_review"
    assert seen["account_number"] is None
    # Nothing about how the check is being handled reaches the client.
    assert not {"review_flags", "provider", "automatic_checks", "decided_by"} & seen.keys()


async def test_bad_details_and_photos_are_refused_field_by_field(api: Api) -> None:
    client = await api.client()
    base = "/client/identity-check"
    await api.http.put(
        f"{base}/document",
        json={"document_type": "passport", "document_country": "UG"},
        headers=client,
    )
    bad = {**details("B1"), "date_of_birth": "2015-01-01", "document_expires_on": "2020-01-01"}
    refused = await api.http.put(f"{base}/details", json=bad, headers=client)
    status, code, fields = error(refused)
    assert (status, code) == (422, "validation_failed")
    assert {"document_number", "date_of_birth", "document_expires_on"} <= {f for f, _ in fields}

    # A passport has no back. Each refusal comes before anything is stored.
    no_back = await api.http.put(f"{base}/photos/back", files=photo(), headers=client)
    assert error(no_back) == (422, "validation_failed", [("kind", "photo_not_part_of_document")])
    huge = JPEG + b"\x00" * (10 * 1024 * 1024)
    too_big = await api.http.put(f"{base}/photos/front", files=photo(huge), headers=client)
    assert error(too_big)[2] == [("byte_size", "invalid_size")]
    pdf = await api.http.put(
        f"{base}/photos/front", files=photo(b"%PDF-1.7", "application/pdf"), headers=client
    )
    assert error(pdf)[2] == [("content_type", "unsupported_content_type")]
    # Called a JPEG, but it is not one.
    page = b"<html><script>alert(1)</script></html>"
    lying = await api.http.put(f"{base}/photos/front", files=photo(page), headers=client)
    assert error(lying)[2] == [("photo", "not_an_image_of_that_type")]
    unknown = await api.http.put(f"{base}/photos/elbow", files=photo(), headers=client)
    assert unknown.status_code == 422
    assert api.storage.files == {}


async def test_nobody_reaches_a_check_without_the_right_session(api: Api) -> None:
    client, check_id = await hand_in(api)
    assert (await api.http.get("/client/identity-check")).status_code == 401
    assert (await api.http.get("/staff/identity-checks")).status_code == 401
    # A client's token is not a staff session, and support staff do not review identities.
    assert (await api.http.get("/staff/identity-checks", headers=client)).status_code == 401
    support = await api.staff("support")
    for method, path in (
        ("GET", "/staff/identity-checks"),
        ("GET", f"/staff/identity-checks/{check_id}"),
        ("POST", f"/staff/identity-checks/{check_id}/document-number"),
        ("POST", f"/staff/identity-checks/{check_id}/photo-links"),
        ("POST", f"/staff/identity-checks/{check_id}/approve"),
    ):
        refused = await api.http.request(method, path, headers=support, json={"reason": "x"})
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden"), path
    # One client never sees another's check.
    other = await api.client()
    assert (await api.http.get("/client/identity-check", headers=other)).json() == {"check": None}


# Staff --------------------------------------------------------------------------------------------


async def test_staff_see_the_number_masked_and_each_look_is_on_the_audit_trail(api: Api) -> None:
    number = a_number()
    _, check_id = await hand_in(api, number)
    staff = await api.staff()

    queue = (await api.http.get("/staff/identity-checks", headers=staff)).json()["items"]
    entry = next(item for item in queue if item["id"] == check_id)
    assert entry["name"] == "Amara Nakato"
    assert entry["review_flags"] == ["no_automatic_checks"]

    opened = await api.http.get(f"/staff/identity-checks/{check_id}", headers=staff)
    check = opened.json()
    assert check["document_number_masked"] == number[:4] + "•" * (len(number) - 7) + number[-3:]
    assert number not in opened.text
    assert "tin" not in check
    assert check["date_of_birth"] == "1994-06-22"
    assert check["allowed"] == ["approve", "reject", "ask_for_photos", "escalate"]
    assert [photo["kind"] for photo in check["photos"]] == ["back", "front", "selfie"]
    assert "kyc.check.document_number_shown" not in await actions(api, check_id)

    shown = await api.http.post(f"/staff/identity-checks/{check_id}/document-number", headers=staff)
    assert shown.json() == {"document_number": number}
    links = await api.http.post(f"/staff/identity-checks/{check_id}/photo-links", headers=staff)
    photos = links.json()["photos"]
    assert [photo["kind"] for photo in photos] == ["back", "front", "selfie"]
    assert all(photo["url"].startswith(f"memory://kyc/{check_id}/") for photo in photos)
    assert links.json()["expires_at"] == (
        api.clock.now() + timedelta(minutes=5)
    ).isoformat().replace("+00:00", "Z")
    assert (await actions(api, check_id))[-2:] == [
        "kyc.check.document_number_shown",
        "kyc.check.photos_viewed",
    ]


async def test_staff_approve_once_with_a_reason_and_the_client_gets_the_number(api: Api) -> None:
    client, check_id = await hand_in(api)
    staff = await api.staff("operations")
    path = f"/staff/identity-checks/{check_id}/approve"

    blank = await api.http.post(path, json={"reason": "   "}, headers=staff)
    assert error(blank) == (422, "validation_failed", [("reason", "missing")])

    approved = await api.http.post(path, json={"reason": "Photos match the ID"}, headers=staff)
    body = approved.json()
    assert (body["status"], body["allowed"]) == ("approved", [])
    assert body["account_number"].startswith("10") and len(body["account_number"]) == 10

    again = await api.http.post(path, json={"reason": "Twice"}, headers=staff)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "conflict")
    mine = (await api.http.get("/client/identity-check", headers=client)).json()["check"]
    assert mine["account_number"] == body["account_number"]
    missing = await api.http.get("/staff/identity-checks/-Nabc0000000000000Z9", headers=staff)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")


async def test_staff_ask_for_a_photo_escalate_and_reject(api: Api) -> None:
    client, check_id = await hand_in(api)
    staff = await api.staff()
    base = f"/staff/identity-checks/{check_id}"

    nothing = await api.http.post(
        f"{base}/ask-for-photos", json={"photos": [], "client_message": "Retake"}, headers=staff
    )
    assert nothing.status_code == 422
    asked = await api.http.post(
        f"{base}/ask-for-photos",
        json={"photos": ["selfie"], "client_message": "Your selfie was too dark."},
        headers=staff,
    )
    assert (asked.json()["status"], asked.json()["allowed"]) == ("awaiting_client", [])
    mine = (await api.http.get("/client/identity-check", headers=client)).json()["check"]
    assert mine["retake_photos"] == ["selfie"]
    assert mine["client_message"] == "Your selfie was too dark."

    api.clock.advance(minutes=1)
    front = await api.http.put("/client/identity-check/photos/front", files=photo(), headers=client)
    assert (front.status_code, front.json()["error"]["code"]) == (409, "conflict")
    retake = await api.http.put(
        "/client/identity-check/photos/selfie", files=photo(PNG, "image/png"), headers=client
    )
    assert retake.status_code == 200
    assert (await api.http.post("/client/identity-check/submit", headers=client)).status_code == 200

    escalated = await api.http.post(
        f"{base}/escalate", json={"reason": "Name spelling differs"}, headers=staff
    )
    assert escalated.json()["allowed"] == ["approve", "reject", "ask_for_photos"]
    rejected = await api.http.post(
        f"{base}/reject",
        json={"reason": "Forged document", "client_message": "We could not verify your ID."},
        headers=staff,
    )
    assert (rejected.json()["status"], rejected.json()["allowed"]) == ("rejected", [])
    mine = (await api.http.get("/client/identity-check", headers=client)).json()["check"]
    assert (mine["status"], mine["client_message"]) == ("rejected", "We could not verify your ID.")


# Configuration ------------------------------------------------------------------------------------


async def test_without_the_data_key_or_storage_the_routes_say_unavailable(
    test_database: TestDatabase, app_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("IDENTITY_DATA_KEY", "IDENTITY_PROVIDER"):
        monkeypatch.delenv(f"FINVEST_{name}", raising=False)
    http, identity, clock = an_app(
        test_database, app_database, sealed=False, storage=MemoryStorage()
    )
    async with http:
        client = await Api(http, identity, clock, app_database, MemoryStorage()).client()
        refused = await http.get("/client/identity-check", headers=client)
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "unavailable")

    http, identity, clock = an_app(test_database, app_database, storage=DisabledStorage())
    async with http:
        client = await Api(http, identity, clock, app_database, MemoryStorage()).client()
        document = {"document_type": "passport", "document_country": "UG"}
        started = await http.put("/client/identity-check/document", json=document, headers=client)
        assert started.status_code == 200
        refused = await http.put(
            "/client/identity-check/photos/front", files=photo(), headers=client
        )
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "unavailable")
        async with app_database.actor_transaction(new_staff()) as session:
            photos = await session.execute(
                text("SELECT count(*) FROM kyc_photos WHERE check_id = :id"),
                {"id": started.json()["id"]},
            )
            assert photos.scalar_one() == 0


async def test_nothing_personal_is_logged(api: Api, capsys: pytest.CaptureFixture[str]) -> None:
    number = a_number()
    _, check_id = await hand_in(api, number)
    staff = await api.staff()
    await api.http.get(f"/staff/identity-checks/{check_id}", headers=staff)
    await api.http.post(f"/staff/identity-checks/{check_id}/document-number", headers=staff)
    await api.http.post(f"/staff/identity-checks/{check_id}/photo-links", headers=staff)
    logged = capsys.readouterr()
    for secret in (number, "NAKATO", "Amara", "1994-06-22", f"kyc/{check_id}/"):
        assert secret not in logged.out
        assert secret not in logged.err
