"""Counts of work that has stopped, readable by the API without reading the work itself.

Revision ID: 0130_operations_signals
Revises: 0100_support

`GET /health/operations` answers 503 when any of these counts is above zero, and an uptime
alert watches it. The API role may read only three columns of `jobs` and nothing of the
scheduler's tables, so the counts come from one function that runs as the table owner and
returns numbers only: no identifiers, payloads, error messages or amounts.

- `jobs_overdue`: work due more than 10 minutes ago that no worker has claimed.
- `jobs_stalled`: work whose lease ran out more than 5 minutes ago and was never reclaimed.
- `jobs_failed`: work that failed in the last 24 hours, including any uncertain outcome.
- `schedules_late`: a recurring run more than 10 minutes past its fire time.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0130_operations_signals"
down_revision: str | None = "0100_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"

FUNCTION = """
CREATE FUNCTION finvest_operations_signals(p_now timestamptz)
    RETURNS TABLE (signal text, total bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT 'jobs_overdue', count(*) FROM public.jobs
            WHERE status = 'pending' AND run_at < p_now - interval '10 minutes'
        UNION ALL
        SELECT 'jobs_stalled', count(*) FROM public.jobs
            WHERE status = 'running' AND lease_expires_at < p_now - interval '5 minutes'
        UNION ALL
        SELECT 'jobs_failed', count(*) FROM public.jobs
            WHERE status = 'failed'
                AND finished_at > p_now - interval '24 hours' AND finished_at <= p_now
        UNION ALL
        SELECT 'schedules_late', count(*) FROM public.schedule_progress
            WHERE next_fire_at < p_now - interval '10 minutes'
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_operations_signals(timestamptz) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION finvest_operations_signals(timestamptz) TO {APP_ROLE}",
]


def upgrade() -> None:
    for statement in [FUNCTION, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Drops a read-only view of the queue and no data.
    op.execute("DROP FUNCTION finvest_operations_signals(timestamptz)")
