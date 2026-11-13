"""Make the closed-day check independent of how the caller opened its transaction.

Revision ID: 0015_ledger_closed_day_guard
Revises: 0090_notification_inbox

Two hardening changes to migration 0014, both asked for in review.

* The closed-day check takes a lock and then reads the latest closed day. That read sees a close
  that committed while it waited only in a READ COMMITTED transaction. In REPEATABLE READ or
  SERIALIZABLE it would read an older snapshot and could let an entry into a day that was just
  closed. The application always uses READ COMMITTED, and now the database insists on it: an
  entry or a close in any other isolation level is refused.
* The advisory lock uses the two-key form, (4016, 14). Other modules take one-key locks from a
  hash, and the two forms can never collide.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_ledger_closed_day_guard"
down_revision: str | None = "0090_notification_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ISOLATION_GUARD = """
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'the ledger is posted to and closed in read committed transactions'
                USING ERRCODE = '0A000';
        END IF;"""

CLOSE_DAY = """
CREATE OR REPLACE FUNCTION finvest_ledger_close_day() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        latest date;
    BEGIN{guard}
        PERFORM {lock};
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
"""

REFUSE_CLOSED_DAY = """
CREATE OR REPLACE FUNCTION finvest_ledger_refuse_closed_day() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        latest date;
    BEGIN{guard}
        PERFORM {lock};
        SELECT max(closed_through) INTO latest FROM public.ledger_closed_days;
        IF NEW.effective_date <= latest THEN
            RAISE EXCEPTION 'the ledger is closed through %', latest
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_closed_day';
        END IF;
        RETURN NEW;
    END;
    $$
"""


def upgrade() -> None:
    op.execute(CLOSE_DAY.format(guard=ISOLATION_GUARD, lock="pg_advisory_xact_lock(4016, 14)"))
    op.execute(
        REFUSE_CLOSED_DAY.format(
            guard=ISOLATION_GUARD, lock="pg_advisory_xact_lock_shared(4016, 14)"
        )
    )


def downgrade() -> None:
    # Back to the functions exactly as migration 0014 made them.
    op.execute(CLOSE_DAY.format(guard="", lock="pg_advisory_xact_lock(4016201014)"))
    op.execute(REFUSE_CLOSED_DAY.format(guard="", lock="pg_advisory_xact_lock_shared(4016201014)"))
