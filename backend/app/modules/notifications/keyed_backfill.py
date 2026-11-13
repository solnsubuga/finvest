"""Fill the keyed hash of email rows saved before the key existed (migration 0093).

The second step of the changeover in docs/notifications.md#how-addresses-are-hashed. Every row
keeps its plain hash, and the keyed hash is worked out from it, so no address is needed. Only
the worker and the API hold the key; the migrator never does.

A run fills one page of each table in one transaction, and queues the next page in the same
transaction while anything is left. A crash repeats one page at most, and a page filled twice
changes nothing: each update fills only an empty column, and migration 0094 refuses a change to
one already set. The scheduler starts a run every hour, which finds nothing to do once all are
filled. Reads move to the keyed columns in a later change, once a run reports nothing left.
"""

from datetime import timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    PermanentJobError,
    enqueue,
)
from app.modules.notifications.domain.email_events import keyed_hash, keyed_hashes
from app.shared.clock import Clock

KEY_HASHES = "notifications.key_email_hashes"
QUEUE = "default"
PAGE = 500
MAX_AGE = timedelta(hours=1)

logger = structlog.get_logger()

# Rows are taken with SKIP LOCKED, so two runs at once share the work instead of waiting.
_EVENTS = text(
    "SELECT svix_id, recipients FROM notification_email_events "
    "WHERE recipients_keyed IS NULL ORDER BY svix_id LIMIT :page FOR UPDATE SKIP LOCKED"
)
_FILL_EVENT = text(
    "UPDATE notification_email_events SET recipients_keyed = CAST(:keyed AS text[]) "
    "WHERE svix_id = :id AND recipients_keyed IS NULL"
)
_SUPPRESSIONS = text(
    "SELECT address_hash FROM notification_email_suppressions "
    "WHERE address_key IS NULL ORDER BY address_hash LIMIT :page FOR UPDATE SKIP LOCKED"
)
_FILL_SUPPRESSION = text(
    "UPDATE notification_email_suppressions SET address_key = :keyed "
    "WHERE address_hash = :id AND address_key IS NULL"
)
_LIFTS = text(
    "SELECT id, address_hash FROM notification_email_suppression_lifts "
    "WHERE address_key IS NULL ORDER BY id LIMIT :page FOR UPDATE SKIP LOCKED"
)
_FILL_LIFT = text(
    "UPDATE notification_email_suppression_lifts SET address_key = :keyed "
    "WHERE id = :id AND address_key IS NULL"
)
_LEFT = text(
    "SELECT (SELECT count(*) FROM notification_email_events WHERE recipients_keyed IS NULL) "
    "+ (SELECT count(*) FROM notification_email_suppressions WHERE address_key IS NULL) "
    "+ (SELECT count(*) FROM notification_email_suppression_lifts WHERE address_key IS NULL)"
)


class KeyedBackfillJobs:
    def __init__(self, database: Database, clock: Clock, key: bytes | None) -> None:
        self._database = database
        self._clock = clock
        # The email hash key (`EmailHashSettings`). Never logged.
        self._key = key

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {KEY_HASHES: self.key_hashes}

    async def key_hashes(self, job: ClaimedJob, context: JobContext) -> None:
        key = self._key
        if key is None:
            raise PermanentJobError(
                "FINVEST_EMAIL_HASH_KEY is not set", code="email_hash_key_missing"
            )
        now = self._clock.now()

        async def fill(session: AsyncSession) -> dict[str, object]:
            filled = await fill_page(session, key)
            left = int((await session.execute(_LEFT)).scalar_one())
            # Rows another run holds count as left; that run fills them.
            if filled and left:
                await enqueue(
                    session,
                    JobRequest(
                        kind=KEY_HASHES,
                        idempotency_key=f"after:{job.id}",
                        payload={},
                        queue=QUEUE,
                        max_age=MAX_AGE,
                    ),
                    now=now,
                )
            logger.info("email_hashes_keyed", filled=filled, left=left)
            return {"filled": filled, "left": left}

        await context.apply(fill)


async def fill_page(session: AsyncSession, key: bytes) -> int:
    """Key one page of each table. Returns how many rows it filled."""
    events = (await session.execute(_EVENTS, {"page": PAGE})).all()
    if events:
        await session.execute(
            _FILL_EVENT,
            [{"id": e.svix_id, "keyed": keyed_hashes(list(e.recipients), key)} for e in events],
        )
    suppressions = (await session.execute(_SUPPRESSIONS, {"page": PAGE})).scalars().all()
    if suppressions:
        await session.execute(
            _FILL_SUPPRESSION,
            [{"id": plain, "keyed": keyed_hash(plain, key)} for plain in suppressions],
        )
    lifts = (await session.execute(_LIFTS, {"page": PAGE})).all()
    if lifts:
        await session.execute(
            _FILL_LIFT,
            [{"id": lift.id, "keyed": keyed_hash(lift.address_hash, key)} for lift in lifts],
        )
    return len(events) + len(suppressions) + len(lifts)
