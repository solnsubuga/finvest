"""Keep a record of each daily journal sent to the accounting system.

Revision ID: 0250_finance_journal_exports
Revises: 0220_identity_email_login

The Finance screen exports the ledger, added up by account code, one journal per day, for days
the ledger is closed through. Each export is a row here, so the screen can say what was last
sent and which closed days have not been sent yet.

* A row names the currency, the first and last day it covers, how many journal lines it held
  and what its debits came to, who exported it and when.
* Only a signed-in member of staff adds a row, and only as themselves. A row never changes and
  is never removed.
* The export reads the ledger and moves nothing, so it needs one person, not two.

See docs/finance.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0250_finance_journal_exports"
down_revision: str | None = "0220_identity_email_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "finance_journal_exports"

TABLE = """
CREATE TABLE finance_journal_exports (
    id push_id NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    lines integer NOT NULL,
    debit_total_minor bigint NOT NULL,
    exported_by push_id NOT NULL,
    exported_at timestamptz NOT NULL,
    CONSTRAINT pk_finance_journal_exports PRIMARY KEY (id),
    CONSTRAINT ck_finance_journal_exports_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_finance_journal_exports_days CHECK (
        date_from <= date_to AND date_to - date_from < 31),
    CONSTRAINT ck_finance_journal_exports_lines CHECK (lines >= 0),
    CONSTRAINT ck_finance_journal_exports_total CHECK (debit_total_minor >= 0)
);
CREATE INDEX ix_finance_journal_exports_latest
    ON finance_journal_exports (currency, exported_at DESC)
"""

GUARD = """
CREATE FUNCTION finvest_finance_journal_exports_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a journal export record never changes' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff'
            OR NEW.exported_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a journal export is recorded by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_finance_journal_exports_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_finance_journal_exports_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_finance_journal_exports_guard()",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
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
    IF EXISTS (SELECT FROM finance_journal_exports) THEN
        RAISE EXCEPTION 'refusing to drop journal export records';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_finance_journal_exports_guard()")
