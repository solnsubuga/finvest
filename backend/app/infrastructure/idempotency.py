"""Idempotent commands: one saved outcome per actor, operation and key.

Every command that moves money takes an `Idempotency-Key`. Inside the command's own transaction,
`run_once` takes a transaction-scoped advisory lock on the key, so two requests with one key run
one at a time. The first runs the command and saves its outcome in the same transaction as the
command's changes. Every later one gets the saved outcome and the command does not run again.

The request digest covers the operation and the whole request. A key reused for a different
request is refused, never answered with another command's outcome. Only successful outcomes are
saved, so a refused command can be corrected and retried with the same key.
"""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.actors import Actor
from app.shared.errors import ApplicationError

KEY_PATTERN = r"[A-Za-z0-9._:-]{8,128}"
_KEY = re.compile(KEY_PATTERN)
_OPERATION = re.compile(r"[a-z][a-z0-9_.]{0,99}")


@dataclass(frozen=True, slots=True)
class Receipt:
    status_code: int
    response: dict[str, object]


@dataclass(frozen=True, slots=True)
class Outcome:
    receipt: Receipt
    replayed: bool


def parse_key(value: str | None) -> str:
    """Parse an `Idempotency-Key` header. Missing and malformed keys are refused."""
    if value is None or not value.strip():
        raise ApplicationError("idempotency_key_required")
    if _KEY.fullmatch(value) is None:
        raise ApplicationError("validation_failed")
    return value


def request_digest(operation: str, request: Mapping[str, object]) -> str:
    """A stable digest of the whole command. Integers only: a float would not be stable."""
    canonical = json.dumps(
        {"operation": operation, "request": request},
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


async def run_once(
    session: AsyncSession,
    *,
    actor: Actor,
    operation: str,
    key: str,
    request: Mapping[str, object],
    now: datetime,
    command: Callable[[], Awaitable[Receipt]],
) -> Outcome:
    """Run `command` unless this actor already ran this exact request under this key.

    `session` must be the actor's open transaction (`Database.actor_transaction(actor)`), and
    `command` must do its work in that same session, so the outcome and the changes commit
    together. If `command` raises, nothing is saved and the key can be used again.
    """
    if not session.in_transaction():
        raise RuntimeError("An idempotent command runs inside its transaction")
    if actor.kind == "system" or actor.id is None:
        raise ValueError("Idempotency keys belong to a client or a staff member")
    if _OPERATION.fullmatch(operation) is None or _KEY.fullmatch(key) is None:
        raise ValueError("Invalid operation or idempotency key")
    digest = request_digest(operation, request)
    scope = {"kind": actor.kind, "actor": actor.id, "operation": operation, "key": key}
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))"),
        {"lock": f"finvest.idempotency:{actor.kind}:{actor.id}:{operation}:{key}"},
    )
    saved = (
        await session.execute(
            text(
                "SELECT request_digest, status_code, response FROM operation_receipts "
                "WHERE actor_kind = :kind AND actor_id = :actor AND operation = :operation "
                "AND idempotency_key = :key"
            ),
            scope,
        )
    ).one_or_none()
    if saved is not None:
        if saved.request_digest != digest:
            raise ApplicationError("idempotency_key_reused")
        response = saved.response
        if isinstance(response, str):
            response = json.loads(response)
        return Outcome(Receipt(saved.status_code, response), replayed=True)
    receipt = await command()
    await session.execute(
        text(
            "INSERT INTO operation_receipts (actor_kind, actor_id, operation, idempotency_key, "
            "request_digest, status_code, response, created_at) VALUES (:kind, :actor, "
            ":operation, :key, :digest, :status, CAST(:response AS jsonb), :now)"
        ),
        {
            **scope,
            "digest": digest,
            "status": receipt.status_code,
            "response": json.dumps(receipt.response, allow_nan=False),
            "now": now,
        },
    )
    return Outcome(receipt, replayed=False)
