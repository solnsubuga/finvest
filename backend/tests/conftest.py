"""Fixtures for every suite. Database setup lives in integration/conftest.py."""

import os
import random
from pathlib import Path

import pytest

from tests.support.clock import AsyncBarrier, FakeClock

SUITES = {
    "integration": Path(__file__).parent / "integration",
    "journey": Path(__file__).parent / "journeys",
}


# Set to a whole number to run the tests in a random order that number picks. CI sets it for
# the integration tests, so a test that only passes after another one fails there.
ORDER_SEED = "FINVEST_TEST_ORDER_SEED"


def pytest_report_header() -> str:
    seed = os.environ.get(ORDER_SEED)
    if not seed:
        return "test order: as written"
    return f"test order: random, seed {seed} (set {ORDER_SEED}={seed} to run it again)"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # The unit job runs `-m 'not integration and not journey'`. An unmarked database test
    # would silently move there, and a marked unit test would escape it.
    for item in items:
        parents = Path(str(item.path)).parents
        for marker, folder in SUITES.items():
            if (folder in parents) != (item.get_closest_marker(marker) is not None):
                raise pytest.UsageError(
                    f"{item.nodeid}: tests are marked `{marker}` exactly when they live in "
                    f"tests/{folder.name}/"
                )
    seed = os.environ.get(ORDER_SEED)
    if seed:
        items[:] = shuffled(items, int(seed))


def shuffled(items: list[pytest.Item], seed: int) -> list[pytest.Item]:
    """The files in a random order, then the tests within each file. A file's tests stay
    together, so a fixture made once per file is still made once."""
    choose = random.Random(seed)
    files: dict[str, list[pytest.Item]] = {}
    for item in items:
        files.setdefault(item.nodeid.split("::")[0], []).append(item)
    names = list(files)
    choose.shuffle(names)
    ordered: list[pytest.Item] = []
    for name in names:
        tests = files[name]
        choose.shuffle(tests)
        ordered.extend(tests)
    return ordered


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def concurrency_barrier() -> AsyncBarrier:
    return AsyncBarrier(2)
