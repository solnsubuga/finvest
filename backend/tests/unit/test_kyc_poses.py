"""The guided selfie's frame rules, and Regula's face quality check against a fake Regula."""

import json

import httpx
import pytest

from app.modules.kyc.domain.poses import (
    FrameReading,
    Kept,
    Pose,
    Quality,
    QualityUnavailable,
    frame_problems,
    poses_apart,
    poses_missing,
)
from app.modules.kyc.regula_quality import RegulaFaceQuality, reading_of
from app.modules.kyc.settings import KycSettings

FACE = "http://regula-face:41101"


def good(yaw: float | None = 0, pitch: float | None = 0, **changes: object) -> FrameReading:
    """A sharp, well lit, centred face of a good size, eyes open, at these angles."""
    fields: dict[str, object] = {
        "faces": 1,
        "yaw": yaw,
        "pitch": pitch,
        "face_width": 0.45,
        "off_centre_x": 0.02,
        "off_centre_y": -0.05,
        "eyes_open": {"left": True, "right": True},
    }
    fields.update(changes)
    return FrameReading(**fields)  # type: ignore[arg-type]


# The rules ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pose", "reading"),
    [
        (None, good()),
        (None, good(yaw=-9.6, pitch=9)),
        (Pose.LEFT, good(yaw=-28, pitch=4)),
        (Pose.RIGHT, good(yaw=31, pitch=-6)),
        (Pose.UP, good(yaw=3, pitch=18)),
        (Pose.DOWN, good(yaw=-5, pitch=-14)),
        # Turned away, one eye hidden or seen closed is fine.
        (Pose.LEFT, good(yaw=-28, eyes_open={"left": False, "right": True})),
        (Pose.RIGHT, good(yaw=28, eyes_open={})),
    ],
)
def test_a_good_frame_has_no_problems(pose: Pose | None, reading: FrameReading) -> None:
    assert frame_problems(pose, reading) == ()


@pytest.mark.parametrize(
    ("pose", "reading", "problems"),
    [
        (None, FrameReading(faces=0), (Quality.NO_FACE,)),
        (Pose.UP, FrameReading(faces=2), (Quality.SEVERAL_FACES,)),
        (None, good(face_width=0.1), (Quality.FACE_TOO_SMALL,)),
        (None, good(face_width=None), (Quality.FACE_TOO_SMALL,)),
        (None, good(off_centre_x=0.3), (Quality.NOT_CENTRED,)),
        (None, good(off_centre_y=-0.25), (Quality.NOT_CENTRED,)),
        (None, good(off_centre_x=None), (Quality.NOT_CENTRED,)),
        (None, good(failed=frozenset({Quality.BLURRY})), (Quality.BLURRY,)),
        (
            None,
            good(failed=frozenset({Quality.TOO_DARK, Quality.GLARE})),
            (Quality.TOO_DARK, Quality.GLARE),
        ),
        (None, good(failed=frozenset({Quality.TOO_BRIGHT})), (Quality.TOO_BRIGHT,)),
        (None, good(eyes_open={"left": False, "right": True}), (Quality.EYES_CLOSED,)),
        (Pose.UP, good(pitch=15, eyes_open={"right": False}), (Quality.EYES_CLOSED,)),
        (
            Pose.LEFT,
            good(yaw=-30, eyes_open={"left": False, "right": False}),
            (Quality.EYES_CLOSED,),
        ),
        (None, good(yaw=15), (Quality.LOOK_STRAIGHT,)),
        (None, good(pitch=-12), (Quality.LOOK_STRAIGHT,)),
        (Pose.LEFT, good(yaw=-12), (Quality.TURN_MORE,)),
        (Pose.RIGHT, good(yaw=0), (Quality.TURN_MORE,)),
        (Pose.UP, good(pitch=8), (Quality.TURN_MORE,)),
        (Pose.DOWN, good(pitch=-20, yaw=25), (Quality.HOLD_STEADY,)),
        (Pose.LEFT, good(yaw=-30, pitch=20), (Quality.HOLD_STEADY,)),
        (Pose.LEFT, good(yaw=None), (Quality.NOT_READ,)),
        (None, good(pitch=None), (Quality.NOT_READ,)),
        # Several at once, each named once, in a steady order.
        (
            None,
            good(face_width=0.1, off_centre_x=0.4, failed=frozenset({Quality.BLURRY}), yaw=40),
            (Quality.FACE_TOO_SMALL, Quality.NOT_CENTRED, Quality.BLURRY, Quality.LOOK_STRAIGHT),
        ),
    ],
)
def test_a_frame_is_taken_again_for_each_reason(
    pose: Pose | None, reading: FrameReading, problems: tuple[Quality, ...]
) -> None:
    assert frame_problems(pose, reading) == problems


def test_a_turn_must_go_the_other_way_from_its_opposite() -> None:
    left = good(yaw=-30)
    assert frame_problems(Pose.RIGHT, good(yaw=-26), left) == (Quality.WRONG_WAY,)
    assert frame_problems(Pose.RIGHT, good(yaw=26), left) == ()
    up = good(pitch=16)
    assert frame_problems(Pose.DOWN, good(pitch=14), up) == (Quality.WRONG_WAY,)
    assert frame_problems(Pose.DOWN, good(pitch=-14), up) == ()
    # An opposite with no angle read does not block.
    assert frame_problems(Pose.DOWN, good(pitch=-14), FrameReading(faces=1)) == ()


def test_a_set_is_complete_and_apart_only_with_all_four_going_opposite_ways() -> None:
    kept = {
        Pose.LEFT: Kept(Pose.LEFT, -30, 2),
        Pose.RIGHT: Kept(Pose.RIGHT, 28, -1),
        Pose.UP: Kept(Pose.UP, 1, 15),
        Pose.DOWN: Kept(Pose.DOWN, -2, -13),
    }
    assert poses_missing(kept) == []
    assert poses_apart(kept)
    assert poses_missing({}) == [Pose.LEFT, Pose.RIGHT, Pose.UP, Pose.DOWN]
    assert poses_missing({Pose.UP: kept[Pose.UP]}) == [Pose.LEFT, Pose.RIGHT, Pose.DOWN]
    assert not poses_apart({**kept, Pose.RIGHT: Kept(Pose.RIGHT, -25, 0)})
    assert not poses_apart({**kept, Pose.DOWN: Kept(Pose.DOWN, 0, 13)})
    assert not poses_apart({k: v for k, v in kept.items() if k is not Pose.UP})


# Reading Regula's answer ----------------------------------------------------------------------


def detail(name: str, status: int = 1, value: float = 0.0) -> dict[str, object]:
    return {"groupId": 1, "name": name, "range": [0, 1], "status": status, "value": value}


def detected(*faces: dict[str, object]) -> dict[str, object]:
    return {"code": 0, "results": {"detections": list(faces), "scenario": ""}}


def face(*details: dict[str, object], roi: object = (380, 300, 450, 560)) -> dict[str, object]:
    return {
        "landmarks": [[1, 2]],
        "roi": list(roi) if isinstance(roi, tuple) else roi,
        "quality": {"details": list(details), "nonCompliant": []},
    }


SIZE = (detail("ImageWidth", value=1200), detail("ImageHeight", value=1600))


def test_a_clear_frame_is_read_with_its_angles_size_and_place() -> None:
    reading = reading_of(
        detected(
            face(
                *SIZE,
                detail("Yaw", 0, -27.4),
                detail("Pitch", 0, 3.2),
                detail("BlurLevel"),
                detail("TooDark"),
                detail("TooLight"),
                detail("FaceGlare"),
                detail("EyeLeftClosed"),
                detail("EyeRightClosed", status=2),
            )
        )
    )
    assert reading.faces == 1
    assert reading.yaw == -27.4 and reading.pitch == 3.2
    assert reading.face_width == pytest.approx(450 / 1200)
    assert reading.off_centre_x == pytest.approx((380 + 225) / 1200 - 0.5)
    assert reading.off_centre_y == pytest.approx((300 + 280) / 1600 - 0.5)
    assert reading.failed == frozenset()
    # An eye Regula could not tell about is left unknown.
    assert reading.eyes_open == {"left": True}
    assert frame_problems(Pose.LEFT, reading) == ()


def test_what_regula_found_wanting_is_named_and_what_it_could_not_tell_is_not() -> None:
    reading = reading_of(
        detected(
            face(
                *SIZE,
                detail("BlurLevel", 0),
                detail("TooDark", 0),
                detail("TooLight", 2),
                detail("EyeLeftClosed", 0),
                detail("EyeRightClosed", 0),
            )
        )
    )
    assert reading.failed == {Quality.BLURRY, Quality.TOO_DARK}
    assert reading.eyes_open == {"left": False, "right": False}


@pytest.mark.parametrize(
    "answer",
    [
        detected(face(roi=(1, 2, 3))),
        detected(face(roi="nonsense")),
        detected(face(detail("ImageWidth", value=0), detail("ImageHeight", value=10))),
        detected(face(detail("ImageWidth", value=True), detail("ImageHeight", value=10))),
        detected({"landmarks": [], "roi": [1, 2, 3, 4]}),
    ],
)
def test_a_face_regula_could_not_size_or_place_is_not_good(answer: dict[str, object]) -> None:
    reading = reading_of(answer)
    assert reading.faces == 1
    assert reading.face_width is None and reading.yaw is None
    problems = frame_problems(None, reading)
    assert Quality.FACE_TOO_SMALL in problems and Quality.NOT_READ in problems


def test_no_face_and_several_faces() -> None:
    assert reading_of(detected()).faces == 0
    assert reading_of(detected(face(), face(), "junk")).faces == 2  # type: ignore[arg-type]


@pytest.mark.parametrize("answer", [{}, {"results": {}}, {"results": {"detections": "x"}}, []])
def test_an_answer_that_is_not_a_detection_is_unavailable(answer: object) -> None:
    with pytest.raises(QualityUnavailable):
        reading_of(answer)


# Talking to Regula ----------------------------------------------------------------------------


def adapter(handler: object) -> RegulaFaceQuality:
    return RegulaFaceQuality(
        face_url=FACE + "/",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


async def test_it_asks_for_every_face_and_the_characteristics_it_reads() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=detected(face(*SIZE)))

    reading = await adapter(handler).read(b"\xff\xd8\xff frame")
    assert reading.faces == 1
    (request,) = sent
    assert str(request.url) == FACE + "/api/detect"
    body = json.loads(request.content)
    assert body["image"] == "/9j/IGZyYW1l"
    assert body["processParam"]["onlyCentralFace"] is False
    assert {c["name"] for c in body["processParam"]["quality"]["config"]} == {
        "BlurLevel",
        "TooDark",
        "TooLight",
        "FaceGlare",
        "EyeLeftClosed",
        "EyeRightClosed",
        "Yaw",
        "Pitch",
        "ImageWidth",
        "ImageHeight",
    }


async def test_an_image_regula_refuses_has_no_face() -> None:
    reading = await adapter(lambda _: httpx.Response(400, json={"msg": "bad image"})).read(b"x")
    assert reading == FrameReading(faces=0)


def timed_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        timed_out,
        lambda _: httpx.Response(500),
        lambda _: httpx.Response(403),
        lambda _: httpx.Response(200, content=b"not json"),
        lambda _: httpx.Response(200, json={"code": 0}),
    ],
)
async def test_anything_but_an_answer_is_unavailable(handler: object) -> None:
    with pytest.raises(QualityUnavailable) as raised:
        await adapter(handler).read(b"x")
    assert raised.value.__cause__ is None


def test_the_guided_selfie_comes_with_regula_only() -> None:
    key = "A" * 43
    regula = KycSettings(
        identity_data_key=key,
        identity_provider="regula",
        regula_document_reader_url="http://regula-docreader:8080",
        regula_face_url=FACE,
    )
    assert isinstance(regula.face_quality(), RegulaFaceQuality)
    assert (
        KycSettings(identity_data_key=key, identity_provider="development-stub").face_quality()
        is None
    )
    assert KycSettings().face_quality() is None
