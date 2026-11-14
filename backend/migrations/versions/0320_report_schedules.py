"""Reports emailed to staff on a schedule, each schedule approved by a second person.

Revision ID: 0320_report_schedules
Revises: 0270_payout_wallet_short

* `report_schedules`: one person proposes a report, how often, its currency and the members of
  staff it goes to. A different person approves it, which starts it, or declines it. The
  proposer may withdraw it while it waits. Anyone who may see reports may stop a running one,
  because stopping sends less. The database refuses an approval by the proposer.
* `report_sends`: one row per schedule, person and period, so a report for a period goes to a
  person once however many times the run goes round. The email says the report is ready and
  links to the console, where the person signs in and downloads it; the file itself is never
  emailed and never stored.
* The worker sends the emails, so it may read the email address, name, role and status of a
  member of staff, and nothing else of their account.

See docs/reports.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0320_report_schedules"
down_revision: str | None = "0270_payout_wallet_short"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE report_schedules (
    id push_id NOT NULL,
    report_key varchar(40) COLLATE "C" NOT NULL,
    frequency varchar(16) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    recipients push_id[] NOT NULL,
    reason text NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason text,
    stopped_by push_id,
    stopped_at timestamptz,
    CONSTRAINT pk_report_schedules PRIMARY KEY (id),
    CONSTRAINT ck_report_schedules_report_key CHECK (report_key ~ '^[a-z][a-z_]{1,39}$'),
    CONSTRAINT ck_report_schedules_frequency
        CHECK (frequency IN ('daily', 'weekly', 'monthly', 'quarterly')),
    CONSTRAINT ck_report_schedules_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_report_schedules_recipients
        CHECK (cardinality(recipients) BETWEEN 1 AND 20),
    CONSTRAINT ck_report_schedules_reason CHECK (char_length(btrim(reason)) BETWEEN 1 AND 500),
    CONSTRAINT ck_report_schedules_status
        CHECK (status IN ('proposed', 'active', 'declined', 'withdrawn', 'stopped')),
    CONSTRAINT ck_report_schedules_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL) AND (decided_by IS NULL) = (decided_at IS NULL)),
    -- Starting or declining a schedule is a second person's decision.
    CONSTRAINT ck_report_schedules_second_person CHECK (
        status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_report_schedules_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_report_schedules_stopped CHECK (
        (status = 'stopped') = (stopped_by IS NOT NULL)
        AND (stopped_by IS NULL) = (stopped_at IS NULL))
);
CREATE INDEX ix_report_schedules_status ON report_schedules (status, proposed_at);
CREATE TABLE report_sends (
    id push_id NOT NULL,
    schedule_id push_id NOT NULL,
    staff_id push_id NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    status varchar(16) NOT NULL,
    failure_code varchar(64),
    created_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_report_sends PRIMARY KEY (id),
    CONSTRAINT fk_report_sends_schedule
        FOREIGN KEY (schedule_id) REFERENCES report_schedules (id),
    CONSTRAINT uq_report_sends_period UNIQUE (schedule_id, staff_id, date_from, date_to),
    CONSTRAINT ck_report_sends_days CHECK (date_from <= date_to),
    CONSTRAINT ck_report_sends_status CHECK (status IN ('queued', 'sent', 'skipped', 'failed')),
    CONSTRAINT ck_report_sends_finished CHECK ((status = 'queued') = (finished_at IS NULL))
);
CREATE INDEX ix_report_sends_schedule ON report_sends (schedule_id, created_at DESC)
"""

GUARD = """
CREATE FUNCTION finvest_report_schedules_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
            RAISE EXCEPTION 'a report schedule is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'report schedules are changed by staff' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed'
                OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a schedule is proposed by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.report_key, NEW.frequency, NEW.currency, NEW.recipients, NEW.reason,
                NEW.proposed_by, NEW.proposed_at)
            IS DISTINCT FROM (OLD.report_key, OLD.frequency, OLD.currency, OLD.recipients,
                OLD.reason, OLD.proposed_by, OLD.proposed_at) THEN
            RAISE EXCEPTION 'what a schedule sends never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'proposed' AND NEW.status IN ('active', 'declined', 'withdrawn') THEN
            IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a schedule is decided by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'active' AND NEW.status = 'stopped'
            AND (NEW.decided_by, NEW.decided_at, NEW.decision_reason)
                IS NOT DISTINCT FROM (OLD.decided_by, OLD.decided_at, OLD.decision_reason) THEN
            IF NEW.stopped_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a schedule is stopped by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'a report schedule cannot move from % to %', OLD.status, NEW.status
            USING ERRCODE = '42501';
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"
S = "report_schedules"
D = "report_sends"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_report_schedules_guard() FROM PUBLIC",
    f"CREATE TRIGGER {S}_guard BEFORE INSERT OR UPDATE OR DELETE ON {S} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_report_schedules_guard()",
    f"CREATE TRIGGER {S}_no_truncate BEFORE TRUNCATE ON {S} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_report_schedules_guard()",
    f"REVOKE ALL ON {S}, {D} FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE ON {S} TO {APP_ROLE}",
    f"GRANT SELECT ON {S} TO {WORKER_ROLE}",
    f"GRANT SELECT ON {D} TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON {D} TO {WORKER_ROLE}",
    f"ALTER TABLE {S} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {S}_app ON {S} FOR ALL TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY {S}_worker ON {S} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"ALTER TABLE {D} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {D}_app ON {D} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {D}_worker ON {D} FOR ALL TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
    # The worker emails staff their reports: these columns only.
    f"GRANT SELECT (id, email, name, role, status) ON identity_staff TO {WORKER_ROLE}",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM report_schedules) THEN
        RAISE EXCEPTION 'refusing to drop report schedules';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(
        "REVOKE SELECT (id, email, name, role, status) ON identity_staff FROM finvest_worker"
    )
    op.execute(f"DROP TABLE {D}")
    op.execute(f"DROP TABLE {S}")
    op.execute("DROP FUNCTION finvest_report_schedules_guard()")
