"""Record when a member of staff last confirmed it is them, so approvals can ask again.

Revision ID: 0165_identity_session_reverify
Revises: 0065_investment_carried_over

Approving another person's change needs a passkey or authenticator app code from the last few
minutes. Signing in counts. `reverified_at` is the time of a later confirmation on the same
session. A passkey challenge may now be spent for that purpose.
"""

from alembic import op

revision = "0165_identity_session_reverify"
down_revision = "0065_investment_carried_over"
branch_labels = None
depends_on = None

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_sessions WHERE reverified_at IS NOT NULL)
        OR EXISTS (SELECT FROM identity_passkey_challenges WHERE purpose = 'reverify') THEN
        RAISE EXCEPTION 'refusing to drop staff re-confirmations';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("ALTER TABLE identity_sessions ADD COLUMN reverified_at timestamptz")
    op.execute(
        "ALTER TABLE identity_sessions ADD CONSTRAINT ck_identity_sessions_reverified "
        "CHECK (reverified_at IS NULL OR (actor_kind = 'staff' AND reverified_at >= created_at))"
    )
    op.execute(
        "ALTER TABLE identity_passkey_challenges "
        "DROP CONSTRAINT ck_identity_passkey_challenges_purpose"
    )
    op.execute(
        "ALTER TABLE identity_passkey_challenges ADD CONSTRAINT "
        "ck_identity_passkey_challenges_purpose CHECK (purpose IN ('register', 'sign_in', "
        "'reverify'))"
    )


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(
        "ALTER TABLE identity_passkey_challenges "
        "DROP CONSTRAINT ck_identity_passkey_challenges_purpose"
    )
    op.execute(
        "ALTER TABLE identity_passkey_challenges ADD CONSTRAINT "
        "ck_identity_passkey_challenges_purpose CHECK (purpose IN ('register', 'sign_in'))"
    )
    op.execute("ALTER TABLE identity_sessions DROP CONSTRAINT ck_identity_sessions_reverified")
    op.execute("ALTER TABLE identity_sessions DROP COLUMN reverified_at")
