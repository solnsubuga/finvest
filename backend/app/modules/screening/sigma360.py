"""The Sigma360 screening adapter. See docs/screening.md#sigma360.

Built from Sigma360's published API description (https://docs.sigma360.com/openapi.json,
v2.0.1): one synchronous search per person, `POST /screening/full-single`. Which lists are
searched and how close a name must be to count is the "filter set" chosen in Sigma360's own
console, passed by its number with every search.

Every row Sigma360 returns is a possible match for a person to look at. Nothing here decides
that a match is real. Nothing is logged, and no error quotes what Sigma360 sent back, because
its answers name people.
"""

from collections.abc import Iterable, Mapping, Sequence

import httpx
from pydantic import SecretStr

from app.modules.screening.domain.provider import (
    Match,
    MatchCategory,
    ProviderRefused,
    ProviderUnavailable,
    ScreeningResult,
    ScreeningSubject,
)

BASE_URL = "https://api.sigma360.com/external/v2"
# A row with no strength, or one outside 0 to 1, is shown as this: never ranked as a weak match.
UNKNOWN_STRENGTH = 100
_MAX_SOURCES = 20
# Most severe first: a row is shown under the most severe kind of list it was found on.
_SEVERITY = (
    MatchCategory.SANCTIONS,
    MatchCategory.PEP,
    MatchCategory.ADVERSE_MEDIA,
    MatchCategory.OTHER,
)


class Sigma360Provider:
    name = "sigma360"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        filter_set_id: int,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.get_secret_value():
            raise ValueError("Sigma360 needs an API key")
        if type(filter_set_id) is not int or filter_set_id < 0:
            raise ValueError("A Sigma360 filter set is a number")
        if not base_url.startswith("https://"):
            # The key and the client's name travel in every request.
            raise ValueError("Sigma360 is only reached over https")
        self._api_key = api_key
        self._filter_set_id = filter_set_id
        self._url = base_url.rstrip("/") + "/screening/full-single"
        self._timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return f"Sigma360Provider(filter_set_id={self._filter_set_id})"

    def body(self, subject: ScreeningSubject, key: str) -> dict[str, object]:
        body: dict[str, object] = {
            "entityName": subject.full_name,
            "entityType": "person",
            "filterSetID": self._filter_set_id,
            "createAlerts": False,
            # Our reference for the search, so staff can find it in Sigma360's console. It
            # carries no personal data.
            "customerKey": key,
        }
        if subject.date_of_birth is not None:
            body["dob"] = subject.date_of_birth.isoformat()
        if subject.document_country is not None:
            body["countries"] = [subject.document_country.upper()]
        return body

    async def screen(self, subject: ScreeningSubject, *, key: str) -> ScreeningResult:
        headers = {
            # The API description names the header and not a scheme, so the key is sent as it
            # is. A key that needs a scheme is set with it, as "Bearer <key>".
            "Authorization": self._api_key.get_secret_value(),
            "Accept": "application/json",
            "User-Agent": "finvest",
        }
        body = self.body(subject, key)
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._url, json=body, headers=headers, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._url, json=body, headers=headers)
        except httpx.HTTPError:
            # A search only reads, so a search that may have arrived is simply asked again.
            raise ProviderUnavailable("Sigma360 is unreachable") from None
        status = response.status_code
        if status == 200:
            return _result(response, key)
        if status == 400:
            raise ProviderRefused("sigma360_refused")
        # 401 and 403 (a wrong or expired key), 429, 5xx: nothing was searched. The job asks
        # again; staff see the screen as unavailable if it never succeeds.
        raise ProviderUnavailable(f"Sigma360 answered {status}")


def _result(response: httpx.Response, key: str) -> ScreeningResult:
    try:
        body = response.json()
    except ValueError:
        raise ProviderUnavailable("Sigma360 answered with something that is not JSON") from None
    if not isinstance(body, dict):
        raise ProviderUnavailable("Sigma360 answered with something unexpected")
    rows = body.get("resultRows")
    if rows is None and body.get("resultCount") == 0:
        rows = []
    if not isinstance(rows, list):
        # Without the rows we cannot say the client is clear.
        raise ProviderUnavailable("Sigma360's answer has no result rows")
    matches = tuple(match for row in rows if (match := _match(row)) is not None)
    if len(matches) != len(rows):
        # A row we cannot read is never dropped silently: it would look like a clear result.
        raise ProviderUnavailable("Sigma360's answer has a row that cannot be read")
    found = body.get("alertCreatedURN")
    return ScreeningResult(
        matches=matches,
        reference=found if isinstance(found, str) and 0 < len(found) <= 200 else f"sigma360:{key}",
    )


def _match(row: object) -> Match | None:
    if not isinstance(row, dict):
        return None
    entity = row.get("entity")
    if not isinstance(entity, dict):
        return None
    entity_id = entity.get("sigmaID")
    if not isinstance(entity_id, str | int) or isinstance(entity_id, bool) or entity_id == "":
        return None
    name = _text(row.get("matchName")) or _text(entity.get("entityName")) or "Unnamed entity"
    indicators = [i for i in _list(entity.get("indicators")) if isinstance(i, dict)]
    return Match(
        entity_id=str(entity_id)[:200],
        name=name[:300],
        category=_category(entity, indicators),
        strength=_strength(row.get("matchStrength")),
        sources=_sources(row, indicators),
    )


def _category(
    entity: Mapping[str, object], indicators: Sequence[Mapping[str, object]]
) -> MatchCategory:
    found = {_kind(i.get("category"), i.get("subcategory")) for i in indicators}
    facet = entity.get("facet")
    person = facet.get("person") if isinstance(facet, dict) else None
    if isinstance(person, dict) and _list(person.get("politicalPositions")):
        found.add(MatchCategory.PEP)
    if _list(entity.get("news")):
        found.add(MatchCategory.ADVERSE_MEDIA)
    return next((category for category in _SEVERITY if category in found), MatchCategory.OTHER)


def _kind(category: object, subcategory: object) -> MatchCategory:
    words = f"{_text(category) or ''} {_text(subcategory) or ''}".lower()
    if "sanction" in words:
        return MatchCategory.SANCTIONS
    if "pep" in words or "political" in words:
        return MatchCategory.PEP
    if "media" in words or "news" in words:
        return MatchCategory.ADVERSE_MEDIA
    return MatchCategory.OTHER


def _strength(value: object) -> int:
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1:
        return round(value * 100)
    return UNKNOWN_STRENGTH


def _sources(
    row: Mapping[str, object], indicators: Iterable[Mapping[str, object]]
) -> tuple[str, ...]:
    names: list[str] = []
    for source in _list(row.get("sources")):
        if isinstance(source, dict) and (name := _text(source.get("name"))):
            names.append(name)
    for indicator in indicators:
        for source in _list(indicator.get("sources")):
            if isinstance(source, dict) and (name := _text(source.get("name"))):
                names.append(name)
    return tuple(dict.fromkeys(name[:200] for name in names))[:_MAX_SOURCES]


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
