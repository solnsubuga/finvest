"""Name each accounting system's accounts for Finvest's ledger codes, and record each export's
format.

Revision ID: 0290_finance_code_maps
Revises: 0271_interest_behind_signal

The daily journal can be downloaded as Finvest's generic CSV or as the journal import file of
QuickBooks Online, Xero, Sage Accounting or Zoho Books. Those four name their own accounts, so each has an
account code map: which of the system's accounts each Finvest ledger code is posted to.

* `finance_code_maps` holds every proposed map, one row per proposal for one system. One member
  of staff proposes a whole map, and a different one approves or rejects it. Only the proposer
  withdraws it. The approved map decided last is the one in use. A decided map never changes,
  and no row is removed, so every map ever used is kept.
* `finance_code_map_lines` holds a proposal's accounts, one row per Finvest code. They are
  written by the proposer while the proposal waits, and never change after.
* `finance_journal_exports` gains the format of each export and the map it used, so what was
  sent to the accounting system can always be told again.

PostgreSQL enforces the two people. See docs/finance.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0290_finance_code_maps"
down_revision: str | None = "0271_interest_behind_signal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
MAPS = "finance_code_maps"
LINES = "finance_code_map_lines"
EXPORTS = "finance_journal_exports"

TABLES = """
CREATE TABLE finance_code_maps (
    id push_id NOT NULL,
    system varchar(12) NOT NULL,
    -- Xero asks for a tax rate on every line. Null for the other systems.
    tax_rate varchar(50),
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_finance_code_maps PRIMARY KEY (id),
    CONSTRAINT ck_finance_code_maps_system CHECK (system IN ('quickbooks', 'xero', 'sage', 'zoho')),
    CONSTRAINT ck_finance_code_maps_tax_rate CHECK (
        (system = 'xero') = (tax_rate IS NOT NULL) AND (tax_rate IS NULL OR btrim(tax_rate) <> '')),
    CONSTRAINT ck_finance_code_maps_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_finance_code_maps_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_finance_code_maps_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)),
    -- Two people. Only the proposer can withdraw, and never approve or reject, their own.
    CONSTRAINT ck_finance_code_maps_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
-- One proposal at a time per system, so an approval is always of the map the approver read.
CREATE UNIQUE INDEX uq_finance_code_maps_open ON finance_code_maps (system)
    WHERE status = 'proposed';
CREATE INDEX ix_finance_code_maps_active ON finance_code_maps (system, decided_at DESC, id DESC)
    WHERE status = 'approved';

CREATE TABLE finance_code_map_lines (
    map_id push_id NOT NULL,
    ledger_code varchar(64) COLLATE "C" NOT NULL,
    account varchar(100) NOT NULL,
    CONSTRAINT pk_finance_code_map_lines PRIMARY KEY (map_id, ledger_code),
    CONSTRAINT fk_finance_code_map_lines_map FOREIGN KEY (map_id)
        REFERENCES finance_code_maps (id),
    CONSTRAINT ck_finance_code_map_lines_code
        CHECK (ledger_code ~ '^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)*$'),
    CONSTRAINT ck_finance_code_map_lines_account
        CHECK (btrim(account) = account AND account <> '')
);

ALTER TABLE finance_journal_exports
    ADD COLUMN format varchar(12) NOT NULL DEFAULT 'csv',
    ADD COLUMN code_map_id push_id;
ALTER TABLE finance_journal_exports
    ADD CONSTRAINT ck_finance_journal_exports_format
        CHECK (format IN ('csv', 'quickbooks', 'xero', 'sage', 'zoho')),
    ADD CONSTRAINT ck_finance_journal_exports_code_map
        CHECK ((format = 'csv') = (code_map_id IS NULL)),
    ADD CONSTRAINT fk_finance_journal_exports_code_map FOREIGN KEY (code_map_id)
        REFERENCES finance_code_maps (id)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001). The guard
# names the acting member of staff on every row, so the two-person check above compares the
# people whose transactions wrote the proposal and the decision.
MAPS_GUARD = """
CREATE FUNCTION finvest_finance_code_maps_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a code map is never removed' USING ERRCODE = '42501';
        END IF;
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff change a code map' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a code map starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided code map never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
        THEN
            RAISE EXCEPTION 'a code map is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

LINES_GUARD = """
CREATE FUNCTION finvest_finance_code_map_lines_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'the accounts of a code map never change' USING ERRCODE = '42501';
        END IF;
        IF NOT EXISTS (
            SELECT FROM finance_code_maps
            WHERE id = NEW.map_id AND status = 'proposed'
                AND finvest_actor_kind() = 'staff' AND proposed_by = finvest_actor_id()
        ) THEN
            RAISE EXCEPTION 'only the proposer names accounts, while the map waits'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# An export names the map it used only if that map was approved, for the export's own system.
EXPORT_MAP_GUARD = """
CREATE FUNCTION finvest_finance_journal_exports_map_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF NEW.code_map_id IS NOT NULL AND NOT EXISTS (
            SELECT FROM finance_code_maps
            WHERE id = NEW.code_map_id AND status = 'approved' AND system = NEW.format
        ) THEN
            RAISE EXCEPTION 'an export uses an approved map of its own system'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_finance_code_maps_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_finance_code_map_lines_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_finance_journal_exports_map_guard() FROM PUBLIC",
    f"CREATE TRIGGER {EXPORTS}_map_guard BEFORE INSERT ON {EXPORTS} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_finance_journal_exports_map_guard()",
    f"CREATE TRIGGER {MAPS}_guard BEFORE INSERT OR UPDATE OR DELETE ON {MAPS} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_finance_code_maps_guard()",
    f"CREATE TRIGGER {MAPS}_no_truncate BEFORE TRUNCATE ON {MAPS} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_finance_code_maps_guard()",
    f"CREATE TRIGGER {LINES}_guard BEFORE INSERT OR UPDATE OR DELETE ON {LINES} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_finance_code_map_lines_guard()",
    f"CREATE TRIGGER {LINES}_no_truncate BEFORE TRUNCATE ON {LINES} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_finance_code_map_lines_guard()",
    f"REVOKE ALL ON {MAPS}, {LINES} FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason) "
    f"ON {MAPS} TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON {LINES} TO {APP_ROLE}",
    f"GRANT SELECT ON {MAPS}, {LINES} TO {WORKER_ROLE}",
    f"ALTER TABLE {MAPS} ENABLE ROW LEVEL SECURITY",
    f"ALTER TABLE {LINES} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {MAPS}_app_read ON {MAPS} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {MAPS}_app_insert ON {MAPS} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY {MAPS}_app_update ON {MAPS} FOR UPDATE TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {MAPS}_worker_read ON {MAPS} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {LINES}_app_read ON {LINES} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {LINES}_app_insert ON {LINES} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY {LINES}_worker_read ON {LINES} FOR SELECT TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), MAPS_GUARD, LINES_GUARD, EXPORT_MAP_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM finance_code_maps)
        OR EXISTS (SELECT FROM finance_journal_exports WHERE format <> 'csv') THEN
        RAISE EXCEPTION 'refusing to drop the account code maps';
    END IF;
END
$$
"""


def downgrade() -> None:
    # The maps, who proposed and approved them, and which exports used them are kept.
    op.execute(EMPTY_GUARD)
    op.execute(f"DROP TRIGGER {EXPORTS}_map_guard ON {EXPORTS}")
    op.execute("DROP FUNCTION finvest_finance_journal_exports_map_guard()")
    op.execute(f"ALTER TABLE {EXPORTS} DROP CONSTRAINT fk_finance_journal_exports_code_map")
    op.execute(f"ALTER TABLE {EXPORTS} DROP CONSTRAINT ck_finance_journal_exports_code_map")
    op.execute(f"ALTER TABLE {EXPORTS} DROP CONSTRAINT ck_finance_journal_exports_format")
    op.execute(f"ALTER TABLE {EXPORTS} DROP COLUMN code_map_id, DROP COLUMN format")
    op.execute(f"DROP TABLE {LINES}")
    op.execute(f"DROP TABLE {MAPS}")
    op.execute("DROP FUNCTION finvest_finance_code_map_lines_guard()")
    op.execute("DROP FUNCTION finvest_finance_code_maps_guard()")
