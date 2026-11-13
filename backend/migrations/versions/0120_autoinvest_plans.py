"""Add automatic investing: a client's plans and the runs they make.

Revision ID: 0120_autoinvest_plans
Revises: 0110_goals

A plan invests the same amount on a schedule. The client creates and changes it. The worker
opens a run for each due day and moves the plan to its next one. A run is a record of one due
day: never two for the same day, never two open at once, and nothing in it is ever deleted.
No money moves here. See docs/automatic-investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0120_autoinvest_plans"
down_revision: str | None = "0110_goals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE autoinvest_plans (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    -- The product's code in the investments module, checked by the service when it is set.
    product_code varchar(32) COLLATE "C" NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    amount_minor bigint NOT NULL,
    frequency varchar(8) NOT NULL,
    run_day smallint NOT NULL,
    network varchar(8) NOT NULL,
    -- The client's own number when they set the plan up, as +256 and nine digits.
    payer_msisdn varchar(13) COLLATE "C" NOT NULL,
    at_maturity varchar(10),
    status varchar(8) NOT NULL DEFAULT 'active',
    next_run_on date,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    paused_at timestamptz,
    stopped_at timestamptz,
    CONSTRAINT pk_autoinvest_plans PRIMARY KEY (id),
    CONSTRAINT ck_autoinvest_plans_currency CHECK (currency = 'UGX'),
    CONSTRAINT ck_autoinvest_plans_amount CHECK (amount_minor BETWEEN 1 AND 5000000),
    CONSTRAINT ck_autoinvest_plans_frequency CHECK (frequency IN ('weekly', 'monthly')),
    CONSTRAINT ck_autoinvest_plans_day CHECK (
        run_day >= 1 AND run_day <= CASE frequency WHEN 'weekly' THEN 7 ELSE 31 END),
    CONSTRAINT ck_autoinvest_plans_network CHECK (network IN ('mtn', 'airtel')),
    CONSTRAINT ck_autoinvest_plans_msisdn CHECK (payer_msisdn ~ '^\\+256[0-9]{9}$'),
    CONSTRAINT ck_autoinvest_plans_at_maturity CHECK (
        at_maturity IS NULL OR at_maturity IN ('roll_over', 'pay_out')),
    CONSTRAINT ck_autoinvest_plans_status CHECK (status IN ('active', 'paused', 'stopped')),
    -- An active plan always knows its next due day. A paused or stopped one has none.
    CONSTRAINT ck_autoinvest_plans_next CHECK ((status = 'active') = (next_run_on IS NOT NULL)),
    CONSTRAINT ck_autoinvest_plans_paused CHECK ((status = 'paused') = (paused_at IS NOT NULL)),
    CONSTRAINT ck_autoinvest_plans_stopped CHECK ((status = 'stopped') = (stopped_at IS NOT NULL))
);
CREATE INDEX ix_autoinvest_plans_client ON autoinvest_plans (client_id, created_at DESC, id DESC);
-- The daily run walks active plans by their next due day.
CREATE INDEX ix_autoinvest_plans_due ON autoinvest_plans (next_run_on, id) WHERE status = 'active';

CREATE TABLE autoinvest_runs (
    id push_id NOT NULL,
    plan_id push_id NOT NULL,
    client_id push_id NOT NULL,
    due_on date NOT NULL,
    status varchar(10) NOT NULL,
    attempt smallint NOT NULL DEFAULT 1,
    -- When a failed first attempt is tried again.
    retry_on date,
    -- The investment and the deposit this run asked for, in their own modules. No foreign keys.
    investment_id push_id,
    deposit_id push_id,
    -- Why it failed or was missed, as an error code. Never personal data.
    code varchar(64) COLLATE "C",
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_autoinvest_runs PRIMARY KEY (id),
    CONSTRAINT fk_autoinvest_runs_plan FOREIGN KEY (plan_id) REFERENCES autoinvest_plans (id),
    -- One run per due day of a plan, so a day is never collected twice.
    CONSTRAINT uq_autoinvest_runs_day UNIQUE (plan_id, due_on),
    CONSTRAINT ck_autoinvest_runs_status
        CHECK (status IN ('due', 'requested', 'paid', 'failed', 'missed')),
    CONSTRAINT ck_autoinvest_runs_attempt CHECK (attempt BETWEEN 1 AND 2),
    CONSTRAINT ck_autoinvest_runs_retry CHECK (
        retry_on IS NULL OR (attempt = 1 AND status = 'failed' AND retry_on > due_on)),
    CONSTRAINT ck_autoinvest_runs_requested CHECK (
        status NOT IN ('requested', 'paid') OR investment_id IS NOT NULL),
    CONSTRAINT ck_autoinvest_runs_code CHECK (code IS NULL OR code ~ '^[a-z0-9_.]{1,64}$')
);
-- At most one run of a plan is open, so a client never has two prompts from one plan at once.
CREATE UNIQUE INDEX uq_autoinvest_runs_open ON autoinvest_runs (plan_id)
    WHERE status IN ('due', 'requested');
CREATE INDEX ix_autoinvest_runs_client ON autoinvest_runs (client_id, due_on DESC, id DESC);
CREATE INDEX ix_autoinvest_runs_retry ON autoinvest_runs (retry_on) WHERE retry_on IS NOT NULL;
CREATE INDEX ix_autoinvest_runs_open ON autoinvest_runs (status, due_on)
    WHERE status IN ('due', 'requested')
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001), so the
# system's steps also require the worker's database role.
PLAN_GUARD = f"""
CREATE FUNCTION finvest_autoinvest_plans_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        is_client boolean := finvest_actor_kind() = 'client'
            AND finvest_actor_id() = NEW.client_id;
        is_worker boolean := finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}';
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NOT is_client OR NEW.status <> 'active'
                OR NEW.next_run_on <= (NEW.created_at AT TIME ZONE 'Africa/Kampala')::date THEN
                RAISE EXCEPTION 'a client starts their own plan, from tomorrow at the earliest'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'stopped' THEN
            RAISE EXCEPTION 'a stopped plan never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id
            OR NEW.created_at <> OLD.created_at OR NEW.currency <> OLD.currency THEN
            RAISE EXCEPTION 'whose plan it is never changes' USING ERRCODE = '42501';
        END IF;
        IF is_worker THEN
            -- The worker only moves an active plan on to its next due day.
            IF (to_jsonb(NEW) - '{{next_run_on,updated_at}}'::text[])
                <> (to_jsonb(OLD) - '{{next_run_on,updated_at}}'::text[])
                OR OLD.status <> 'active' OR NEW.next_run_on <= OLD.next_run_on THEN
                RAISE EXCEPTION 'the daily run only moves a plan forwards'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NOT is_client THEN
            RAISE EXCEPTION 'only the client changes their plan' USING ERRCODE = '42501';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'active' AND NEW.status IN ('paused', 'stopped'))
            OR (OLD.status = 'paused' AND NEW.status IN ('active', 'stopped'))) THEN
            RAISE EXCEPTION 'a plan cannot go from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'active'
            AND NEW.next_run_on <= (NEW.updated_at AT TIME ZONE 'Africa/Kampala')::date
            AND NEW.next_run_on IS DISTINCT FROM OLD.next_run_on THEN
            RAISE EXCEPTION 'a changed plan runs from tomorrow at the earliest'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RUN_GUARD = f"""
CREATE FUNCTION finvest_autoinvest_runs_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        is_worker boolean := finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}';
        owner push_id;
    BEGIN
        IF NOT is_worker THEN
            RAISE EXCEPTION 'only the daily run records runs' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT client_id INTO owner FROM autoinvest_plans WHERE id = NEW.plan_id;
            IF owner IS DISTINCT FROM NEW.client_id OR NEW.status NOT IN ('due', 'missed')
                OR NEW.attempt <> 1 OR NEW.investment_id IS NOT NULL
                OR NEW.deposit_id IS NOT NULL OR NEW.retry_on IS NOT NULL THEN
                RAISE EXCEPTION 'a run begins due or missed, for its own plan''s client'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.id <> OLD.id OR NEW.plan_id <> OLD.plan_id OR NEW.client_id <> OLD.client_id
            OR NEW.due_on <> OLD.due_on OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'which day a run is for never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status IN ('paid', 'missed')
            OR (OLD.status = 'failed' AND OLD.retry_on IS NULL) THEN
            RAISE EXCEPTION 'a finished run never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.attempt < OLD.attempt
            OR (NEW.investment_id IS DISTINCT FROM OLD.investment_id
                AND OLD.investment_id IS NOT NULL) THEN
            RAISE EXCEPTION 'a run only moves forwards' USING ERRCODE = '42501';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'due' AND NEW.status IN ('requested', 'failed', 'missed'))
            OR (OLD.status = 'requested' AND NEW.status IN ('paid', 'failed'))
            -- The one retry of a first failure.
            OR (OLD.status = 'failed' AND NEW.status IN ('due', 'missed')
                AND NEW.attempt = 2 - (NEW.status = 'missed')::int)) THEN
            RAISE EXCEPTION 'a run cannot go from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

RULES = [
    "REVOKE ALL ON FUNCTION finvest_autoinvest_plans_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_autoinvest_runs_guard() FROM PUBLIC",
    "CREATE TRIGGER autoinvest_plans_guard BEFORE INSERT OR UPDATE ON autoinvest_plans "
    "FOR EACH ROW EXECUTE FUNCTION finvest_autoinvest_plans_guard()",
    "CREATE TRIGGER autoinvest_runs_guard BEFORE INSERT OR UPDATE ON autoinvest_runs "
    "FOR EACH ROW EXECUTE FUNCTION finvest_autoinvest_runs_guard()",
    "REVOKE ALL ON autoinvest_plans FROM PUBLIC",
    "REVOKE ALL ON autoinvest_runs FROM PUBLIC",
    # No DELETE for anyone: a plan and its runs are a record of what a client asked for.
    f"GRANT SELECT, INSERT, UPDATE (product_code, amount_minor, frequency, run_day, network, "
    f"payer_msisdn, at_maturity, status, next_run_on, updated_at, paused_at, stopped_at) "
    f"ON autoinvest_plans TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (next_run_on, updated_at) ON autoinvest_plans TO {WORKER_ROLE}",
    f"GRANT SELECT ON autoinvest_runs TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, attempt, retry_on, investment_id, deposit_id, code, "
    f"updated_at) ON autoinvest_runs TO {WORKER_ROLE}",
    "ALTER TABLE autoinvest_plans ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE autoinvest_runs ENABLE ROW LEVEL SECURITY",
    # A client reaches their own plans and runs. Staff read every one; which staff may is a
    # permission checked in the application.
    f"CREATE POLICY autoinvest_plans_app_read ON autoinvest_plans FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY autoinvest_plans_app_insert ON autoinvest_plans FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY autoinvest_plans_app_update ON autoinvest_plans FOR UPDATE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY autoinvest_plans_worker ON autoinvest_plans TO {WORKER_ROLE} "
    "USING (finvest_actor_kind() = 'system') WITH CHECK (finvest_actor_kind() = 'system')",
    f"CREATE POLICY autoinvest_runs_app_read ON autoinvest_runs FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY autoinvest_runs_worker ON autoinvest_runs TO {WORKER_ROLE} "
    "USING (finvest_actor_kind() = 'system') WITH CHECK (finvest_actor_kind() = 'system')",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), PLAN_GUARD, RUN_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM autoinvest_plans) THEN
        RAISE EXCEPTION 'refusing to drop automatic investing plans that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Plans are client records; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE autoinvest_runs")
    op.execute("DROP TABLE autoinvest_plans")
    op.execute("DROP FUNCTION finvest_autoinvest_runs_guard()")
    op.execute("DROP FUNCTION finvest_autoinvest_plans_guard()")
