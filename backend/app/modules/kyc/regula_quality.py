"""Regula's face quality check, for the guided selfie. See docs/identity-checks.md#guided-selfie.

`POST /api/detect` on Finvest's own Regula Face service finds the faces in one frame and
assesses the quality characteristics asked for. Each is compliant (1), not (0) or could not be
told (2), against Regula's own recommended range, and carries its value. The angles and the
face's size and place are read from the values, so the rules in `domain/poses.py` decide them,
not Regula's passport-photo ranges.

It changes nothing at Regula, so it is asked as often as needed. Nothing here is logged, and no
error quotes what Regula sent back.
"""

import base64
from collections.abc import Mapping, Sequence

import httpx

from app.modules.kyc.domain.poses import FrameReading, Quality, QualityUnavailable

_COMPLIANT, _NOT_COMPLIANT = 1, 0
# Characteristics whose verdict is Regula's, and what each one failing means.
_JUDGED = {
    "BlurLevel": Quality.BLURRY,
    "TooDark": Quality.TOO_DARK,
    "TooLight": Quality.TOO_BRIGHT,
    "FaceGlare": Quality.GLARE,
}
_EYES = {"EyeLeftClosed": "left", "EyeRightClosed": "right"}
# Characteristics whose value is read here.
_MEASURED = ("Yaw", "Pitch", "ImageWidth", "ImageHeight")


class RegulaFaceQuality:
    name = "regula"

    def __init__(
        self, *, face_url: str, timeout: float = 20.0, client: httpx.AsyncClient | None = None
    ) -> None:
        self._url = face_url.rstrip("/") + "/api/detect"
        self._timeout = timeout
        self._client = client

    async def read(self, image: bytes) -> FrameReading:
        body = {
            "image": base64.b64encode(image).decode("ascii"),
            "processParam": {
                # Every face, so a second person in the frame is seen.
                "onlyCentralFace": False,
                "quality": {"config": [{"name": name} for name in (*_JUDGED, *_EYES, *_MEASURED)]},
            },
        }
        try:
            if self._client is not None:
                response = await self._client.post(self._url, json=body, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._url, json=body)
        except httpx.HTTPError:
            raise QualityUnavailable from None
        if response.status_code in (400, 422):
            # Regula could not use the image at all: as good as no face.
            return FrameReading(faces=0)
        if response.status_code != 200:
            raise QualityUnavailable
        try:
            answer = response.json()
        except ValueError:
            raise QualityUnavailable from None
        return reading_of(answer)


def reading_of(answer: object) -> FrameReading:
    """A frame reading from Regula's answer. Anything missing is left unknown, never good."""
    results = answer.get("results") if isinstance(answer, Mapping) else None
    detections = results.get("detections") if isinstance(results, Mapping) else None
    if not isinstance(detections, Sequence) or isinstance(detections, str):
        raise QualityUnavailable
    faces = [d for d in detections if isinstance(d, Mapping)]
    if len(faces) != 1:
        return FrameReading(faces=len(faces))
    face = faces[0]
    quality = face.get("quality")
    details = quality.get("details") if isinstance(quality, Mapping) else None
    statuses: dict[str, int] = {}
    values: dict[str, float] = {}
    for detail in details if isinstance(details, Sequence) else ():
        if not isinstance(detail, Mapping) or not isinstance(detail.get("name"), str):
            continue
        name = detail["name"]
        status, value = detail.get("status"), detail.get("value")
        if isinstance(status, int) and not isinstance(status, bool):
            statuses[name] = status
        if isinstance(value, int | float) and not isinstance(value, bool):
            values[name] = float(value)
    # Only a characteristic Regula found wanting asks for a retake. One it could not tell, or
    # does not assess, does not: the frame is a guide for people and the match, and the
    # liveness and the face match are what prove who is there.
    failed = {q for name, q in _JUDGED.items() if statuses.get(name) == _NOT_COMPLIANT}
    eyes = {
        eye: statuses[name] == _COMPLIANT
        for name, eye in _EYES.items()
        if statuses.get(name) in (_COMPLIANT, _NOT_COMPLIANT)
    }
    width, height = values.get("ImageWidth"), values.get("ImageHeight")
    box = face.get("roi")
    face_width = off_x = off_y = None
    if (
        width
        and height
        and isinstance(box, Sequence)
        and len(box) == 4
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in box)
    ):
        x, y, w, h = (float(v) for v in box)
        face_width = w / width
        off_x = (x + w / 2) / width - 0.5
        off_y = (y + h / 2) / height - 0.5
    return FrameReading(
        faces=1,
        yaw=values.get("Yaw"),
        pitch=values.get("Pitch"),
        face_width=face_width,
        off_centre_x=off_x,
        off_centre_y=off_y,
        failed=frozenset(failed),
        eyes_open=eyes,
    )
