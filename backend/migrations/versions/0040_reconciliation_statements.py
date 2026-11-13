"""Add imported statements and their lines.

Revision ID: 0040_reconciliation_statements
Revises: 0070_migration_checks

* One statement per pot and business day. A second import of a day is refused, whatever the
  file looks like.
* Days join up. A statement's opening balance must equal the closing balance of the previous
  day's statement for the same pot, and no day may be skipped once a pot has a statement.
* A statement adds up. At commit the database checks that the opening balance plus every line
  equals the closing balance, and that the line count is the one declared.
* Statements and lines are insert only, for every role.
* Only staff import and read them. The worker reads them for the daily run.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0040_reconciliation_statements"
down_revision: str | None = "0070_migration_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE reconciliation_statements (
    id push_id NOT NULL,
    source varchar(16) NOT NULL,
    account_reference varchar(64) NOT NULL,
    business_date date NOT NULL,
    currency varchar(3) NOT NULL,
    opening_minor bigint NOT NULL,
    closing_minor bigint NOT NULL,
    line_count integer NOT NULL,
    file_sha256 char(64) NOT NULL,
    imported_by push_id NOT NULL,
    imported_at timestamptz NOT NULL,
    created_xact xid8 NOT NULL DEFAULT pg_current_xact_id(),
    CONSTRAINT pk_reconciliation_statements PRIMARY KEY (id),
    CONSTRAINT uq_reconciliation_statements_pot_day
        UNIQUE (source, account_reference, business_date),
    CONSTRAINT ck_reconciliation_statements_source
        CHECK (source IN ('iotec', 'bank', 'custodian')),
    CONSTRAINT ck_reconciliation_statements_account_reference
        CHECK (account_reference ~ '^[A-Za-z0-9._:-]{1,64}$'),
    CONSTRAINT ck_reconciliation_statements_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_reconciliation_statements_line_count CHECK (line_count BETWEEN 0 AND 50000),
    CONSTRAINT ck_reconciliation_statements_file_sha256 CHECK (file_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE TABLE reconciliation_statement_lines (
    id push_id NOT NULL,
    statement_id push_id NOT NULL,
    line_no integer NOT NULL,
    value_date date NOT NULL,
    direction varchar(3) NOT NULL,
    amount_minor bigint NOT NULL,
    reference varchar(200) NOT NULL,
    narrative varchar(200) NOT NULL,
    fingerprint char(64) NOT NULL,
    CONSTRAINT pk_reconciliation_statement_lines PRIMARY KEY (id),
    CONSTRAINT fk_reconciliation_statement_lines_statement
        FOREIGN KEY (statement_id) REFERENCES reconciliation_statements (id),
    CONSTRAINT uq_reconciliation_statement_lines_line_no UNIQUE (statement_id, line_no),
    CONSTRAINT uq_reconciliation_statement_lines_fingerprint UNIQUE (statement_id, fingerprint),
    CONSTRAINT ck_reconciliation_statement_lines_line_no CHECK (line_no >= 1),
    CONSTRAINT ck_reconciliation_statement_lines_direction CHECK (direction IN ('in', 'out')),
    CONSTRAINT ck_reconciliation_statement_lines_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_reconciliation_statement_lines_fingerprint
        CHECK (fingerprint ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_reconciliation_statement_lines_reference
    ON reconciliation_statement_lines (reference) WHERE reference <> '';
CREATE INDEX ix_reconciliation_statement_lines_value_date
    ON reconciliation_statement_lines (value_date, amount_minor)
"""

IMMUTABLE_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_statements_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'imported statements are never changed' USING ERRCODE = '42501';
    END;
    $$
"""

CONTINUITY_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_statement_continuity() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        previous record;
    BEGIN
        -- Never trust a supplied value: lines are tied to the transaction that made the statement.
        NEW.created_xact := pg_current_xact_id();
        -- One import at a time per pot, so two days cannot both claim to follow the same one.
        PERFORM pg_advisory_xact_lock(
            hashtextextended('statement:' || NEW.source || ':' || NEW.account_reference, 0));
        SELECT business_date, closing_minor, currency INTO previous
          FROM reconciliation_statements
         WHERE source = NEW.source AND account_reference = NEW.account_reference
         ORDER BY business_date DESC LIMIT 1;
        IF FOUND THEN
            IF NEW.business_date = previous.business_date THEN
                RAISE EXCEPTION 'this day already has a statement'
                    USING ERRCODE = '23505',
                          CONSTRAINT = 'uq_reconciliation_statements_pot_day';
            END IF;
            IF NEW.business_date <> previous.business_date + 1 THEN
                RAISE EXCEPTION 'statements are imported one day after another'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_reconciliation_statements_next_day';
            END IF;
            IF NEW.opening_minor <> previous.closing_minor OR NEW.currency <> previous.currency THEN
                RAISE EXCEPTION 'the opening balance is not the last closing balance'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_reconciliation_statements_continuity';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
"""

ADDS_UP_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_statement_adds_up() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        target_id push_id;
        statement record;
        lines record;
    BEGIN
        IF TG_TABLE_NAME = 'reconciliation_statements' THEN
            target_id := NEW.id;
        ELSE
            target_id := NEW.statement_id;
        END IF;
        SELECT opening_minor, closing_minor, line_count INTO statement
          FROM reconciliation_statements WHERE id = target_id;
        SELECT count(*) AS total,
               COALESCE(sum(CASE direction WHEN 'in' THEN amount_minor ELSE -amount_minor END), 0)
                   AS movement
          INTO lines FROM reconciliation_statement_lines AS line
         WHERE line.statement_id = target_id;
        IF lines.total <> statement.line_count
           OR statement.opening_minor + lines.movement <> statement.closing_minor THEN
            RAISE EXCEPTION 'the statement does not add up'
                USING ERRCODE = '23514', CONSTRAINT = 'ck_reconciliation_statements_adds_up';
        END IF;
        RETURN NULL;
    END;
    $$
"""

CLOSED_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_statement_lines_closed() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        -- Lines join a statement only in the transaction that created it.
        IF NOT EXISTS (
            SELECT FROM reconciliation_statements
             WHERE id = NEW.statement_id AND created_xact = pg_current_xact_id()
        ) THEN
            RAISE EXCEPTION 'lines are added only while their statement is being imported'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_reconciliation_statements_immutable() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_reconciliation_statement_continuity() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_reconciliation_statement_adds_up() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_reconciliation_statement_lines_closed() FROM PUBLIC",
    "CREATE TRIGGER reconciliation_statements_continuity BEFORE INSERT "
    "ON reconciliation_statements FOR EACH ROW "
    "EXECUTE FUNCTION finvest_reconciliation_statement_continuity()",
    "CREATE TRIGGER reconciliation_statement_lines_closed BEFORE INSERT "
    "ON reconciliation_statement_lines FOR EACH ROW "
    "EXECUTE FUNCTION finvest_reconciliation_statement_lines_closed()",
    "CREATE CONSTRAINT TRIGGER reconciliation_statements_adds_up AFTER INSERT "
    "ON reconciliation_statements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION finvest_reconciliation_statement_adds_up()",
    "CREATE CONSTRAINT TRIGGER reconciliation_statement_lines_adds_up AFTER INSERT "
    "ON reconciliation_statement_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION finvest_reconciliation_statement_adds_up()",
]
for _table in ("reconciliation_statements", "reconciliation_statement_lines"):
    RULES += [
        f"CREATE TRIGGER {_table}_immutable BEFORE UPDATE OR DELETE ON {_table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_reconciliation_statements_immutable()",
        f"CREATE TRIGGER {_table}_no_truncate BEFORE TRUNCATE ON {_table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_reconciliation_statements_immutable()",
        f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY",
        f"REVOKE ALL ON {_table} FROM PUBLIC",
        f"GRANT SELECT, INSERT ON {_table} TO {APP_ROLE}",
        f"GRANT SELECT ON {_table} TO {WORKER_ROLE}",
        f"CREATE POLICY {_table}_app_read ON {_table} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
        f"CREATE POLICY {_table}_worker_read ON {_table} FOR SELECT TO {WORKER_ROLE} USING (true)",
    ]
RULES += [
    # A statement is imported in the name of the staff member who uploaded it.
    f"CREATE POLICY reconciliation_statements_app_insert ON reconciliation_statements "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF} AND imported_by = finvest_actor_id())",
    f"CREATE POLICY reconciliation_statement_lines_app_insert ON reconciliation_statement_lines "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    functions = [IMMUTABLE_FUNCTION, CONTINUITY_FUNCTION, ADDS_UP_FUNCTION, CLOSED_FUNCTION]
    for statement in [*split(TABLES), *functions, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM reconciliation_statements) THEN
        RAISE EXCEPTION 'refusing to drop imported statements';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE reconciliation_statement_lines")
    op.execute("DROP TABLE reconciliation_statements")
    for name in ("lines_closed", "adds_up", "continuity"):
        op.execute(f"DROP FUNCTION finvest_reconciliation_statement_{name}()")
    op.execute("DROP FUNCTION finvest_reconciliation_statements_immutable()")
