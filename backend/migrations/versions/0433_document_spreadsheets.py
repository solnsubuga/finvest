"""Keep an Excel copy of each statement next to its PDF.

Revision ID: 0433_document_spreadsheets
Revises: 0432_document_checks

One row for each statement built since this revision, owned by the documents module. The worker
writes it in the transaction that builds the statement, from the same drawn figures as the PDF,
so the two always agree, and nothing ever changes it. The PDF on `documents` stays the
statement: it is what is emailed, signed and checked. See docs/documents.md, "Excel copies".

A client reads their own rows, staff read any, through the same rule as `documents`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0433_document_spreadsheets"
down_revision: str | None = "0432_document_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

UPGRADE = [
    """
CREATE TABLE document_spreadsheets (
    document_id push_id NOT NULL,
    file_name varchar(200) NOT NULL,
    content bytea NOT NULL,
    sha256 bytea NOT NULL,
    byte_size integer NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_document_spreadsheets PRIMARY KEY (document_id),
    CONSTRAINT fk_document_spreadsheets_document
        FOREIGN KEY (document_id) REFERENCES documents (id),
    CONSTRAINT ck_document_spreadsheets_file_name
        CHECK (file_name ~ '^[A-Za-z0-9 ._-]{1,160}[.]xlsx$'),
    CONSTRAINT ck_document_spreadsheets_sha256 CHECK (octet_length(sha256) = 32),
    CONSTRAINT ck_document_spreadsheets_byte_size CHECK (
        byte_size > 0 AND byte_size = octet_length(content))
)
""",
    """
CREATE FUNCTION public.finvest_document_spreadsheets_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'INSERT' AND public.finvest_actor_kind() = 'system'
            AND EXISTS (
                SELECT FROM public.documents d
                WHERE d.id = NEW.document_id
                    AND d.kind IN ('monthly_statement', 'requested_statement'))
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'an Excel statement is written once, by the worker, and never changes'
            USING ERRCODE = '42501';
    END;
    $$
""",
    "REVOKE ALL ON FUNCTION finvest_document_spreadsheets_guard() FROM PUBLIC",
    "CREATE TRIGGER document_spreadsheets_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON document_spreadsheets "
    "FOR EACH ROW EXECUTE FUNCTION finvest_document_spreadsheets_guard()",
    "CREATE TRIGGER document_spreadsheets_no_truncate BEFORE TRUNCATE ON document_spreadsheets "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_document_spreadsheets_guard()",
    "REVOKE ALL ON document_spreadsheets FROM PUBLIC",
    f"GRANT SELECT ON document_spreadsheets TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON document_spreadsheets TO {WORKER_ROLE}",
    "ALTER TABLE document_spreadsheets ENABLE ROW LEVEL SECURITY",
    # The documents policy decides who sees the document, so a client sees only their own.
    f"CREATE POLICY document_spreadsheets_app_read ON document_spreadsheets FOR SELECT "  # noqa: S608
    f"TO {APP_ROLE} USING (EXISTS (SELECT FROM documents d WHERE d.id = document_id))",
    f"CREATE POLICY document_spreadsheets_worker ON document_spreadsheets TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (finvest_actor_kind() = 'system')",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Each is a copy of a PDF that stays, so nothing a client relies on is lost.
    op.execute("DROP TABLE document_spreadsheets")
    op.execute("DROP FUNCTION finvest_document_spreadsheets_guard()")
