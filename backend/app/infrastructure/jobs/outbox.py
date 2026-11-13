"""Transactional outbox: an event and its consumer jobs commit or roll back with the change."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs.queue import JobRequest, enqueue, to_json
from app.shared.push_ids import push_id


@dataclass(frozen=True)
class Consumer:
    """A durable handler for an event. Each consumer gets its own job and retry budget."""

    kind: str
    queue: str = "default"
    max_attempts: int = 8
    max_age: timedelta = timedelta(days=1)


async def publish(
    session: AsyncSession,
    *,
    topic: str,
    idempotency_key: str,
    payload: Mapping[str, object],
    consumers: Sequence[Consumer],
    now: datetime,
) -> str | None:
    """Record one event and a job per consumer in the caller's open transaction.

    Returns the new event's ID. Publishing the same topic and key again returns None and creates
    nothing, so "payment confirmed" reaches each consumer once however often it is reported.
    The payload carries identifiers and codes, never personal data.
    """
    if not session.in_transaction():
        raise RuntimeError("Events are published inside the business transaction")
    kinds = [consumer.kind for consumer in consumers]
    if len(set(kinds)) != len(kinds):
        raise ValueError("An event has one job per consumer kind")
    event_id = push_id()
    inserted: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
        text(
            "INSERT INTO outbox_events (id, topic, idempotency_key, payload, created_at) "
            "VALUES (:id, :topic, :key, CAST(:payload AS jsonb), :now) "
            "ON CONFLICT (topic, idempotency_key) DO NOTHING"
        ),
        {
            "id": event_id,
            "topic": topic,
            "key": idempotency_key,
            "payload": to_json(payload),
            "now": now,
        },
    )
    if inserted.rowcount != 1:
        return None
    job_ids: list[str] = []
    for consumer in consumers:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=consumer.kind,
                queue=consumer.queue,
                # Consumers use the event key, so receivers can deduplicate across retries.
                idempotency_key=f"{topic}:{idempotency_key}",
                payload={"event_id": event_id, "topic": topic, "data": dict(payload)},
                max_attempts=consumer.max_attempts,
                max_age=consumer.max_age,
            ),
            now=now,
            event_id=event_id,
        )
        if job_id is not None:
            job_ids.append(job_id)
    # Staged, not yet committed: a rolled-back transaction leaves this line with no job run.
    structlog.get_logger().info(
        "outbox_event_staged", event_id=event_id, topic=topic, job_ids=job_ids
    )
    return event_id
