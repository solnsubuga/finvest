"""Let an investment have a second note, the one that confirms it ended.

Revision ID: 0144_ended_investment_notes
Revises: 0143_investment_notes

An investment note is keyed `started:<investment id>` when the investment starts, and now also
`ended:<investment id>` when it matures, rolls over or is withdrawn early. An investment ends
once, so it has at most these two. See docs/documents.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0144_ended_investment_notes"
down_revision: str | None = "0143_investment_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_investment_note",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_investment_note CHECK ("
    "kind <> 'investment_note' OR idempotency_key IN "
    "('started:' || source_id, 'ended:' || source_id))",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM documents WHERE kind = 'investment_note' AND idempotency_key LIKE 'ended:%'
    ) THEN
        RAISE EXCEPTION 'refusing to drop investment notes that were issued';
    END IF;
END
$$
"""

DOWNGRADE = [
    "ALTER TABLE documents DROP CONSTRAINT ck_documents_investment_note",
    "ALTER TABLE documents ADD CONSTRAINT ck_documents_investment_note CHECK ("
    "kind <> 'investment_note' OR idempotency_key = 'started:' || source_id)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # What a client was given in writing is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    for statement in DOWNGRADE:
        op.execute(statement)
