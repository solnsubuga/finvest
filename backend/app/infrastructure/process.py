"""Plumbing shared by the worker and scheduler: stop on signals, and a liveness heartbeat.

These processes serve no HTTP, so their health check is a file they touch while their loop is
turning. `python -m app.infrastructure.process <name>` exits 0 while the heartbeat is fresh.
"""

import argparse
import asyncio
import signal
import sys
import tempfile
import time
from pathlib import Path

HEARTBEAT_SECONDS = 5.0
STALE_AFTER_SECONDS = 30.0


def stop_on_signals() -> asyncio.Event:
    """An event set by SIGTERM or SIGINT so work in hand can finish before exit."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    return stop


def heartbeat_path(name: str, directory: Path | None = None) -> Path:
    return (directory or Path(tempfile.gettempdir())) / f"finvest-{name}.alive"


def beat(path: Path) -> None:
    path.touch()


def alive(path: Path, *, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except OSError:
        return False
    return age < STALE_AFTER_SECONDS


async def wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        async with asyncio.timeout(seconds):
            await stop.wait()
    except TimeoutError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Exit 0 if the named process is alive.")
    parser.add_argument("name", choices=("worker", "scheduler"))
    sys.exit(0 if alive(heartbeat_path(parser.parse_args().name)) else 1)


if __name__ == "__main__":
    main()
