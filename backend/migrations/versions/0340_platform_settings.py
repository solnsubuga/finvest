"""Keep the platform's settings, each changed by two people on the console's Settings screen.

Revision ID: 0340_platform_settings
Revises: 0270_payout_wallet_short

`platform_settings` holds every proposed value of every setting: which payment channels are
open, the public holidays, the bank payout cut-off, the maturity reminders, how long an idle
staff session lasts, the office networks and the statement layouts. The list and the rules
for each value are in `app/modules/platform_settings/domain/catalogue.py`.

One member of staff proposes a value and a different one approves or rejects it; only the
proposer can withdraw. PostgreSQL enforces all of it, as for the trust account (migration
0260). The value in use for a setting is its last approved row, or the code's default while
there is none. A decided row never changes and no row is ever deleted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0340_platform_settings"
down_revision: str | None = "0270_payout_wallet_short"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE platform_settings (
    id push_id NOT NULL,
    key varchar(40) NOT NULL,
    value jsonb NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_platform_settings PRIMARY KEY (id),
    CONSTRAINT ck_platform_settings_key CHECK (key IN ('payment_channels', 'public_holidays',
        'bank_payout_cutoff', 'maturity_reminders', 'staff_idle_minutes', 'office_networks',
        'statement_layouts')),
    CONSTRAINT ck_platform_settings_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_platform_settings_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_platform_settings_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)),
    -- Two people. Only the proposer can withdraw, and never approve or reject, their own.
    CONSTRAINT ck_platform_settings_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
-- One change at a time per setting, so an approval is always of the value the approver read.
CREATE UNIQUE INDEX uq_platform_settings_open ON platform_settings (key) WHERE status = 'proposed';
CREATE INDEX ix_platform_settings_in_use
    ON platform_settings (key, decided_at DESC, id DESC) WHERE status = 'approved'
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001). The guard
# names the acting member of staff on every row, so the two-person check compares the people
# whose transactions wrote the proposal and the decision.
GUARD = """
CREATE FUNCTION finvest_platform_settings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff change a setting' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a setting starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided setting never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
        THEN
            RAISE EXCEPTION 'a setting is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_platform_settings_guard() FROM PUBLIC",
    "CREATE TRIGGER platform_settings_guard BEFORE INSERT OR UPDATE ON platform_settings "
    "FOR EACH ROW EXECUTE FUNCTION finvest_platform_settings_guard()",
    "REVOKE ALL ON platform_settings FROM PUBLIC",
    # No DELETE for anyone: who changed a rule, and when, is part of the record.
    "GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason) "
    f"ON platform_settings TO {APP_ROLE}",
    f"GRANT SELECT ON platform_settings TO {WORKER_ROLE}",
    "ALTER TABLE platform_settings ENABLE ROW LEVEL SECURITY",
    # The values in use are read wherever they apply, including before anyone is signed in
    # (a staff session's idle time) and for clients (whether a channel is open). Proposals and
    # history are for staff.
    "CREATE POLICY platform_settings_app_read ON platform_settings FOR SELECT "
    f"TO {APP_ROLE} USING (status = 'approved' OR finvest_actor_kind() = 'staff')",
    "CREATE POLICY platform_settings_app_insert ON platform_settings FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'staff')",
    "CREATE POLICY platform_settings_app_update ON platform_settings FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff')",
    "CREATE POLICY platform_settings_worker ON platform_settings FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM platform_settings) THEN
        RAISE EXCEPTION 'refusing to drop platform settings that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Who changed a rule, and when, is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE platform_settings")
    op.execute("DROP FUNCTION finvest_platform_settings_guard()")
