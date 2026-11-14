"""Let a client find another approved client to send an investment to.

Revision ID: 0240_kyc_transfer_recipients
Revises: 0225_identity_pin_confirmations

A client's row policies show them only their own identity check, so finding someone else by
account number needs a narrow way through. `finvest_kyc_transfer_recipient` answers, for one
account number or one client ID, only whether that client is approved and the names on their
document, from which the service shows the first given name and the surname's initial. Nothing
else of the check is reachable this way: no document, date of birth or photo.

Runs as the table owner, pins its search path and names tables with their schema.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0240_kyc_transfer_recipients"
down_revision: str | None = "0225_identity_pin_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
SIGNATURE = "public.finvest_kyc_transfer_recipient(text, text)"

FUNCTION = """
CREATE FUNCTION public.finvest_kyc_transfer_recipient(by_number text, by_client text)
    RETURNS TABLE (client_id text, account_number text, surname text, given_names text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT c.client_id::text, c.account_number::text, c.surname::text, c.given_names::text
          FROM public.kyc_checks AS c
         WHERE c.status = 'approved'
           AND public.finvest_actor_kind() IN ('client', 'staff', 'system')
           AND ((by_number IS NOT NULL AND c.account_number = by_number)
             OR (by_client IS NOT NULL AND c.client_id = by_client))
         LIMIT 1
    $$
"""


def upgrade() -> None:
    op.execute(FUNCTION)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {APP_ROLE}, {WORKER_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {SIGNATURE}")
