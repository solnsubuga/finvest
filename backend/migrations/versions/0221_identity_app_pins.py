"""Hold the app PIN that opens the Finvest app on one phone.

Revision ID: 0221_identity_app_pins
Revises: 0220_identity_email_login

A PIN belongs to one session of the phone app, so it works only on that phone and goes when the
session ends. Only an Argon2id hash is stored. `wrong_tries` counts wrong PINs in a row and is
charged before the PIN is checked, so parallel guesses cannot get past the limit: at five the API
ends the session. A browser session never has a PIN.
"""

from alembic import op

revision = "0221_identity_app_pins"
down_revision = "0182_transaction_monitoring"
branch_labels = None
depends_on = None

UPGRADE = [
    """
CREATE TABLE identity_app_pins (
    session_id push_id NOT NULL,
    pin_hash text NOT NULL,
    wrong_tries integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_identity_app_pins PRIMARY KEY (session_id),
    CONSTRAINT fk_identity_app_pins_session_id_identity_sessions
        FOREIGN KEY (session_id) REFERENCES identity_sessions (id),
    CONSTRAINT ck_identity_app_pins_wrong_tries CHECK (wrong_tries BETWEEN 0 AND 5)
)
""",
    "REVOKE ALL ON identity_app_pins FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE ON identity_app_pins TO finvest_app",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_app_pins) THEN
        RAISE EXCEPTION 'refusing to drop app PINs that are in use';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE identity_app_pins")
