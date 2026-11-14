"""Lock a goal's savings until its date, and unlock them seven days after the client asks.

Revision ID: 0303_goal_locks
Revises: 0302_goal_paydays

A client may lock a goal so that its money cannot be taken out early on a whim. The lock is
kept on each investment saved towards the goal, in the investments module's own table
`investment_withdrawal_locks`, so the investments module refuses an early withdrawal or a send
to another client without asking the goals module. The goal keeps a copy of the date, and when
the client asked to unlock, for its own screens.

What must hold is enforced here, not only in application code:

* While an investment's lock runs, it is not withdrawn early and not sent to another client.
* A lock is only ever lengthened, or shortened to no sooner than seven days from today (Solo's
  rule: undoing a lock waits seven days). A lock that already ends sooner than that stays as it
  is. Nobody removes a lock.
* A client locks only their own investments that are waiting for payment or running.

See docs/goals.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0303_goal_locks"
down_revision: str | None = "0302_goal_paydays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE investment_withdrawal_locks (
    investment_id push_id NOT NULL,
    -- The client's ID in the identity module, so row-level security needs no join.
    client_id push_id NOT NULL,
    -- The first day the investment may be withdrawn early again.
    locked_until date NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_investment_withdrawal_locks PRIMARY KEY (investment_id),
    CONSTRAINT fk_investment_withdrawal_locks_investment FOREIGN KEY (investment_id)
        REFERENCES investments (id)
)
"""

# A lock is only lengthened, or shortened to seven days from today at the soonest. Shared by
# the investments' locks and the goals' copy of them.
LOCK_RULE = """
CREATE FUNCTION finvest_lock_may_change(old_until date, new_until date) RETURNS boolean
    LANGUAGE sql VOLATILE SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT new_until IS NOT NULL AND (old_until IS NULL OR new_until >= least(
            old_until, (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date + 7))
    $$
"""

LOCKS_GUARD = """
CREATE FUNCTION finvest_investment_withdrawal_locks_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
        investment public.investments%ROWTYPE;
        today date := (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date;
    BEGIN
        IF actor IS DISTINCT FROM 'client' AND actor IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the client or the system locks an investment'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT * INTO investment FROM public.investments WHERE id = NEW.investment_id;
            IF investment.id IS NULL OR investment.client_id <> NEW.client_id
                OR investment.status NOT IN ('awaiting_payment', 'active')
                OR NEW.locked_until <= today THEN
                RAISE EXCEPTION 'a lock is on a waiting or running investment, until a later day'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.investment_id <> OLD.investment_id OR NEW.client_id <> OLD.client_id
            OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a lock keeps its investment' USING ERRCODE = '42501';
        END IF;
        IF NOT public.finvest_lock_may_change(OLD.locked_until, NEW.locked_until) THEN
            RAISE EXCEPTION 'a lock is undone seven days after asking, not sooner'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# Separate from the investments guard (0241), which is the investing thread's: these only add
# a refusal, and read the locks whoever asks.
WITHDRAWAL_FENCE = """
CREATE FUNCTION finvest_investments_withdrawal_lock() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.withdrawal_requested_at IS NOT NULL AND OLD.withdrawal_requested_at IS NULL
            AND EXISTS (SELECT FROM public.investment_withdrawal_locks WHERE investment_id = NEW.id
                        AND locked_until > (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date)
        THEN
            RAISE EXCEPTION 'a locked investment is not withdrawn early' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

TRANSFER_FENCE = """
CREATE FUNCTION finvest_investment_transfers_lock() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF EXISTS (SELECT FROM public.investment_withdrawal_locks WHERE investment_id = NEW.investment_id
                   AND locked_until > (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date)
        THEN
            RAISE EXCEPTION 'a locked investment is not sent to another client'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

GOALS_FENCE = """
CREATE FUNCTION finvest_goals_lock_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF NEW.locked_until IS DISTINCT FROM OLD.locked_until
            AND NOT finvest_lock_may_change(OLD.locked_until, NEW.locked_until) THEN
            RAISE EXCEPTION 'a lock is undone seven days after asking, not sooner'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    TABLE,
    "CREATE INDEX ix_investment_withdrawal_locks_client ON investment_withdrawal_locks (client_id)",
    LOCK_RULE,
    LOCKS_GUARD,
    WITHDRAWAL_FENCE,
    TRANSFER_FENCE,
    "REVOKE ALL ON FUNCTION finvest_lock_may_change(date, date) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION finvest_lock_may_change(date, date) TO {APP_ROLE}, {WORKER_ROLE}",
    "REVOKE ALL ON FUNCTION finvest_investment_withdrawal_locks_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_investments_withdrawal_lock() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_investment_transfers_lock() FROM PUBLIC",
    "CREATE TRIGGER investment_withdrawal_locks_guard BEFORE INSERT OR UPDATE "
    "ON investment_withdrawal_locks "
    "FOR EACH ROW EXECUTE FUNCTION finvest_investment_withdrawal_locks_guard()",
    "CREATE TRIGGER investments_withdrawal_lock BEFORE UPDATE OF withdrawal_requested_at "
    "ON investments FOR EACH ROW EXECUTE FUNCTION finvest_investments_withdrawal_lock()",
    "CREATE TRIGGER investment_transfers_lock BEFORE INSERT ON investment_transfers "
    "FOR EACH ROW EXECUTE FUNCTION finvest_investment_transfers_lock()",
    # Nobody deletes a lock: it is lengthened or shortened, and lapses on its day.
    f"GRANT SELECT, INSERT, UPDATE (locked_until, updated_at) ON investment_withdrawal_locks "
    f"TO {APP_ROLE}, {WORKER_ROLE}",
    "ALTER TABLE investment_withdrawal_locks ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY investment_withdrawal_locks_app_read ON investment_withdrawal_locks "
    f"FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY investment_withdrawal_locks_app_insert ON investment_withdrawal_locks "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY investment_withdrawal_locks_app_update ON investment_withdrawal_locks "
    f"FOR UPDATE TO {APP_ROLE} USING ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY investment_withdrawal_locks_worker ON investment_withdrawal_locks "
    f"TO {WORKER_ROLE} USING (true)",
    # The goal's own copy: until when, and when the client asked to unlock.
    "ALTER TABLE goals ADD COLUMN locked_until date, "
    "ADD COLUMN unlock_requested_at timestamptz, "
    "ADD CONSTRAINT ck_goals_unlock CHECK (unlock_requested_at IS NULL OR locked_until IS NOT NULL)",
    GOALS_FENCE,
    "REVOKE ALL ON FUNCTION finvest_goals_lock_guard() FROM PUBLIC",
    "CREATE TRIGGER goals_lock_guard BEFORE UPDATE OF locked_until ON goals "
    "FOR EACH ROW EXECUTE FUNCTION finvest_goals_lock_guard()",
    f"GRANT UPDATE (locked_until, unlock_requested_at) ON goals TO {APP_ROLE}",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_withdrawal_locks)
        OR EXISTS (SELECT FROM goals WHERE locked_until IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the locks clients set';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # A lock is a client's promise to themselves; a downgrade never quietly lifts it.
    op.execute(EMPTY_GUARD)
    op.execute(f"REVOKE UPDATE (locked_until, unlock_requested_at) ON goals FROM {APP_ROLE}")
    op.execute("DROP TRIGGER goals_lock_guard ON goals")
    op.execute("DROP FUNCTION finvest_goals_lock_guard()")
    op.execute("ALTER TABLE goals DROP COLUMN unlock_requested_at, DROP COLUMN locked_until")
    op.execute("DROP TRIGGER investment_transfers_lock ON investment_transfers")
    op.execute("DROP TRIGGER investments_withdrawal_lock ON investments")
    op.execute("DROP TABLE investment_withdrawal_locks")
    op.execute("DROP FUNCTION finvest_investment_transfers_lock()")
    op.execute("DROP FUNCTION finvest_investments_withdrawal_lock()")
    op.execute("DROP FUNCTION finvest_investment_withdrawal_locks_guard()")
    op.execute("DROP FUNCTION finvest_lock_may_change(date, date)")
