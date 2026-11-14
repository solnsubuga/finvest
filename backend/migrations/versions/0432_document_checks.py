"""Add the code printed on each statement, which anyone can check on the website.

Revision ID: 0432_document_checks
Revises: 0431_payment_receipt_reuse

One row for each statement built since this revision, owned by the documents module. The worker
writes it in the transaction that builds the statement, so the code on the PDF and the row
always agree, and nothing ever changes it. See docs/documents.md, "Checking a statement".

A bank or embassy enters the code at finvestug.com/check. The website asks the API, which reads
the row through `documents_check`, a function that answers one exact code with only what the
check page shows: what kind of statement, the period, the day it was issued, the last four
digits of the account number and the closing value in each currency. No role reads the table
itself except the worker that writes it, so the API cannot list codes. The client's name is not
kept here: it is personal data, and the bank already holds it on the statement.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0432_document_checks"
down_revision: str | None = "0431_payment_receipt_reuse"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

UPGRADE = [
    """
CREATE TABLE document_checks (
    document_id push_id NOT NULL,
    code char(12) COLLATE "C" NOT NULL,
    account_ending char(4) COLLATE "C" NOT NULL,
    -- What the statement says the client held at the end of the period, one entry for each
    -- currency in the order printed: [{"currency": "UGX", "minor": 1250000}].
    closing jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_document_checks PRIMARY KEY (document_id),
    CONSTRAINT fk_document_checks_document FOREIGN KEY (document_id) REFERENCES documents (id),
    CONSTRAINT uq_document_checks_code UNIQUE (code),
    CONSTRAINT ck_document_checks_code CHECK (code ~ '^[0-9A-HJKMNP-TV-Z]{12}$'),
    CONSTRAINT ck_document_checks_account_ending CHECK (account_ending ~ '^[0-9]{4}$'),
    CONSTRAINT ck_document_checks_closing CHECK (
        jsonb_typeof(closing) = 'array' AND jsonb_array_length(closing) BETWEEN 1 AND 4)
)
""",
    """
CREATE FUNCTION public.finvest_document_checks_guard() RETURNS trigger
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
        RAISE EXCEPTION 'a statement check is written once, by the worker, and never changes'
            USING ERRCODE = '42501';
    END;
    $$
""",
    "REVOKE ALL ON FUNCTION finvest_document_checks_guard() FROM PUBLIC",
    "CREATE TRIGGER document_checks_guard BEFORE INSERT OR UPDATE OR DELETE ON document_checks "
    "FOR EACH ROW EXECUTE FUNCTION finvest_document_checks_guard()",
    "CREATE TRIGGER document_checks_no_truncate BEFORE TRUNCATE ON document_checks "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_document_checks_guard()",
    "REVOKE ALL ON document_checks FROM PUBLIC",
    f"GRANT SELECT, INSERT ON document_checks TO {WORKER_ROLE}",
    "ALTER TABLE document_checks ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY document_checks_worker ON document_checks TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (finvest_actor_kind() = 'system')",
    # The one way the API reads the table: by an exact code, a finished statement only. It
    # runs as the table owner and pins its search path.
    """
CREATE FUNCTION documents_check(p_code text) RETURNS TABLE (
    kind varchar, period_start date, period_end date, issued_at timestamptz,
    account_ending char(4), closing jsonb
)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    SELECT d.kind, d.period_start, d.period_end, d.finished_at, c.account_ending, c.closing
    FROM public.document_checks c JOIN public.documents d ON d.id = c.document_id
    WHERE c.code = p_code AND d.status = 'ready'
    $$
""",
    "REVOKE ALL ON FUNCTION documents_check(text) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION documents_check(text) TO {APP_ROLE}",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM document_checks) THEN
        RAISE EXCEPTION 'refusing to drop statement checks that banks may still use';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # A code printed on a statement keeps working. A downgrade never erases one.
    op.execute(EMPTY_GUARD)
    op.execute("DROP FUNCTION documents_check(text)")
    op.execute("DROP TABLE document_checks")
    op.execute("DROP FUNCTION finvest_document_checks_guard()")
