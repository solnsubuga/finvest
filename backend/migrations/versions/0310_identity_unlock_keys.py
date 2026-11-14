"""Let a phone open the Finvest app with a fingerprint or face, through its own unlock key.

Revision ID: 0310_identity_unlock_keys
Revises: 0360_investment_product_changes

Turning it on, with the PIN, gives the phone a random key once. The phone keeps it where only a
fingerprint or face can read it, and sends it to open a locked app instead of the PIN. Only its
SHA-256 digest is stored, beside the PIN of the same session, so it works only on that phone and
goes when the session ends, when the PIN is replaced, when it is turned off, or when a wrong key
is sent. The PIN itself never stays on the phone.
"""

from alembic import op

revision = "0310_identity_unlock_keys"
down_revision = "0360_investment_product_changes"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE identity_app_pins ADD COLUMN unlock_key_hash bytea",
    "ALTER TABLE identity_app_pins ADD COLUMN unlock_key_set_at timestamptz",
    """
ALTER TABLE identity_app_pins ADD CONSTRAINT ck_identity_app_pins_unlock_key
    CHECK ((unlock_key_hash IS NULL) = (unlock_key_set_at IS NULL))
""",
]

IN_USE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_app_pins WHERE unlock_key_hash IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop unlock keys that are in use';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(IN_USE_GUARD)
    op.execute("ALTER TABLE identity_app_pins DROP CONSTRAINT ck_identity_app_pins_unlock_key")
    op.execute("ALTER TABLE identity_app_pins DROP COLUMN unlock_key_set_at")
    op.execute("ALTER TABLE identity_app_pins DROP COLUMN unlock_key_hash")
