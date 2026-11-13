"""Let a one-time code prove that a client holds a new payout account.

Revision ID: 0034_payout_account_codes
Revises: 0152_client_locks

* `identity_codes` takes the purpose `payout_account`, beside every purpose it already had. Such a code belongs to one client
  (`actor_id`), one payout account (`bound_to`) and the number it was texted to (`phone`).
* The identity module issues and spends the code; payouts moves the account on only in the
  transaction that spent it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0034_payout_account_codes"
down_revision: str | None = "0152_client_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    "ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_purpose",
    "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_purpose "
    "CHECK (purpose IN ('signup', 'login', 'password_reset', 'claim', 'payout_account'))",
    "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_payout_account CHECK ("
    "purpose <> 'payout_account' OR "
    "(actor_id IS NOT NULL AND bound_to IS NOT NULL AND phone IS NOT NULL))",
    # The daily ceiling counts one client's payout account codes.
    "CREATE INDEX ix_identity_codes_payout_account ON identity_codes (actor_id, created_at) "
    "WHERE purpose = 'payout_account'",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_codes WHERE purpose = 'payout_account') THEN
        RAISE EXCEPTION 'refusing to drop payout account codes';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP INDEX ix_identity_codes_payout_account")
    op.execute("ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_payout_account")
    op.execute("ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_purpose")
    op.execute(
        "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_purpose "
        "CHECK (purpose IN ('signup', 'login', 'password_reset', 'claim'))"
    )
