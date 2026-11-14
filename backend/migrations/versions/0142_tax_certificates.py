"""Let the documents table hold a client's yearly withholding tax certificate.

Revision ID: 0142_tax_certificates
Revises: 0170_tax_remittances

A tax certificate covers Uganda's tax year, 1 July to 30 June, and is keyed by the year's
label (`2026-27`). Like a monthly statement it is asked for by the worker, never by a client,
and a year in which the client earned nothing is recorded as `empty` and never shown or sent.
The figures come from the tax module, from the ledger. See docs/documents.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0142_tax_certificates"
down_revision: str | None = "0170_tax_remittances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_kind",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_kind CHECK (kind IN "
    "('monthly_statement', 'requested_statement', 'receipt', 'tax_certificate'))",
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_empty",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_empty CHECK (status <> 'empty' "
    "OR kind IN ('monthly_statement', 'tax_certificate'))",
    # A certificate covers exactly one tax year, from 1 July, and is keyed by its label.
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_tax_year CHECK ("
    "kind <> 'tax_certificate' OR ("
    "extract(month FROM period_start) = 7 AND extract(day FROM period_start) = 1 "
    "AND period_end = (period_start + interval '1 year' - interval '1 day')::date "
    "AND idempotency_key = to_char(period_start, 'YYYY') || '-' "
    "|| to_char(period_end, 'YY')))",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM documents WHERE kind = 'tax_certificate') THEN
        RAISE EXCEPTION 'refusing to drop tax certificates that were issued';
    END IF;
END
$$
"""

DOWNGRADE = [
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_tax_year",
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_empty",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_empty "
    "CHECK (status <> 'empty' OR kind = 'monthly_statement')",
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_kind",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_kind "
    "CHECK (kind IN ('monthly_statement', 'requested_statement', 'receipt'))",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # What a client was given in writing is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    for statement in DOWNGRADE:
        op.execute(statement)
