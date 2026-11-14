"""Record that a payout waits because Finvest's own ioTec wallet is short, and raise it.

Revision ID: 0270_payout_wallet_short
Revises: 0280_payout_bank_bulk_debits

A send that took nothing ends `deferred`, and the sweep sends it again. Most deferrals clear on
their own, but one because Finvest's wallet does not hold enough to pay only clears when staff
top the wallet up. The attempt now says so in `deferred_for` ('wallet_short'), written with its
outcome and never changed after (the guard from 0033 already refuses any change to a finished
attempt).

`finvest_operations_signals` gains `payouts_waiting_on_wallet`: queued mobile money payouts
whose last send was deferred for the wallet. `GET /health/operations` answers 503 while it is
above zero, so the uptime alert reaches a person at once rather than after the two hours a
payout takes to show as held up.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0270_payout_wallet_short"
down_revision: str | None = "0280_payout_bank_bulk_debits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

COLUMN = [
    "ALTER TABLE payout_send_attempts ADD COLUMN deferred_for varchar(16)",
    "ALTER TABLE payout_send_attempts ADD CONSTRAINT ck_payout_send_attempts_deferred_for "
    "CHECK (deferred_for IS NULL OR (outcome = 'deferred' AND deferred_for IN ('wallet_short')))",
    f"GRANT UPDATE (deferred_for) ON payout_send_attempts TO {WORKER_ROLE}",
]

SIGNALS = """
CREATE OR REPLACE FUNCTION finvest_operations_signals(p_now timestamptz)
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
        UNION ALL
        SELECT 'payouts_waiting_on_wallet', count(*) FROM public.payouts p
            WHERE p.status = 'queued' AND p.kind = 'mobile_money'
                AND (SELECT a.deferred_for FROM public.payout_send_attempts a
                     WHERE a.payout_id = p.id ORDER BY a.attempt DESC LIMIT 1) = 'wallet_short'
    $$
"""

OLD_SIGNALS = """
CREATE OR REPLACE FUNCTION finvest_operations_signals(p_now timestamptz)
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

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_send_attempts WHERE deferred_for IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop why payout sends were deferred';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in [*COLUMN, SIGNALS]:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(OLD_SIGNALS)
    op.execute("ALTER TABLE payout_send_attempts DROP COLUMN deferred_for")
