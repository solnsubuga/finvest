"""The OpenAI and Gemini custodian statement readers, against fake clients."""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import SecretStr, ValidationError

from app.modules.reconciliation import statement_answer
from app.modules.reconciliation.custodian_settings import CustodianReaderSettings
from app.modules.reconciliation.domain.custodian import (
    AmountScale,
    ReaderRefused,
    ReaderUnavailable,
    StatementFile,
)
from app.modules.reconciliation.gemini_reader import GeminiStatementReader
from app.modules.reconciliation.openai_reader import OpenAIStatementReader
from tests.unit.test_custodian_checks import ANSWER

PDF = StatementFile("application/pdf", b"%PDF-1.7 statement")
CSV = StatementFile("text/csv", b"instrument,face_value\n91 day bill,1250000\n")
KEY = "-Ncheck0000000000001"


# OpenAI


@dataclass
class FakeResponses:
    answer: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.answer


def response(
    text: str | None, *, status: str = "completed", reason: str | None = None, refusal: bool = False
) -> Any:
    content = [SimpleNamespace(type="refusal", refusal="no")] if refusal else []
    if text is not None:
        content.append(SimpleNamespace(type="output_text", text=text))
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        output=[SimpleNamespace(type="message", content=content)],
        output_text=text or "",
        model="a-model",
    )


def openai_reader(responses: FakeResponses) -> OpenAIStatementReader:
    client = SimpleNamespace(responses=responses)
    return OpenAIStatementReader(api_key=SecretStr(""), model="a-model", client=client)  # type: ignore[arg-type]


def openai_status(code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.test/v1/responses")
    return openai.APIStatusError("x", response=httpx.Response(code, request=request), body=None)


async def test_the_openai_reader_copies_rows_with_strict_structured_output() -> None:
    responses = FakeResponses(answer=response(json.dumps(ANSWER)))
    reading = await openai_reader(responses).read(PDF, key=KEY)
    assert reading.amount_scale is AmountScale.THOUSAND
    assert reading.rows[0].face_value == "1,250,000"
    assert reading.reader == "openai:a-model"
    call = responses.calls[0]
    assert call["model"] == "a-model"
    assert call["instructions"] == statement_answer.SYSTEM
    assert call["text"]["format"]["schema"] == statement_answer.SCHEMA
    assert call["text"]["format"]["strict"] is True
    assert call["store"] is False
    assert call["safety_identifier"] == KEY
    document = call["input"][0]["content"][0]
    assert document["type"] == "input_file"
    assert document["file_data"].startswith("data:application/pdf;base64,")


async def test_the_openai_reader_sends_a_spreadsheet_as_text() -> None:
    responses = FakeResponses(answer=response(json.dumps(ANSWER)))
    await openai_reader(responses).read(CSV, key=KEY)
    document = responses.calls[0]["input"][0]["content"][0]
    assert document["type"] == "input_text"
    assert "Row 2: 91 day bill | 1250000" in document["text"]


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (response(None, status="incomplete", reason="max_output_tokens"), "statement_too_long"),
        (response(None, status="incomplete", reason="content_filter"), "reader_declined"),
        (response(None, refusal=True), "reader_declined"),
        (response(None), "reader_gave_no_answer"),
        (response("not json"), "reader_answer_invalid"),
    ],
)
async def test_the_openai_reader_refuses_an_answer_it_cannot_use(answer: Any, code: str) -> None:
    with pytest.raises(ReaderRefused) as refused:
        await openai_reader(FakeResponses(answer=answer)).read(PDF, key=KEY)
    assert refused.value.code == code


@pytest.mark.parametrize("code", [500, 503, 408, 409])
async def test_the_openai_reader_says_when_openai_is_unavailable(code: int) -> None:
    with pytest.raises(ReaderUnavailable):
        await openai_reader(FakeResponses(error=openai_status(code))).read(PDF, key=KEY)


async def test_the_openai_reader_says_when_it_cannot_connect() -> None:
    request = httpx.Request("POST", "https://api.openai.test/v1/responses")
    with pytest.raises(ReaderUnavailable):
        await openai_reader(FakeResponses(error=openai.APIConnectionError(request=request))).read(
            PDF, key=KEY
        )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "reader_not_authorised"),
        (413, "statement_too_large"),
        (400, "reader_rejected_request"),
    ],
)
async def test_the_openai_reader_names_a_refused_request(status: int, code: str) -> None:
    with pytest.raises(ReaderRefused) as refused:
        await openai_reader(FakeResponses(error=openai_status(status))).read(PDF, key=KEY)
    assert refused.value.code == code


# Gemini


@dataclass
class FakeModels:
    answer: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.answer


def generated(text: str | None, *, finish: str = "STOP", blocked: bool = False) -> Any:
    return SimpleNamespace(
        prompt_feedback=SimpleNamespace(block_reason="SAFETY") if blocked else None,
        candidates=[SimpleNamespace(finish_reason=types.FinishReason(finish))],
        text=text,
        model_version="a-model-001",
    )


def gemini_reader(models: FakeModels) -> GeminiStatementReader:
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return GeminiStatementReader(api_key=SecretStr(""), model="a-model", client=client)  # type: ignore[arg-type]


async def test_the_gemini_reader_copies_rows_held_to_the_schema() -> None:
    models = FakeModels(answer=generated(json.dumps(ANSWER)))
    reading = await gemini_reader(models).read(PDF, key=KEY)
    assert reading.rows[0].matures_on == "2026-12-03"
    assert reading.reader == "gemini:a-model-001"
    call = models.calls[0]
    assert call["model"] == "a-model"
    config: types.GenerateContentConfig = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == statement_answer.SCHEMA
    assert config.system_instruction == statement_answer.SYSTEM
    document = call["contents"].parts[0]
    assert document.inline_data.mime_type == "application/pdf"
    assert document.inline_data.data == PDF.data


async def test_the_gemini_reader_sends_a_spreadsheet_as_text() -> None:
    models = FakeModels(answer=generated(json.dumps(ANSWER)))
    await gemini_reader(models).read(CSV, key=KEY)
    assert "Row 2: 91 day bill | 1250000" in models.calls[0]["contents"].parts[0].text


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (generated(None, finish="MAX_TOKENS"), "statement_too_long"),
        (generated(None, finish="SAFETY"), "reader_declined"),
        (generated(None, blocked=True), "reader_declined"),
        (generated(None), "reader_gave_no_answer"),
        (generated("{}"), "reader_answer_invalid"),
    ],
)
async def test_the_gemini_reader_refuses_an_answer_it_cannot_use(answer: Any, code: str) -> None:
    with pytest.raises(ReaderRefused) as refused:
        await gemini_reader(FakeModels(answer=answer)).read(PDF, key=KEY)
    assert refused.value.code == code


@pytest.mark.parametrize(
    "error",
    [
        genai_errors.ServerError(503, {"error": {"message": "x", "status": "UNAVAILABLE"}}),
        genai_errors.ClientError(429, {"error": {"message": "x", "status": "RESOURCE_EXHAUSTED"}}),
        httpx.ConnectError("no route"),
    ],
)
async def test_the_gemini_reader_says_when_gemini_is_unavailable(error: Exception) -> None:
    with pytest.raises(ReaderUnavailable):
        await gemini_reader(FakeModels(error=error)).read(PDF, key=KEY)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, "reader_not_authorised"),
        (413, "statement_too_large"),
        (400, "reader_rejected_request"),
    ],
)
async def test_the_gemini_reader_names_a_refused_request(status: int, code: str) -> None:
    error = genai_errors.ClientError(status, {"error": {"message": "x", "status": "X"}})
    with pytest.raises(ReaderRefused) as refused:
        await gemini_reader(FakeModels(error=error)).read(PDF, key=KEY)
    assert refused.value.code == code


# Settings


@pytest.mark.parametrize(
    ("reader", "key_setting", "expected"),
    [
        ("openai", "openai_api_key", OpenAIStatementReader),
        ("gemini", "gemini_api_key", GeminiStatementReader),
    ],
)
def test_each_ai_reader_needs_its_own_key_and_a_model(
    reader: str, key_setting: str, expected: type
) -> None:
    def settings(**values: Any) -> CustodianReaderSettings:
        return CustodianReaderSettings.model_validate({"custodian_reader": reader, **values})

    with pytest.raises(ValidationError):
        settings(custodian_reader_model="a-model")
    with pytest.raises(ValidationError):
        settings(anthropic_api_key=SecretStr("k"), custodian_reader_model="a-model")
    with pytest.raises(ValidationError):
        settings(**{key_setting: SecretStr("k")})
    built = settings(
        custodian_reader_model="a-model", **{key_setting: SecretStr("sk-test")}
    ).reader()
    assert isinstance(built, expected)
    assert "sk-test" not in repr(built)
