"""The guided selfie through the API, with a fake face quality service, on real PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.storage import MemoryStorage
from app.modules.identity.service import IdentityService
from app.modules.kyc import poses
from app.modules.kyc.domain.poses import FrameReading, Pose, Quality, QualityUnavailable
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import Actor
from app.shared.push_ids import push_id
from tests.integration.test_kyc_api import (
    CLIENT_ORIGIN,
    HASHER,
    STAFF_ORIGIN,
    Api,
    a_number,
    an_app,
    details,
    error,
)
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.kyc import SEALER
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

BASE = "/client/identity-check"


def frame(tag: str) -> bytes:
    """A JPEG the fake service knows by its tag."""
    return b"\xff\xd8\xff\xe0" + tag.encode().ljust(2044, b"\x00")


def reading(yaw: float = 0, pitch: float = 0, **changes: object) -> FrameReading:
    fields: dict[str, object] = {
        "faces": 1,
        "yaw": yaw,
        "pitch": pitch,
        "face_width": 0.4,
        "off_centre_x": 0.0,
        "off_centre_y": 0.0,
        "eyes_open": {"left": True, "right": True},
    }
    fields.update(changes)
    return FrameReading(**fields)  # type: ignore[arg-type]


READINGS = {
    "straight": reading(),
    "left": reading(yaw=-30),
    "right": reading(yaw=27),
    "up": reading(pitch=16),
    "down": reading(pitch=-14),
    "dark-and-small": reading(face_width=0.1, failed=frozenset({Quality.TOO_DARK})),
    "barely-turned": reading(yaw=-8),
    "two-people": FrameReading(faces=2),
}


@dataclass
class FakeQuality:
    """Answers by the frame's tag. `down` makes it unreachable."""

    down: bool = False
    read_count: int = 0
    seen: list[str] = field(default_factory=list)

    async def read(self, image: bytes) -> FrameReading:
        self.read_count += 1
        if self.down:
            raise QualityUnavailable
        tag = image[4:].rstrip(b"\x00").decode()
        self.seen.append(tag)
        return READINGS[tag]


@dataclass
class Guided(Api):
    quality: FakeQuality = field(default_factory=FakeQuality)


@pytest.fixture
async def api(
    test_database: TestDatabase, app_database: Database, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Guided]:
    for name in ("IDENTITY_DATA_KEY", "IDENTITY_PROVIDER"):
        monkeypatch.delenv(f"FINVEST_{name}", raising=False)
    settings = test_database.settings("finvest_app").model_copy(
        update={
            "client_allowed_origins": [CLIENT_ORIGIN],
            "staff_allowed_origins": [STAFF_ORIGIN],
            "trusted_proxies": ["127.0.0.1"],
        }
    )
    clock, storage, quality = FakeClock(), MemoryStorage(), FakeQuality()
    app = create_app(
        settings,
        app_database,
        clock,
        HASHER,
        identity_sealer=SEALER,
        identity_provider=ManualProvider(),
        storage=storage,
        face_quality=quality,
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    identity: IdentityService = app.state.identity
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Guided(http, identity, clock, app_database, storage, quality)


def upload(tag: str) -> dict[str, tuple[str, bytes, str]]:
    return {"photo": ("frame", frame(tag), "image/jpeg")}


async def ready(api: Guided) -> dict[str, str]:
    """A client with a national ID's sides and details, still to take the selfie."""
    client = await api.client()
    document = {"document_type": "national_id", "document_country": "UG"}
    assert (await api.http.put(f"{BASE}/document", json=document, headers=client)).is_success
    for kind in ("front", "back"):
        sent = await api.http.put(f"{BASE}/photos/{kind}", files=upload(kind), headers=client)
        assert sent.is_success
    saved = await api.http.put(f"{BASE}/details", json=details(a_number()), headers=client)
    assert saved.is_success
    return client


async def take(api: Guided, client: dict[str, str], *poses: str) -> httpx.Response:
    answer = None
    for pose in poses:
        answer = await api.http.put(f"{BASE}/poses/{pose}", files=upload(pose), headers=client)
        assert answer.status_code == 200, answer.text
    assert answer is not None
    return answer


async def stored_frames(api: Guided) -> int:
    return sum(1 for key in api.storage.files if "/poses/" in key)


async def test_a_client_takes_the_guided_selfie_and_hands_it_in(api: Guided) -> None:
    client = await ready(api)
    # Front and back are not read by the face quality service.
    assert api.quality.seen == []

    bad = await api.http.put(
        f"{BASE}/photos/selfie", files=upload("dark-and-small"), headers=client
    )
    assert error(bad) == (
        422,
        "validation_failed",
        [("photo", "face_too_small"), ("photo", "too_dark")],
    )
    selfie = await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    assert selfie.status_code == 200
    body = selfie.json()
    assert body["guided_selfie"] is True
    assert body["poses"] == []
    assert body["missing"] == ["pose_left", "pose_right", "pose_up", "pose_down"]

    not_ready = await api.http.post(f"{BASE}/submit", headers=client)
    assert error(not_ready)[2] == [
        ("pose_left", "missing"),
        ("pose_right", "missing"),
        ("pose_up", "missing"),
        ("pose_down", "missing"),
    ]

    turned = await take(api, client, "left", "right", "up", "down")
    assert turned.json()["poses"] == ["left", "right", "up", "down"]
    assert turned.json()["missing"] == []
    submitted = await api.http.post(f"{BASE}/submit", headers=client)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "needs_review"

    # Handed in, the frames cannot change.
    late = await api.http.put(f"{BASE}/poses/left", files=upload("left"), headers=client)
    assert late.status_code == 409


async def test_a_frame_is_refused_with_its_reasons_and_kept_only_when_good(api: Guided) -> None:
    client = await ready(api)
    early = await api.http.put(f"{BASE}/poses/left", files=upload("left"), headers=client)
    assert error(early) == (422, "validation_failed", [("pose", "selfie_first")])
    await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)

    barely = await api.http.put(f"{BASE}/poses/left", files=upload("barely-turned"), headers=client)
    assert error(barely) == (422, "validation_failed", [("frame", "turn_more")])
    crowd = await api.http.put(f"{BASE}/poses/up", files=upload("two-people"), headers=client)
    assert error(crowd) == (422, "validation_failed", [("frame", "several_faces")])
    assert await stored_frames(api) == 0

    await take(api, client, "left")
    # Turned the same way as the left turn already kept.
    same_way = await api.http.put(f"{BASE}/poses/right", files=upload("left"), headers=client)
    assert error(same_way) == (422, "validation_failed", [("frame", "wrong_way")])
    assert await stored_frames(api) == 1


async def test_a_new_selfie_retires_the_poses_taken_with_the_old_one(api: Guided) -> None:
    client = await ready(api)
    await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    await take(api, client, "left", "right", "up", "down")
    again = await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    assert again.json()["poses"] == []
    assert again.json()["missing"] == ["pose_left", "pose_right", "pose_up", "pose_down"]
    # A retaken pose retires the earlier frame of the same pose. Every frame is kept.
    retaken = await take(api, client, "up", "up")
    assert retaken.json()["poses"] == ["up"]
    async with api.database.actor_transaction(new_staff()) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT pose, replaced_at IS NULL AS current FROM kyc_pose_frames "
                    "WHERE check_id = :id ORDER BY created_at, id"
                ),
                {"id": retaken.json()["id"]},
            )
        ).all()
    assert [(r.pose, r.current) for r in rows] == [
        ("left", False),
        ("right", False),
        ("up", False),
        ("down", False),
        ("up", False),
        ("up", True),
    ]


async def test_with_the_service_down_nothing_is_kept_and_the_client_tries_again(
    api: Guided,
) -> None:
    client = await ready(api)
    api.quality.down = True
    answer = await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    assert answer.status_code == 503
    check = (await api.http.get(BASE, headers=client)).json()["check"]
    assert "selfie" in check["missing"]
    api.quality.down = False
    assert (
        await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    ).is_success
    api.quality.down = True
    answer = await api.http.put(f"{BASE}/poses/left", files=upload("left"), headers=client)
    assert answer.status_code == 503
    assert await stored_frames(api) == 0


async def test_staff_see_the_frames_with_their_angles(api: Guided) -> None:
    client = await ready(api)
    await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    await take(api, client, "left", "right", "up", "down")
    check_id = (await api.http.post(f"{BASE}/submit", headers=client)).json()["id"]
    staff = await api.staff()
    links = await api.http.post(f"/staff/identity-checks/{check_id}/photo-links", headers=staff)
    assert links.status_code == 200
    shown = {p["pose"]: (p["yaw"], p["pitch"]) for p in links.json()["poses"]}
    assert shown == {"left": (-30, 0), "right": (27, 0), "up": (0, 16), "down": (0, -14)}
    assert all(p["url"] for p in links.json()["poses"])


async def test_without_the_guided_selfie_there_are_no_pose_routes(
    test_database: TestDatabase, app_database: Database
) -> None:
    http, identity, clock = an_app(test_database, app_database, storage=MemoryStorage())
    async with http:
        plain = Api(http, identity, clock, app_database, MemoryStorage())
        client = await plain.client()
        answer = await http.put(f"{BASE}/poses/left", files=upload("left"), headers=client)
        assert answer.status_code == 404


async def test_the_database_refuses_frames_once_handed_in_and_never_rewrites_one(
    api: Guided,
) -> None:
    client = await ready(api)
    await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    await take(api, client, "left", "right", "up", "down")
    check_id = (await api.http.post(f"{BASE}/submit", headers=client)).json()["id"]
    async with api.database.actor_transaction(new_staff()) as session:
        client_id = (
            await session.execute(
                text("SELECT client_id FROM kyc_checks WHERE id = :id"), {"id": check_id}
            )
        ).scalar_one()
    owner = Actor("client", client_id)
    insert = text(
        "INSERT INTO kyc_pose_frames (id, check_id, pose, storage_key, content_type, byte_size, "
        "sha256, yaw, pitch, created_at) VALUES (:id, :check_id, 'left', :key, 'image/jpeg', "
        "10, :sha, -30, 0, now())"
    )
    frame_id = push_id()
    with pytest.raises(DBAPIError, match="not with the client"):
        async with api.database.actor_transaction(owner) as session:
            await session.execute(
                insert,
                {"id": frame_id, "check_id": check_id, "key": f"x/{frame_id}", "sha": "a" * 64},
            )
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(owner) as session:
            await session.execute(
                text("UPDATE kyc_pose_frames SET yaw = 0 WHERE check_id = :id"), {"id": check_id}
            )
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(owner) as session:
            await session.execute(
                text("DELETE FROM kyc_pose_frames WHERE check_id = :id"), {"id": check_id}
            )


async def test_two_uploads_of_one_pose_at_once_take_turns(api: Guided) -> None:
    client = await ready(api)
    selfie = await api.http.put(f"{BASE}/photos/selfie", files=upload("straight"), headers=client)
    check_id = selfie.json()["id"]
    async with api.database.actor_transaction(new_staff()) as session:
        client_id = (
            await session.execute(
                text("SELECT client_id FROM kyc_checks WHERE id = :id"), {"id": check_id}
            )
        ).scalar_one()
    owner = Actor("client", client_id)

    async def keep(session: AsyncSession) -> str:
        frame = await poses.add_frame(
            session,
            owner,
            Pose.UP,
            frame_id=push_id(),
            content_type="image/jpeg",
            byte_size=10,
            sha256="b" * 64,
            reading=READINGS["up"],
            clock=api.clock,
        )
        return frame.id

    async def second() -> str:
        async with api.database.actor_transaction(owner) as session:
            return await keep(session)

    async with api.database.actor_transaction(owner) as first:
        first_id = await keep(first)
        racing = asyncio.create_task(second())
        await asyncio.sleep(0.3)
        # The second waits on the check's lock while the first is open.
        assert not racing.done()
    second_id = await racing
    async with api.database.actor_transaction(new_staff()) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, replaced_at IS NULL AS current FROM kyc_pose_frames "
                    "WHERE check_id = :id"
                ),
                {"id": check_id},
            )
        ).all()
    assert {(r.id, r.current) for r in rows} == {(first_id, False), (second_id, True)}
