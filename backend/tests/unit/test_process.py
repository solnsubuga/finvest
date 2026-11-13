import asyncio
import os
from pathlib import Path

from app.infrastructure.process import (
    STALE_AFTER_SECONDS,
    alive,
    beat,
    heartbeat_path,
    wait_or_stop,
)


def test_a_process_is_alive_only_while_its_heartbeat_is_fresh(tmp_path: Path) -> None:
    path = heartbeat_path("worker", tmp_path)
    assert not alive(path)
    beat(path)
    assert alive(path)
    stamp = path.stat().st_mtime
    assert not alive(path, now=stamp + STALE_AFTER_SECONDS)
    os.utime(path, (stamp, stamp))
    assert alive(path, now=stamp + STALE_AFTER_SECONDS - 1)


async def test_waiting_ends_at_once_when_asked_to_stop() -> None:
    stop = asyncio.Event()
    stop.set()
    async with asyncio.timeout(1):
        await wait_or_stop(stop, 60)


async def test_waiting_ends_after_the_interval() -> None:
    async with asyncio.timeout(1):
        await wait_or_stop(asyncio.Event(), 0.01)
