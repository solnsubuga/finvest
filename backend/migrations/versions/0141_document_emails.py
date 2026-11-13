"""Add the email that carries each statement and receipt.

Revision ID: 0141_document_emails
Revises: 0140_documents

One row per document that is emailed, owned by the documents module. See docs/documents.md.

A row is written `queued` in the same transaction that finishes the document, with the job
that sends it. The worker then sends the email with the PDF attached, outside any
transaction, and records what happened: `sent`, `skipped` when the client has no email
address to send to, or `failed` with the reason. The address is never stored here: it is
looked up when the email is sent, so a client who changed it gets the email at the new one.
A finished row never changes and no row is ever deleted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0141_document_emails"
down_revision: str | None = "0140_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE document_emails (
    document_id push_id NOT NULL,
    client_id push_id NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'queued',
    failure_code varchar(64),
    provider varchar(32),
    provider_message_id varchar(200),
    queued_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_document_emails PRIMARY KEY (document_id),
    CONSTRAINT fk_document_emails_document FOREIGN KEY (document_id) REFERENCES documents (id),
    CONSTRAINT ck_document_emails_status
        CHECK (status IN ('queued', 'sent', 'skipped', 'failed')),
    CONSTRAINT ck_document_emails_finished CHECK (
        (status = 'queued') = (finished_at IS NULL)
        AND (status IN ('skipped', 'failed')) = (failure_code IS NOT NULL)
        AND (status = 'sent') = (provider IS NOT NULL)),
    CONSTRAINT ck_document_emails_failure_code
        CHECK (failure_code ~ '^[a-z][a-z0-9_]{0,63}$')
);
CREATE INDEX ix_document_emails_client ON document_emails (client_id, queued_at DESC);
-- A receipt is keyed by its deposit's ID, and push IDs can hold an underscore.
ALTER TABLE documents DROP CONSTRAINT ck_documents_idempotency_key;
ALTER TABLE documents ADD CONSTRAINT ck_documents_idempotency_key
    CHECK (idempotency_key ~ '^[A-Za-z0-9._:@_-]{1,200}$')
"""

GUARD = f"""
CREATE FUNCTION public.finvest_document_emails_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'TRUNCATE' THEN
            RAISE EXCEPTION 'document emails are never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system'
            OR current_user <> '{WORKER_ROLE}'
        THEN
            RAISE EXCEPTION 'only the worker sends a document' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'queued' OR NEW.finished_at IS NOT NULL THEN
                RAISE EXCEPTION 'a document email is queued before it is sent'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (
                SELECT FROM public.documents
                WHERE id = NEW.document_id AND client_id = NEW.client_id AND status = 'ready'
            ) THEN
                RAISE EXCEPTION 'only a ready document is emailed, to its own client'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'queued' THEN
            RAISE EXCEPTION 'a finished document email never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.document_id <> OLD.document_id OR NEW.client_id <> OLD.client_id
            OR NEW.queued_at <> OLD.queued_at
        THEN
            RAISE EXCEPTION 'what a document email is for never changes'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_document_emails_guard() FROM PUBLIC",
    "CREATE TRIGGER document_emails_guard BEFORE INSERT OR UPDATE ON document_emails "
    "FOR EACH ROW EXECUTE FUNCTION public.finvest_document_emails_guard()",
    "CREATE TRIGGER document_emails_no_truncate BEFORE TRUNCATE ON document_emails "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_document_emails_guard()",
    "REVOKE ALL ON document_emails FROM PUBLIC",
    # No DELETE for anyone. The API only reads whether a document was emailed.
    f"GRANT SELECT ON document_emails TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, failure_code, provider, provider_message_id, "
    f"finished_at) ON document_emails TO {WORKER_ROLE}",
    "ALTER TABLE document_emails ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY document_emails_app_read ON document_emails FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY document_emails_worker ON document_emails TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
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
    IF EXISTS (SELECT FROM document_emails) THEN
        RAISE EXCEPTION 'refusing to drop document emails that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A record of what was sent to a client is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE document_emails")
    op.execute("DROP FUNCTION public.finvest_document_emails_guard()")
    op.execute("ALTER TABLE documents DROP CONSTRAINT ck_documents_idempotency_key")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT ck_documents_idempotency_key "
        "CHECK (idempotency_key ~ '^[A-Za-z0-9._:@-]{1,200}$')"
    )
