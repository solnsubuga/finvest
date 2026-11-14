"""Hold the one-use confirmations a right app PIN gives for one action.

Revision ID: 0225_identity_pin_confirmations
Revises: 0224_identity_recovery_asks

On the phone app, some actions ask for the PIN again before they go ahead. The client enters it
on `POST /client/pin/confirm`, which charges a wrong try exactly as unlocking does, and a right
PIN gives a confirmation for one named action. The action's own route spends it: once, from the
same session, within a few minutes. Only a SHA-256 digest of the confirmation is kept.
"""

from alembic import op

revision = "0225_identity_pin_confirmations"
down_revision = "0224_identity_recovery_asks"
branch_labels = None
depends_on = None

UPGRADE = [
    """
CREATE TABLE identity_pin_confirmations (
    id push_id NOT NULL,
    session_id push_id NOT NULL,
    action text NOT NULL,
    token_hash bytea NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    CONSTRAINT pk_identity_pin_confirmations PRIMARY KEY (id),
    CONSTRAINT fk_identity_pin_confirmations_session_id_identity_sessions
        FOREIGN KEY (session_id) REFERENCES identity_sessions (id),
    CONSTRAINT uq_identity_pin_confirmations_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_identity_pin_confirmations_action CHECK (action IN ('withdrawal', 'payout_account')),
    CONSTRAINT ck_identity_pin_confirmations_expiry CHECK (expires_at > created_at)
)
""",
    "REVOKE ALL ON identity_pin_confirmations FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE ON identity_pin_confirmations TO finvest_app",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_pin_confirmations WHERE used_at IS NULL AND expires_at > now()) THEN
        RAISE EXCEPTION 'refusing to drop PIN confirmations that are not used yet';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE identity_pin_confirmations")
