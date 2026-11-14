"""Let the worker ask for a mobile money payment for a saving it set going.

Revision ID: 0300_system_deposits
Revises: 0225_identity_pin_confirmations

Until now only a client saved a deposit, from their own transaction. An automatic investing run
has nobody signed in: on its due day the worker saves the investment and then the deposit that
pays for it, and the send job prompts the client's phone as for any payment. The client still
approves the prompt with their mobile money PIN.

The worker gains INSERT on deposits, fenced by a restrictive policy to the start of a mobile
money deposit for an investment: status `created`, MTN or Airtel, no provider reference and no
ledger entry. It never inserts a confirmed deposit, a bank transfer or a goal payment. Every
later move goes through the update guard of 0020, as before.
"""

from alembic import op

revision = "0300_system_deposits"
down_revision = "0225_identity_pin_confirmations"
branch_labels = None
depends_on = None

WORKER_ROLE = "finvest_worker"

UPGRADE = [
    f"GRANT INSERT ON payment_deposits TO {WORKER_ROLE}",
    # Restrictive: on top of payment_deposits_worker, which lets the worker reach every row.
    "CREATE POLICY payment_deposits_worker_insert ON payment_deposits AS RESTRICTIVE "
    f"FOR INSERT TO {WORKER_ROLE} WITH CHECK (finvest_actor_kind() = 'system' "
    "AND status = 'created' AND method IN ('mtn', 'airtel') AND purpose_type = 'investment' "
    "AND provider_reference IS NULL AND ledger_entry_id IS NULL AND code IS NULL "
    "AND sent_at IS NULL AND settled_at IS NULL)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Deposits the worker saved stay: they are ordinary deposits once saved.
    op.execute("DROP POLICY payment_deposits_worker_insert ON payment_deposits")
    op.execute(f"REVOKE INSERT ON payment_deposits FROM {WORKER_ROLE}")
