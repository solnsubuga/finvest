"""Reconcile the Pesapal balance as a pot of its own, and let it settle to the trust account.

Revision ID: 0481_pesapal_pot
Revises: 0480_card_deposits

Card payments are paid into the ledger account `pesapal.balance` (0480). Pesapal holds that
money until it settles to the trust bank account, keeping its fee. This lets a Pesapal
statement be imported and closed each day like ioTec's and the bank's, and lets staff record a
settlement as a pot transfer from Pesapal to the bank, with Pesapal's fee as an expense. Nothing
else about statements, the close of day or transfers changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0481_pesapal_pot"
down_revision: str | None = "0480_card_deposits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "'iotec', 'bank', 'custodian'"
NEW = "'iotec', 'pesapal', 'bank', 'custodian'"
OLD_CASH = "'iotec', 'bank'"
NEW_CASH = "'iotec', 'pesapal', 'bank'"


def _checks(sources: str, cash: str) -> list[str]:
    return [
        "ALTER TABLE reconciliation_statements DROP CONSTRAINT ck_reconciliation_statements_source",
        "ALTER TABLE reconciliation_statements ADD CONSTRAINT ck_reconciliation_statements_source "
        f"CHECK (source IN ({sources}))",
        "ALTER TABLE reconciliation_movements DROP CONSTRAINT ck_reconciliation_movements_source",
        "ALTER TABLE reconciliation_movements ADD CONSTRAINT ck_reconciliation_movements_source "
        f"CHECK (source IN ({sources}))",
        "ALTER TABLE reconciliation_pot_transfers "
        "DROP CONSTRAINT ck_reconciliation_pot_transfers_sources",
        "ALTER TABLE reconciliation_pot_transfers "
        "ADD CONSTRAINT ck_reconciliation_pot_transfers_sources "
        f"CHECK (from_source IN ({cash}) AND to_source IN ({cash}))",
    ]


def upgrade() -> None:
    for statement in _checks(NEW, NEW_CASH):
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM reconciliation_statements WHERE source = 'pesapal')
       OR EXISTS (SELECT FROM reconciliation_movements WHERE source = 'pesapal')
       OR EXISTS (SELECT FROM reconciliation_pot_transfers
                   WHERE 'pesapal' IN (from_source, to_source)) THEN
        RAISE EXCEPTION 'refusing to remove the Pesapal pot while it holds statements or transfers';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in _checks(OLD, OLD_CASH):
        op.execute(statement)
