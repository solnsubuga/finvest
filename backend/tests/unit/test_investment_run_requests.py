"""Which days staff may ask the interest run or the maturity run for again."""

from datetime import date, timedelta

import pytest

# The service first: the jobs module imported on its own meets a cycle through transfers.
import app.modules.investments.service  # noqa: F401
from app.modules.investments import jobs, run_requests
from app.modules.investments.domain.runs import INTEREST_DAYS_BACK, RunKind, run_date_problem
from app.scheduler_main import schedules

TODAY = date(2026, 11, 14)


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [
        (-30, "not_yet"),
        (-1, "not_yet"),
        (0, None),
        (1, None),
        (INTEREST_DAYS_BACK, None),
        (INTEREST_DAYS_BACK + 1, "too_old"),
        (400, "too_old"),
    ],
)
def test_interest_is_run_again_for_any_day_the_scheduler_would_catch_up(
    days_ago: int, expected: str | None
) -> None:
    run_date = TODAY - timedelta(days=days_ago)
    assert run_date_problem(RunKind.INTEREST, run_date, TODAY) == expected


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [(-1, "not_yet"), (0, None), (1, "maturity_today_only"), (40, "maturity_today_only")],
)
def test_maturity_is_run_again_for_today_only(days_ago: int, expected: str | None) -> None:
    run_date = TODAY - timedelta(days=days_ago)
    assert run_date_problem(RunKind.MATURITY, run_date, TODAY) == expected


def test_the_job_kinds_are_the_scheduled_runs_own() -> None:
    assert run_requests.JOB_KINDS == {
        RunKind.INTEREST: jobs.ACCRUE_KIND,
        RunKind.MATURITY: jobs.MATURE_KIND,
    }


def test_the_scheduler_has_never_caught_up_further_back_than_staff_may_ask() -> None:
    [accrual] = [s for s in schedules() if s.kind == jobs.ACCRUE_KIND]
    assert accrual.max_catch_up == INTEREST_DAYS_BACK
