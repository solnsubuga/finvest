"""The Sigma360 adapter against a fake Sigma360: a match, no match, refused, timed out, unsure."""

import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.modules.screening.domain.provider import (
    Match,
    MatchCategory,
    ProviderRefused,
    ProviderUnavailable,
    ScreeningSubject,
)
from app.modules.screening.settings import ScreeningSettings
from app.modules.screening.sigma360 import UNKNOWN_STRENGTH, Sigma360Provider

SUBJECT = ScreeningSubject(
    surname="NAKATO", given_names="Amara", date_of_birth=date(1994, 6, 22), document_country="ug"
)
KEY = "run-1"
Handler = Callable[[httpx.Request], httpx.Response]


def sanctioned_row() -> dict[str, object]:
    return {
        "matchName": "Amara Nakato",
        "matchStrength": 0.934,
        "sources": [{"key": "ofac", "name": "OFAC SDN"}],
        "entity": {
            "sigmaID": "SIG-1",
            "entityName": "NAKATO, Amara",
            "riskSummary": {"score": 90},
            "indicators": [
                {
                    "category": "Sanctions",
                    "subcategory": "sanctions_un",
                    "code": "SAN001",
                    "score": 90,
                    "sources": [{"name": "UN Security Council", "url": "https://un.org"}],
                }
            ],
            "facet": {"person": {"politicalPositions": [{"country": "UG"}]}},
            "news": [],
        },
    }


def answering(body: object, status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


def provider(handler: Handler) -> Sigma360Provider:
    return Sigma360Provider(
        api_key=SecretStr("s360-key"),
        filter_set_id=7,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_a_match_is_shown_under_its_most_severe_list() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200, json={"resultCount": 1, "resultRows": [sanctioned_row()], "searchDate": "x"}
        )

    result = await provider(handler).screen(SUBJECT, key=KEY)
    assert result.matches == (
        Match(
            entity_id="SIG-1",
            name="Amara Nakato",
            category=MatchCategory.SANCTIONS,
            strength=93,
            sources=("OFAC SDN", "UN Security Council"),
        ),
    )
    assert result.reference == f"sigma360:{KEY}"
    (request,) = sent
    assert request.url == "https://api.sigma360.com/external/v2/screening/full-single"
    assert request.headers["Authorization"] == "s360-key"
    assert json.loads(request.content) == {
        "entityName": "Amara NAKATO",
        "entityType": "person",
        "filterSetID": 7,
        "createAlerts": False,
        "customerKey": KEY,
        "dob": "1994-06-22",
        "countries": ["UG"],
    }


async def test_no_rows_is_clear_and_unknown_details_are_left_out() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"resultCount": 0, "resultRows": [], "searchDate": "x"})

    subject = ScreeningSubject("NAKATO", "Amara", None, None)
    result = await provider(handler).screen(subject, key=KEY)
    assert result.matches == ()
    body = json.loads(sent[0].content)
    assert "dob" not in body and "countries" not in body


@pytest.mark.parametrize(
    ("change", "category"),
    [
        ({"indicators": [{"category": "Politically Exposed Person"}]}, MatchCategory.PEP),
        ({"indicators": [], "news": [{"title": "x"}]}, MatchCategory.ADVERSE_MEDIA),
        ({"indicators": [{"category": "Registry"}], "facet": {}}, MatchCategory.OTHER),
    ],
)
async def test_each_kind_of_list(change: dict[str, object], category: MatchCategory) -> None:
    row = sanctioned_row()
    entity = row["entity"]
    assert isinstance(entity, dict)
    entity.update({"facet": {}, "news": []} | change)
    result = await provider(answering({"resultCount": 1, "resultRows": [row]})).screen(
        SUBJECT, key=KEY
    )
    assert result.matches[0].category is category


@pytest.mark.parametrize("strength", [None, 1.7, -0.1, True, "0.9"])
async def test_a_strength_it_does_not_understand_is_never_weak(strength: object) -> None:
    row = sanctioned_row() | {"matchStrength": strength}
    result = await provider(answering({"resultCount": 1, "resultRows": [row]})).screen(
        SUBJECT, key=KEY
    )
    assert result.matches[0].strength == UNKNOWN_STRENGTH


async def test_an_answer_it_cannot_read_is_never_taken_as_clear() -> None:
    body: object
    for body in ({"resultCount": 1}, {"resultRows": [{"entity": {}}]}, ["x"]):
        with pytest.raises(ProviderUnavailable):
            await provider(answering(body)).screen(SUBJECT, key=KEY)

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    with pytest.raises(ProviderUnavailable):
        await provider(not_json).screen(SUBJECT, key=KEY)


async def test_a_refused_search_is_refused() -> None:
    with pytest.raises(ProviderRefused) as refused:
        await provider(answering("bad name", 400)).screen(SUBJECT, key=KEY)
    assert refused.value.code == "sigma360_refused"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_a_search_that_did_not_run_is_asked_again(status: int) -> None:
    with pytest.raises(ProviderUnavailable):
        await provider(answering("no", status)).screen(SUBJECT, key=KEY)


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
async def test_a_timeout_or_a_lost_answer_is_asked_again(error: type[httpx.TransportError]) -> None:
    # A search only reads lists, so one that may have arrived is safe to ask again.
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("lost", request=request)

    with pytest.raises(ProviderUnavailable) as raised:
        await provider(handler).screen(SUBJECT, key=KEY)
    assert raised.value.__cause__ is None


def test_sigma360_needs_its_key_and_filter_set_and_https() -> None:
    with pytest.raises(ValidationError, match="FINVEST_SIGMA360_API_KEY"):
        ScreeningSettings(screening_provider="sigma360", sigma360_filter_set_id=7)
    with pytest.raises(ValidationError, match="FINVEST_SIGMA360_FILTER_SET_ID"):
        ScreeningSettings(screening_provider="sigma360", sigma360_api_key="k")
    with pytest.raises(ValidationError, match="https"):
        ScreeningSettings(
            screening_provider="sigma360",
            sigma360_api_key="k",
            sigma360_filter_set_id=7,
            sigma360_base_url="http://api.sigma360.com/external/v2",
        )
    settings = ScreeningSettings(
        screening_provider="sigma360", sigma360_api_key="s360-key", sigma360_filter_set_id=7
    )
    built = settings.provider()
    assert isinstance(built, Sigma360Provider)
    assert "s360-key" not in repr(settings) and "s360-key" not in repr(built)
    assert ScreeningSettings().provider() is None
