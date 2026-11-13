"""Let the ledger close a day for good.

Revision ID: 0014_ledger_closed_days
Revises: 0031_payouts

Once a day has been reconciled against the bank and the provider and signed off, nothing more may
be dated in it. Otherwise the balance that was agreed for that day could change afterwards.

PostgreSQL enforces this, not only the application:

* `ledger_closed_days` is an insert-only list of dates. The books are closed through the latest.
* Only a staff transaction can add a date, under its own ID.
* A date is added only after that day has ended in Kampala, and only later than the last one.
  A closed day is never reopened.
* A journal entry dated on or before the closed day is refused, for every role and every entry
  type. A mistake found in a closed day is corrected by an entry dated in an open day.
* Closing and posting take turns. A close waits for every transaction that has already posted
  an entry, and an entry posted while a close is in progress waits for it and is then checked
  against the new date. No entry can slip into a day behind its close.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_ledger_closed_days"
down_revision: str | None = "0031_payouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

# 4016201014 is the advisory lock key for "the ledger's closed day". Posting takes it shared,
# closing takes it exclusive, and both hold it until their transaction ends.

TABLE = """
CREATE TABLE ledger_closed_days (
    closed_through date NOT NULL,
    closed_by push_id NOT NULL,
    closed_at timestamptz NOT NULL,
    CONSTRAINT pk_ledger_closed_days PRIMARY KEY (closed_through)
)
"""

FUNCTIONS = [
    # Runs as the table owner so the rule holds whatever the caller may see.
    """
CREATE FUNCTION finvest_ledger_close_day() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        latest date;
    BEGIN
        PERFORM pg_advisory_xact_lock(4016201014);
        IF NEW.closed_through >= (now() AT TIME ZONE 'Africa/Kampala')::date THEN
            RAISE EXCEPTION 'a day can be closed only after it has ended'
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_closed_days_ended';
        END IF;
        SELECT max(closed_through) INTO latest FROM public.ledger_closed_days;
        IF NEW.closed_through <= latest THEN
            RAISE EXCEPTION 'the ledger is already closed through %', latest
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_closed_days_forward';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    """
CREATE FUNCTION finvest_ledger_refuse_closed_day() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        latest date;
    BEGIN
        PERFORM pg_advisory_xact_lock_shared(4016201014);
        SELECT max(closed_through) INTO latest FROM public.ledger_closed_days;
        IF NEW.effective_date <= latest THEN
            RAISE EXCEPTION 'the ledger is closed through %', latest
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_closed_day';
        END IF;
        RETURN NEW;
    END;
    $$
""",
]

STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_ledger_close_day() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_ledger_refuse_closed_day() FROM PUBLIC",
    "CREATE TRIGGER ledger_closed_days_immutable BEFORE UPDATE OR DELETE ON ledger_closed_days "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_immutable()",
    "CREATE TRIGGER ledger_closed_days_no_truncate BEFORE TRUNCATE ON ledger_closed_days "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_ledger_immutable()",
    "CREATE TRIGGER ledger_closed_days_close BEFORE INSERT ON ledger_closed_days "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_close_day()",
    "CREATE TRIGGER ledger_journal_entries_closed_day BEFORE INSERT ON ledger_journal_entries "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_refuse_closed_day()",
    "ALTER TABLE ledger_closed_days ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON ledger_closed_days FROM PUBLIC",
    f"GRANT SELECT, INSERT ON ledger_closed_days TO {APP_ROLE}",
    # The worker reads the closed day for reports. A day is closed by people, not by a job.
    f"GRANT SELECT ON ledger_closed_days TO {WORKER_ROLE}",
    f"CREATE POLICY ledger_closed_days_worker_read ON ledger_closed_days "
    f"FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY ledger_closed_days_app_read ON ledger_closed_days "
    f"FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY ledger_closed_days_app_insert ON ledger_closed_days "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF} AND closed_by = finvest_actor_id())",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM ledger_closed_days) THEN
        RAISE EXCEPTION 'refusing to drop closed days that would reopen the books';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in [TABLE, *FUNCTIONS, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the table would reopen every day that was signed off.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TRIGGER ledger_journal_entries_closed_day ON ledger_journal_entries")
    op.execute("DROP TABLE ledger_closed_days")
    op.execute("DROP FUNCTION finvest_ledger_refuse_closed_day()")
    op.execute("DROP FUNCTION finvest_ledger_close_day()")
