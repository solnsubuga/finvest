"""Let two members of staff send a client a statement.

Revision ID: 0353_documents_staff_statements
Revises: 0352_payout_client_limits

`console_client_actions` gains the kind `statement`, with the period in its details. Approving
one asks documents for a `requested_statement` for that client, keyed `staff:<action ID>`, which
the worker builds and emails as it does one the client asks for in the app.

The documents row policy gains one way in for staff: a requested statement whose key names a
`statement` action for the same client approved in this same transaction, so one member of
staff can never send a client a statement alone.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0353_documents_staff_statements"
down_revision: str | None = "0352_payout_client_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"

UPGRADE = [
    "ALTER TABLE console_client_actions DROP CONSTRAINT ck_console_client_actions_kind",
    "ALTER TABLE console_client_actions ADD CONSTRAINT ck_console_client_actions_kind "
    "CHECK (kind IN ('pin_reset', 'withdrawal_limits', 'statement'))",
    # The actor settings are a fence against bugs, not a credential (see migration 0001).
    f"CREATE POLICY documents_staff_statement ON documents FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'staff' AND kind = 'requested_statement' AND EXISTS ("
    "SELECT FROM console_client_actions a WHERE 'staff:' || a.id = idempotency_key "
    "AND a.client_id = documents.client_id AND a.kind = 'statement' "
    "AND a.status = 'approved' AND a.decided_in = pg_current_xact_id()))",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM console_client_actions WHERE kind = 'statement') THEN
        RAISE EXCEPTION 'refusing to drop statements staff sent that hold records';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP POLICY documents_staff_statement ON documents")
    op.execute("ALTER TABLE console_client_actions DROP CONSTRAINT ck_console_client_actions_kind")
    op.execute(
        "ALTER TABLE console_client_actions ADD CONSTRAINT ck_console_client_actions_kind "
        "CHECK (kind IN ('pin_reset', 'withdrawal_limits'))"
    )
