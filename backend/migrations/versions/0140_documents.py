"""Add client documents: statements and receipts, and the PDF of each.

Revision ID: 0140_documents
Revises: 0070_migration_checks

One table, owned by the documents module. See docs/documents.md.

A row is asked for first (`pending`): by the client for a statement of their chosen dates, by
the monthly run for each client, or by a confirmed payment for its receipt. The worker then
builds it once from the ledger and stores the PDF (`ready`), or records that the month had
nothing to show (`empty`) or that it could not be built (`failed`). A finished row never
changes and no row is ever deleted: what Finvest gave a client in writing is part of the record.

The PDF is kept in the row. A statement is a few tens of kilobytes, and keeping it beside its
record means it is backed up, fenced and kept exactly like the record. The database refuses
a file over 5 MB.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0140_documents"
down_revision: str | None = "0070_migration_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
MAX_BYTES = 5 * 1024 * 1024

TABLE = f"""
CREATE TABLE documents (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    kind varchar(24) NOT NULL,
    -- The client's key for a statement they ask for, `YYYY-MM` for a monthly one, and the
    -- deposit ID for a receipt. Unique per client and kind, so a repeat asks for nothing new.
    idempotency_key varchar(200) COLLATE "C" NOT NULL,
    period_start date,
    period_end date,
    -- The deposit a receipt is for.
    source_id varchar(64) COLLATE "C",
    status varchar(10) NOT NULL DEFAULT 'pending',
    failure_code varchar(64),
    requested_at timestamptz NOT NULL,
    finished_at timestamptz,
    file_name varchar(160),
    content bytea,
    sha256 bytea,
    byte_size integer,
    CONSTRAINT pk_documents PRIMARY KEY (id),
    CONSTRAINT uq_documents_client_kind_key UNIQUE (client_id, kind, idempotency_key),
    CONSTRAINT ck_documents_kind
        CHECK (kind IN ('monthly_statement', 'requested_statement', 'receipt')),
    CONSTRAINT ck_documents_idempotency_key
        CHECK (idempotency_key ~ '^[A-Za-z0-9._:@-]{{1,200}}$'),
    CONSTRAINT ck_documents_status CHECK (status IN ('pending', 'ready', 'empty', 'failed')),
    -- A statement covers a period of at most 366 days. A receipt is for one deposit.
    CONSTRAINT ck_documents_shape CHECK (
        (kind = 'receipt' AND source_id IS NOT NULL
            AND period_start IS NULL AND period_end IS NULL)
        OR (kind <> 'receipt' AND source_id IS NULL
            AND period_start IS NOT NULL AND period_end IS NOT NULL
            AND period_start <= period_end AND period_end - period_start < 366)),
    -- Only a monthly statement can turn out to have nothing in it.
    CONSTRAINT ck_documents_empty CHECK (status <> 'empty' OR kind = 'monthly_statement'),
    CONSTRAINT ck_documents_finished CHECK (
        (status = 'pending') = (finished_at IS NULL)
        AND (status = 'failed') = (failure_code IS NOT NULL)),
    CONSTRAINT ck_documents_failure_code CHECK (failure_code ~ '^[a-z][a-z0-9_]{{0,63}}$'),
    -- A ready document has its file; nothing else does.
    CONSTRAINT ck_documents_file CHECK (
        (status = 'ready') = (content IS NOT NULL)
        AND (content IS NULL) = (file_name IS NULL)
        AND (content IS NULL) = (sha256 IS NULL)
        AND (content IS NULL) = (byte_size IS NULL)
        AND (content IS NULL OR (octet_length(content) = byte_size
            AND byte_size BETWEEN 1 AND {MAX_BYTES} AND octet_length(sha256) = 32
            AND sha256 = sha256(content)))),
    CONSTRAINT ck_documents_file_name CHECK (file_name ~ '^[A-Za-z0-9 ._-]{{1,160}}[.]pdf$')
);
CREATE INDEX ix_documents_client ON documents (client_id, requested_at DESC, id DESC);
CREATE UNIQUE INDEX uq_documents_receipt ON documents (source_id) WHERE kind = 'receipt'
"""

GUARD = """
CREATE FUNCTION finvest_documents_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'TRUNCATE' THEN
            RAISE EXCEPTION 'documents are never deleted' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'pending' OR NEW.finished_at IS NOT NULL OR NEW.content IS NOT NULL
            THEN
                RAISE EXCEPTION 'a document is asked for before it is built'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'pending' THEN
            RAISE EXCEPTION 'a finished document never changes' USING ERRCODE = '42501';
        END IF;
        IF finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the worker builds a document' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'failure_code', 'finished_at', 'file_name',
                'content', 'sha256', 'byte_size'])
            <> (to_jsonb(OLD) - ARRAY['status', 'failure_code', 'finished_at', 'file_name',
                'content', 'sha256', 'byte_size'])
        THEN
            RAISE EXCEPTION 'what a document was asked for never changes'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_documents_guard() FROM PUBLIC",
    "CREATE TRIGGER documents_guard BEFORE INSERT OR UPDATE ON documents "
    "FOR EACH ROW EXECUTE FUNCTION finvest_documents_guard()",
    "CREATE TRIGGER documents_no_truncate BEFORE TRUNCATE ON documents "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_documents_guard()",
    "REVOKE ALL ON documents FROM PUBLIC",
    # No DELETE for anyone. The API asks for statements; only the worker builds them.
    f"GRANT SELECT, INSERT ON documents TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, failure_code, finished_at, file_name, content, "
    f"sha256, byte_size) ON documents TO {WORKER_ROLE}",
    "ALTER TABLE documents ENABLE ROW LEVEL SECURITY",
    # A client reads their own documents. Staff read any client's, so support can see what a
    # client was sent. Which staff may is a permission checked in the application.
    f"CREATE POLICY documents_app_read ON documents FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    # A client asks for a statement of their own. Monthly statements and receipts come from
    # the worker.
    f"CREATE POLICY documents_app_insert ON documents FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
    "AND kind = 'requested_statement')",
    f"CREATE POLICY documents_worker ON documents TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
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
    IF EXISTS (SELECT FROM documents) THEN
        RAISE EXCEPTION 'refusing to drop documents that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # What a client was given in writing is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE documents")
    op.execute("DROP FUNCTION finvest_documents_guard()")
