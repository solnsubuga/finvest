"""Add the record of daily interest runs.

Revision ID: 0062_investment_interest_runs
Revises: 0061_investments

It also closes a gap the review of migration 0061 found: the database now checks that an
investment is created at the current time, so a wrong clock cannot quote an old rate.

One row per business day and currency: how many investments the run paid, how much interest
and tax it posted, and how many it left behind. The interest itself is in the ledger and on
each investment; this table is the summary staff read on the console. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0062_investment_interest_runs"
down_revision: str | None = "0016_ledger_staff_source_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE investment_interest_runs (
    run_date date NOT NULL,
    currency text NOT NULL,
    investments integer NOT NULL DEFAULT 0,
    gross_minor bigint NOT NULL DEFAULT 0,
    tax_minor bigint NOT NULL DEFAULT 0,
    behind integer NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_investment_interest_runs PRIMARY KEY (run_date, currency),
    CONSTRAINT ck_investment_interest_runs_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_investment_interest_runs_totals CHECK (
        investments >= 0 AND behind >= 0 AND tax_minor >= 0 AND gross_minor >= tax_minor),
    CONSTRAINT ck_investment_interest_runs_finished CHECK (
        finished_at IS NULL OR finished_at >= started_at)
)
"""

# A run's totals only grow, and its day, currency and start never change.
GUARD = """
CREATE FUNCTION finvest_investment_interest_runs_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        IF NEW.run_date <> OLD.run_date OR NEW.currency <> OLD.currency
            OR NEW.started_at <> OLD.started_at OR NEW.investments < OLD.investments
            OR NEW.gross_minor < OLD.gross_minor OR NEW.tax_minor < OLD.tax_minor THEN
            RAISE EXCEPTION 'the record of an interest run only grows'
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'investment_interest_runs_only_grow';
        END IF;
        RETURN NEW;
    END
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_investment_interest_runs_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_interest_runs_guard BEFORE UPDATE ON investment_interest_runs "
    "FOR EACH ROW EXECUTE FUNCTION finvest_investment_interest_runs_guard()",
    "REVOKE ALL ON investment_interest_runs FROM PUBLIC",
    # Only the worker writes it, and nobody deletes it.
    f"GRANT SELECT ON investment_interest_runs TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (investments, gross_minor, tax_minor, behind, finished_at) "
    f"ON investment_interest_runs TO {WORKER_ROLE}",
    "ALTER TABLE investment_interest_runs ENABLE ROW LEVEL SECURITY",
    # It shows interest before tax, so it is for staff and never for a client.
    f"CREATE POLICY investment_interest_runs_staff_read ON investment_interest_runs "
    f"FOR SELECT TO {APP_ROLE} USING (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY investment_interest_runs_worker ON investment_interest_runs "
    f"TO {WORKER_ROLE} USING (finvest_actor_kind() = 'system') "
    "WITH CHECK (finvest_actor_kind() = 'system')",
]


# The guard of migration 0061 picks the rate from `created_at`. That is only safe if
# `created_at` is now, by PostgreSQL's own clock, and the payment window is the agreed one.
QUOTED_NOW = [
    """
    CREATE FUNCTION finvest_investments_quoted_now() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF abs(extract(epoch FROM NEW.created_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'an investment is created at the current time'
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'investments_quoted_now';
            END IF;
            IF NEW.pay_by <> NEW.created_at + interval '72 hours' THEN
                RAISE EXCEPTION 'an investment is payable for 72 hours'
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'investments_pay_by';
            END IF;
            RETURN NEW;
        END
        $$
    """,
    "REVOKE ALL ON FUNCTION finvest_investments_quoted_now() FROM PUBLIC",
    "CREATE TRIGGER investments_quoted_now BEFORE INSERT ON investments "
    "FOR EACH ROW EXECUTE FUNCTION finvest_investments_quoted_now()",
]


def upgrade() -> None:
    for statement in [TABLE, GUARD, *RULES, *QUOTED_NOW]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_interest_runs) THEN
        RAISE EXCEPTION 'refusing to drop interest runs that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # The record of what was paid each day is kept; a downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE investment_interest_runs")
    op.execute("DROP FUNCTION finvest_investment_interest_runs_guard()")
    op.execute("DROP TRIGGER investments_quoted_now ON investments")
    op.execute("DROP FUNCTION finvest_investments_quoted_now()")
