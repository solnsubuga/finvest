"""Let other modules ask whether a client is closing their account.

Revision ID: 0233_closure_pending
Revises: 0182_transaction_monitoring

`closure_pending(client_id)` answers true while the client has a request to close that is
waiting (requested or prepared), and once it closed. Investments, payments, goals and automatic
investing call it before they start anything new, so no money comes in while two people check
the account is clear. It reads `closure_requests` for them, so their own roles need no grant on
that table, and the worker can ask too.

It takes a share lock on the request it finds. An approval holds that row for update, so a
start that arrives during an approval waits for it, then sees the account closed and refuses.

See docs/account-closure.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0233_closure_pending"
down_revision: str | None = "0182_transaction_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

PENDING = """
CREATE FUNCTION closure_pending(p_client_id text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    PERFORM 1 FROM public.closure_requests
    WHERE client_id = p_client_id AND status IN ('requested', 'prepared', 'closed')
    FOR SHARE;
    RETURN FOUND;
END
$$
"""


def upgrade() -> None:
    op.execute(PENDING)
    op.execute("REVOKE ALL ON FUNCTION closure_pending(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION closure_pending(text) TO {APP_ROLE}, {WORKER_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION closure_pending(text)")
