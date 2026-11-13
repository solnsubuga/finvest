"""Fixtures for every suite. Database setup lives in integration/conftest.py."""

from pathlib import Path

import pytest

from tests.support.clock import AsyncBarrier, FakeClock

SUITES = {
    "integration": Path(__file__).parent / "integration",
    "journey": Path(__file__).parent / "journeys",
}


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


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def concurrency_barrier() -> AsyncBarrier:
    return AsyncBarrier(2)
