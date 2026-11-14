"""The custodian statement reader on Google's Gemini API.

The same instructions and JSON schema as the Claude reader (statement_answer.py), with the
answer held to that schema as JSON. The answer is checked again here, and Finvest's own code
parses the amounts and compares the holdings. Nothing the model writes decides anything.

The statement's contents go to Google's Gemini API. Use a paid API key: Google's terms let it
use what is sent on its free tier to improve its products. Nothing is logged, and no error
quotes what was sent or what came back. See docs/reconciliation.md#custodian.
"""

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import SecretStr

from app.modules.reconciliation.custodian_reading import as_text
from app.modules.reconciliation.domain.custodian import (
    XLSX,
    ReaderRefused,
    ReaderUnavailable,
    Reading,
    StatementFile,
    check_reading,
)
from app.modules.reconciliation.statement_answer import INSTRUCTION, SCHEMA, SYSTEM, parse_answer

MAX_OUTPUT_TOKENS = 64_000
# Why Gemini can stop that means it would not read the file, rather than ran out of room.
_DECLINED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"}


class GeminiStatementReader:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        timeout: float = 600.0,
        client: genai.Client | None = None,
    ) -> None:
        if client is None and not api_key.get_secret_value():
            raise ValueError("The Gemini reader needs an API key")
        self._model = model
        self._client = client or genai.Client(
            api_key=api_key.get_secret_value(),
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),
        )

    def __repr__(self) -> str:
        return f"GeminiStatementReader(model={self._model})"

    async def read(self, file: StatementFile, *, key: str) -> Reading:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=types.Content(
                    role="user", parts=[_document(file), types.Part.from_text(text=INSTRUCTION)]
                ),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    response_mime_type="application/json",
                    response_json_schema=SCHEMA,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0,
                ),
            )
        except httpx.TransportError:
            # Never chain the cause: an error can quote the request.
            raise ReaderUnavailable() from None
        except genai_errors.APIError as error:
            if error.code >= 500 or error.code in {408, 409, 429}:
                raise ReaderUnavailable() from None
            if error.code in {401, 403}:
                raise ReaderRefused("reader_not_authorised") from None
            if error.code == 413:
                raise ReaderRefused("statement_too_large") from None
            raise ReaderRefused("reader_rejected_request") from None
        if response.prompt_feedback is not None and response.prompt_feedback.block_reason:
            raise ReaderRefused("reader_declined")
        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason.value if candidate and candidate.finish_reason else None
        if finish == "MAX_TOKENS":
            raise ReaderRefused("statement_too_long")
        if finish in _DECLINED:
            raise ReaderRefused("reader_declined")
        text = response.text
        if not text:
            raise ReaderRefused("reader_gave_no_answer")
        model = response.model_version or self._model
        return check_reading(parse_answer(text, reader=f"gemini:{model}"))


def _document(file: StatementFile) -> types.Part:
    if file.content_type in {"application/pdf", "image/png", "image/jpeg"}:
        return types.Part.from_bytes(data=file.data, mime_type=file.content_type)
    if file.content_type in {"text/csv", XLSX}:
        return types.Part.from_text(text=f"Custodian statement:\n\n{as_text(file)}")
    raise ReaderRefused("statement_type_not_supported")
