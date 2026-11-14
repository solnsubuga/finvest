"""Raise investments the last interest run left behind on /health/operations.

Revision ID: 0271_interest_behind_signal
Revises: 0234_kyc_next_of_kin_contacts

The interest run records how many investments it left behind (`behind` on
`investment_interest_runs`), but only a log line said so. `finvest_operations_signals` gains
`interest_behind`: active investments that are still due for the last finished run of their
currency. `GET /health/operations` answers 503 while it is above zero, so the uptime alert
reaches a person. The count is live, so it falls back to zero as soon as staff put each
investment right, without waiting for the next run.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0271_interest_behind_signal"
down_revision: str | None = "0234_kyc_next_of_kin_contacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = """
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
        UNION ALL
        SELECT 'payouts_waiting_on_wallet', count(*) FROM public.payouts p
            WHERE p.status = 'queued' AND p.kind = 'mobile_money'
                AND (SELECT a.deferred_for FROM public.payout_send_attempts a
                     WHERE a.payout_id = p.id ORDER BY a.attempt DESC LIMIT 1) = 'wallet_short'
"""

# The same test as `_IS_DUE` in app/modules/investments/accrual.py, against the last finished
# run of the investment's currency that is not dated after today.
BEHIND = """
        UNION ALL
        SELECT 'interest_behind', count(*) FROM public.investments i
            JOIN (SELECT currency, max(run_date) AS run_date
                    FROM public.investment_interest_runs
                   WHERE finished_at IS NOT NULL
                     AND run_date <= (p_now AT TIME ZONE 'Africa/Kampala')::date
                   GROUP BY currency) r ON r.currency = i.currency
            WHERE i.status = 'active' AND i.withdrawal_requested_at IS NULL
                AND i.start_date < r.run_date
                AND (i.accrued_through IS NULL OR (i.accrued_through < r.run_date
                    AND (i.maturity_date IS NULL OR i.accrued_through < i.maturity_date)))
"""


def _function(body: str) -> str:
    return (
        "CREATE OR REPLACE FUNCTION finvest_operations_signals(p_now timestamptz) "
        "RETURNS TABLE (signal text, total bigint) "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp "
        f"AS $$ {body} $$"
    )


def upgrade() -> None:
    op.execute(_function(BEFORE + BEHIND))


def downgrade() -> None:
    # Changes a read-only count and drops no data.
    op.execute(_function(BEFORE))
