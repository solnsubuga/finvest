"""Let one account only prove an email address, so the address can sign a client in.

Revision ID: 0220_identity_email_login
Revises: 0076_migration_carry_resolutions

A client may log in with an email address instead of their mobile number, but only with an
address they proved by its link. Two accounts may still type the same address, but only one can
prove it: the second proof fails with a unique violation, which the API answers `email_taken`.
Changing the address clears the proof (0163), so the index never holds an address an account
no longer has.

Only the link sets `email_verified_at`. Accounts carried over from Finvest 1.0 arrive with it
unset, whatever address the old system held, so the import cannot add duplicate proofs. If two
accounts had already proved the same address before this runs, creating the index fails and the
upgrade stops: clear one of the two proofs by hand, and that client proves their address again.
Production has no clients yet, so this can only happen in a test or a copy of the database.
"""

from alembic import op

revision = "0220_identity_email_login"
down_revision = "0076_migration_carry_resolutions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_identity_clients_proved_email ON identity_clients (email) "
        "WHERE email_verified_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_identity_clients_proved_email")
