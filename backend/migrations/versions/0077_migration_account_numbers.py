"""Keep each carried-over client's Finvest 1.0 account number, as a bank reference.

Revision ID: 0077_migration_account_numbers
Revises: 0370_kyc_phone_names

Clients of Finvest 1.0 wrote their old account number on bank transfers, and some will go on
doing so after the switch. `migration_carried_clients` gains `legacy_account_number`, filled
from the sheet when the switch carries the client over, so staff matching a statement line can
see that it names the client. Empty for clients carried over before this column, and for those
the sheet gave no number.

The table stays insert-only, so the number is written once with the row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0077_migration_account_numbers"
down_revision: str | None = "0370_kyc_phone_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "migration_carried_clients"

UPGRADE = [
    f"ALTER TABLE {TABLE} ADD COLUMN legacy_account_number varchar(64)",
    f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_{TABLE}_legacy_account_number "
    "CHECK (legacy_account_number IS NULL OR length(btrim(legacy_account_number)) > 0)",
    # The sheet refuses a number used twice, so this only guards a sheet read wrongly.
    f"CREATE UNIQUE INDEX uq_{TABLE}_legacy_account_number ON {TABLE} (legacy_account_number) "
    "WHERE legacy_account_number IS NOT NULL",
]

DOWNGRADE = [
    f"DROP INDEX uq_{TABLE}_legacy_account_number",
    f"ALTER TABLE {TABLE} DROP COLUMN legacy_account_number",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
