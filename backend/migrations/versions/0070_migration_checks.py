"""Keep trial imports, the old system's totals and two-person sign-offs for the migration.

Revision ID: 0070_migration_checks
Revises: 0007_notifications

A trial import keeps its result and nothing from the spreadsheet: counts, problem codes with
their sheet, row and column, the new system's totals, and the figures of every investment that
differs. No name, phone number or identity document is stored here.

A difference between the old system and the new is accepted with a sign-off, and a sign-off
decides what the ledger is opened with. PostgreSQL holds it to two people:

* A sign-off and its decision each name the staff member whose transaction wrote them.
* The person who decides is never the person who proposed.
* A sign-off is decided once.
* Every table here is insert-only for every role, so a result, a total or a reason cannot be
  changed afterwards. A correction is a new row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0070_migration_checks"
down_revision: str | None = "0007_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE migration_trials (
    id push_id NOT NULL,
    as_of date NOT NULL,
    clients_sha256 char(64) NOT NULL,
    investments_sha256 char(64) NOT NULL,
    error_count integer NOT NULL,
    warning_count integer NOT NULL,
    problems jsonb NOT NULL,
    summary jsonb NOT NULL,
    new_totals jsonb NOT NULL,
    differing jsonb NOT NULL,
    ran_by push_id NOT NULL,
    ran_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_trials PRIMARY KEY (id),
    CONSTRAINT ck_migration_trials_counts CHECK (error_count >= 0 AND warning_count >= 0),
    CONSTRAINT ck_migration_trials_hashes
        CHECK (clients_sha256 ~ '^[0-9a-f]{64}$' AND investments_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_migration_trials_problems
        CHECK (jsonb_typeof(problems) = 'array' AND jsonb_array_length(problems) <= 500),
    CONSTRAINT ck_migration_trials_objects
        CHECK (jsonb_typeof(summary) = 'object' AND jsonb_typeof(new_totals) = 'object'
               AND jsonb_typeof(differing) = 'object')
);
CREATE INDEX ix_migration_trials_ran_at ON migration_trials (ran_at, id);

CREATE TABLE migration_control_totals (
    id push_id NOT NULL,
    as_of date NOT NULL,
    totals jsonb NOT NULL,
    entered_by push_id NOT NULL,
    entered_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_control_totals PRIMARY KEY (id),
    CONSTRAINT ck_migration_control_totals_totals CHECK (jsonb_typeof(totals) = 'object')
);
CREATE INDEX ix_migration_control_totals_entered_at
    ON migration_control_totals (entered_at, id);

CREATE TABLE migration_sign_offs (
    id push_id NOT NULL,
    key varchar(120) NOT NULL,
    figures bigint[] NOT NULL,
    reason varchar(500) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_sign_offs PRIMARY KEY (id),
    CONSTRAINT ck_migration_sign_offs_key
        CHECK (key ~ '^(total|investment|tax_rate):[A-Za-z0-9._/:-]+$'),
    CONSTRAINT ck_migration_sign_offs_figures
        CHECK (array_ndims(figures) = 1 AND cardinality(figures) BETWEEN 2 AND 4
               AND array_position(figures, NULL) IS NULL),
    CONSTRAINT ck_migration_sign_offs_reason CHECK (length(btrim(reason)) > 0)
);
CREATE INDEX ix_migration_sign_offs_key ON migration_sign_offs (key, proposed_at);

CREATE TABLE migration_sign_off_decisions (
    sign_off_id push_id NOT NULL,
    outcome varchar(16) NOT NULL,
    reason varchar(500),
    decided_by push_id NOT NULL,
    decided_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_sign_off_decisions PRIMARY KEY (sign_off_id),
    CONSTRAINT fk_migration_sign_off_decisions_sign_off_id
        FOREIGN KEY (sign_off_id) REFERENCES migration_sign_offs (id),
    CONSTRAINT ck_migration_sign_off_decisions_outcome
        CHECK (outcome IN ('approved', 'rejected')),
    CONSTRAINT ck_migration_sign_off_decisions_reason CHECK (length(btrim(reason)) > 0),
    CONSTRAINT ck_migration_sign_off_decisions_rejection_reason
        CHECK (outcome <> 'rejected' OR reason IS NOT NULL)
)
"""

FUNCTIONS = [
    """
CREATE FUNCTION finvest_migration_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'migration table % is insert-only', TG_TABLE_NAME USING ERRCODE = '42501';
    END;
    $$
""",
    # Runs as the table owner so it reads the sign-off whatever the caller may see.
    """
CREATE FUNCTION finvest_migration_second_person() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        proposer public.push_id;
    BEGIN
        SELECT proposed_by INTO proposer
        FROM public.migration_sign_offs WHERE id = NEW.sign_off_id;
        IF proposer = NEW.decided_by THEN
            RAISE EXCEPTION 'a sign-off is decided by a second person, not its proposer'
                USING ERRCODE = '23514', CONSTRAINT = 'migration_sign_off_second_person';
        END IF;
        RETURN NEW;
    END;
    $$
""",
]

TABLE_NAMES = (
    "migration_trials",
    "migration_control_totals",
    "migration_sign_offs",
    "migration_sign_off_decisions",
)
# Which column names the staff member whose transaction wrote the row.
WRITERS = {
    "migration_trials": "ran_by",
    "migration_control_totals": "entered_by",
    "migration_sign_offs": "proposed_by",
    "migration_sign_off_decisions": "decided_by",
}
STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    *(
        f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC"
        for name in ("finvest_migration_immutable", "finvest_migration_second_person")
    ),
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_migration_immutable()"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_migration_immutable()"
        for table in TABLE_NAMES
    ),
    "CREATE TRIGGER migration_sign_off_decisions_second_person "
    "BEFORE INSERT ON migration_sign_off_decisions "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_second_person()",
    *(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in TABLE_NAMES),
    *(f"REVOKE ALL ON {table} FROM PUBLIC" for table in TABLE_NAMES),
    *(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}" for table in TABLE_NAMES),
    # The worker reads the checks when it runs the switch. It is not a person, so it cannot
    # run a trial, enter a total or sign anything off.
    *(f"GRANT SELECT ON {table} TO {WORKER_ROLE}" for table in TABLE_NAMES),
    *(
        f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER_ROLE} USING (true)"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE POLICY {table}_app_read ON {table} FOR SELECT TO {APP_ROLE} USING ({STAFF})"
        for table in TABLE_NAMES
    ),
    # Each row names the staff member whose transaction wrote it, and nobody else.
    *(
        f"CREATE POLICY {table}_app_insert ON {table} FOR INSERT TO {APP_ROLE} "
        f"WITH CHECK ({STAFF} AND {writer} = finvest_actor_id())"
        for table, writer in WRITERS.items()
    ),
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM migration_sign_offs) OR EXISTS (SELECT FROM migration_control_totals)
        OR EXISTS (SELECT FROM migration_trials) THEN
        RAISE EXCEPTION 'refusing to drop migration checks that hold records';
    END IF;
END
$$
"""


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *FUNCTIONS, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the tables would erase who accepted each difference and why.
    op.execute(EMPTY_GUARD)
    for table in reversed(TABLE_NAMES):
        op.execute(f"DROP TABLE {table}")
    for function in ("finvest_migration_second_person", "finvest_migration_immutable"):
        op.execute(f"DROP FUNCTION {function}()")
