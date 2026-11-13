"""Index the audit trail by action, for the console's filtered view and export.

Revision ID: 0151_audit_search
Revises: 0072_migration_claims

The trail was indexed by time, record, actor and request (migration 0012). The console also
filters by action, and lists every action on record for its filter.
"""

from alembic import op

revision = "0151_audit_search"
down_revision = "0072_migration_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_audit_events_action ON audit_events (action, occurred_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_audit_events_action")
