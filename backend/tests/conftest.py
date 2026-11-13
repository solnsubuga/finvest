"""Fixtures for every suite. Database setup lives in integration/conftest.py."""

from pathlib import Path

import pytest

from tests.support.clock import AsyncBarrier, FakeClock

INTEGRATION = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # The unit job runs `-m 'not integration'`. An unmarked database test would silently move
    # there, and a marked unit test would escape it.
    for item in items:
        in_integration = INTEGRATION in Path(str(item.path)).parents
        if in_integration != (item.get_closest_marker("integration") is not None):
            raise pytest.UsageError(
                f"{item.nodeid}: tests are marked `integration` exactly when they live in "
                "tests/integration/"
            )


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def concurrency_barrier() -> AsyncBarrier:
    return AsyncBarrier(2)
