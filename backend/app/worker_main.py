"""The worker process: `python -m app.worker_main`. Connect it as `finvest_worker`.

It will claim and run jobs from the PostgreSQL jobs table. The job queue arrives in its own
pull request; until then the process checks its database role, keeps a heartbeat and waits.
"""

import asyncio

import structlog

from app.infrastructure.database import Database
from app.infrastructure.database.roles import WORKER, check_role
from app.infrastructure.logging import configure_logging
from app.infrastructure.process import (
    HEARTBEAT_SECONDS,
    beat,
    heartbeat_path,
    stop_on_signals,
    wait_or_stop,
)
from app.infrastructure.settings import Settings


async def run(settings: Settings, stop: asyncio.Event | None = None) -> None:
    stop = stop or stop_on_signals()
    log = structlog.get_logger()
    database = Database(settings)
    heartbeat = heartbeat_path("worker")
    try:
        await check_role(database, WORKER)
        log.info("worker_started")
        while not stop.is_set():
            if await database.ready():
                beat(heartbeat)
            await wait_or_stop(stop, HEARTBEAT_SECONDS)
    finally:
        heartbeat.unlink(missing_ok=True)
        await database.close()
        log.info("worker_stopped")


def main() -> None:
    settings = Settings()
    configure_logging(settings, "worker")
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
