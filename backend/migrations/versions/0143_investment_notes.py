"""Let the documents table hold the note that confirms an investment started.

Revision ID: 0143_investment_notes
Revises: 0225_identity_pin_confirmations

An investment note is asked for by the worker when an investment starts, never by a client.
It names the investment as its source and is keyed `started:<investment id>`, so each
investment gets one. Its figures are after tax and come from the investments module. See
docs/documents.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0143_investment_notes"
down_revision: str | None = "0225_identity_pin_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_kind",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_kind CHECK (kind IN "
    "('monthly_statement', 'requested_statement', 'receipt', 'tax_certificate', "
    "'investment_note'))",
    # A receipt or an investment note is for one thing, its source. A statement or a
    # certificate covers a period of at most 366 days.
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_shape",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_shape CHECK ("
    "(kind IN ('receipt', 'investment_note') AND source_id IS NOT NULL "
    "AND period_start IS NULL AND period_end IS NULL) "
    "OR (kind NOT IN ('receipt', 'investment_note') AND source_id IS NULL "
    "AND period_start IS NOT NULL AND period_end IS NOT NULL "
    "AND period_start <= period_end AND period_end - period_start < 366))",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_investment_note CHECK ("
    "kind <> 'investment_note' OR idempotency_key = 'started:' || source_id)",
    "CREATE UNIQUE INDEX uq_documents_investment_note ON documents (idempotency_key) "
    "WHERE kind = 'investment_note'",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM documents WHERE kind = 'investment_note') THEN
        RAISE EXCEPTION 'refusing to drop investment notes that were issued';
    END IF;
END
$$
"""

DOWNGRADE = [
    "DROP INDEX uq_documents_investment_note",
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_investment_note",
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_shape",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_shape CHECK ("
    "(kind = 'receipt' AND source_id IS NOT NULL "
    "AND period_start IS NULL AND period_end IS NULL) "
    "OR (kind <> 'receipt' AND source_id IS NULL "
    "AND period_start IS NOT NULL AND period_end IS NOT NULL "
    "AND period_start <= period_end AND period_end - period_start < 366))",
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_kind",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_kind CHECK (kind IN "
    "('monthly_statement', 'requested_statement', 'receipt', 'tax_certificate'))",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # What a client was given in writing is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    for statement in DOWNGRADE:
        op.execute(statement)
