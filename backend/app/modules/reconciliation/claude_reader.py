"""The custodian statement reader on the Claude API. See docs/reconciliation.md#custodian.

The model is asked to copy, not to work anything out: each holding's instrument, its face value
exactly as printed, its maturity date and where it is on the statement. The answer is held to a
fixed JSON schema (structured outputs), and checked again here. Finvest's own code then parses
the amounts and compares the holdings. Nothing the model writes decides anything.

The statement's contents go to Anthropic's API. Nothing is logged, and no error quotes what was
sent or what came back.
"""

import base64
from typing import Any, cast

import anthropic
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

# A statement of a few hundred holdings fits well inside this. Streaming keeps a long answer
# from timing out.
MAX_TOKENS = 64_000
# Re-runs the request on another model if the first declines it, instead of failing the read.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeStatementReader:
    name = "claude"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        timeout: float = 600.0,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        if client is None and not api_key.get_secret_value():
            raise ValueError("The Claude reader needs an API key")
        self._model = model
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key.get_secret_value(), timeout=timeout, max_retries=2
        )

    def __repr__(self) -> str:
        return f"ClaudeStatementReader(model={self._model})"

    async def read(self, file: StatementFile, *, key: str) -> Reading:
        content = [_document(file), {"type": "text", "text": INSTRUCTION}]
        try:
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=MAX_TOKENS,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": SCHEMA},
                },
                system=SYSTEM,
                messages=cast(Any, [{"role": "user", "content": content}]),
                metadata={"user_id": key},
            ) as stream:
                message = await stream.get_final_message()
        except (anthropic.APIConnectionError, anthropic.RateLimitError):
            # Never chain the cause: an error can quote the request.
            raise ReaderUnavailable() from None
        except anthropic.APIStatusError as error:
            if error.status_code >= 500 or error.status_code in {408, 409, 529}:
                raise ReaderUnavailable() from None
            if error.status_code in {401, 403}:
                raise ReaderRefused("reader_not_authorised") from None
            if error.status_code == 413:
                raise ReaderRefused("statement_too_large") from None
            raise ReaderRefused("reader_rejected_request") from None
        if message.stop_reason == "refusal":
            raise ReaderRefused("reader_declined")
        if message.stop_reason == "max_tokens":
            raise ReaderRefused("statement_too_long")
        text = next((block.text for block in message.content if block.type == "text"), None)
        if text is None:
            raise ReaderRefused("reader_gave_no_answer")
        return check_reading(parse_answer(text, reader=f"claude:{message.model}"))


def _document(file: StatementFile) -> dict[str, Any]:
    if file.content_type == "application/pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(file.data).decode("ascii"),
            },
        }
    if file.content_type in {"image/png", "image/jpeg"}:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": file.content_type,
                "data": base64.standard_b64encode(file.data).decode("ascii"),
            },
        }
    if file.content_type in {"text/csv", XLSX}:
        return {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": as_text(file)},
            "title": "Custodian statement",
        }
    raise ReaderRefused("statement_type_not_supported")
