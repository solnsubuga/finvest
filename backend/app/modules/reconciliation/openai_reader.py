"""The custodian statement reader on OpenAI's API (the models behind ChatGPT).

The same instructions and JSON schema as the Claude reader (statement_answer.py), sent through
the Responses API with strict structured outputs. The answer is checked again here, and
Finvest's own code parses the amounts and compares the holdings. Nothing the model writes
decides anything.

The statement's contents go to OpenAI's API, with `store` off so OpenAI does not keep the
response for later retrieval. Nothing is logged, and no error quotes what was sent or what came
back. See docs/reconciliation.md#custodian.
"""

import base64
from typing import Any, cast

import openai
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


class OpenAIStatementReader:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        timeout: float = 600.0,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        if client is None and not api_key.get_secret_value():
            raise ValueError("The OpenAI reader needs an API key")
        self._model = model
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key.get_secret_value(), timeout=timeout, max_retries=2
        )

    def __repr__(self) -> str:
        return f"OpenAIStatementReader(model={self._model})"

    async def read(self, file: StatementFile, *, key: str) -> Reading:
        content = [_document(file), {"type": "input_text", "text": INSTRUCTION}]
        try:
            response = await self._client.responses.create(
                model=self._model,
                instructions=SYSTEM,
                input=cast(Any, [{"role": "user", "content": content}]),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "custodian_statement",
                        "schema": SCHEMA,
                        "strict": True,
                    }
                },
                reasoning={"effort": "high"},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
                safety_identifier=key,
            )
        except (openai.APIConnectionError, openai.RateLimitError):
            # Never chain the cause: an error can quote the request.
            raise ReaderUnavailable() from None
        except openai.APIStatusError as error:
            if error.status_code >= 500 or error.status_code in {408, 409}:
                raise ReaderUnavailable() from None
            if error.status_code in {401, 403}:
                raise ReaderRefused("reader_not_authorised") from None
            if error.status_code == 413:
                raise ReaderRefused("statement_too_large") from None
            raise ReaderRefused("reader_rejected_request") from None
        if response.status == "incomplete":
            reason = response.incomplete_details.reason if response.incomplete_details else None
            if reason == "max_output_tokens":
                raise ReaderRefused("statement_too_long")
            raise ReaderRefused("reader_declined")
        for output in response.output:
            if output.type == "message" and any(c.type == "refusal" for c in output.content):
                raise ReaderRefused("reader_declined")
        text = response.output_text
        if not text:
            raise ReaderRefused("reader_gave_no_answer")
        return check_reading(parse_answer(text, reader=f"openai:{response.model}"))


def _document(file: StatementFile) -> dict[str, Any]:
    encoded = base64.standard_b64encode(file.data).decode("ascii")
    if file.content_type == "application/pdf":
        return {
            "type": "input_file",
            "filename": "statement.pdf",
            "file_data": f"data:application/pdf;base64,{encoded}",
        }
    if file.content_type in {"image/png", "image/jpeg"}:
        return {
            "type": "input_image",
            "image_url": f"data:{file.content_type};base64,{encoded}",
            "detail": "high",
        }
    if file.content_type in {"text/csv", XLSX}:
        return {"type": "input_text", "text": f"Custodian statement:\n\n{as_text(file)}"}
    raise ReaderRefused("statement_type_not_supported")
