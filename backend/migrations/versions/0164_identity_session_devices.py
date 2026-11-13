"""Remember which kind of device each session was opened on.

Revision ID: 0164_identity_session_devices
Revises: 0163_identity_email_fence

A fixed label such as "Chrome on Android", never the raw User-Agent. People see it in their
list of signed-in devices, and a sign-in from a device they have not used before is emailed.
"""

from alembic import op

revision = "0164_identity_session_devices"
down_revision = "0163_identity_email_fence"
branch_labels = None
depends_on = None

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_sessions WHERE device IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the devices sessions were opened on';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("ALTER TABLE identity_sessions ADD COLUMN device text")
    op.execute(
        "ALTER TABLE identity_sessions ADD CONSTRAINT ck_identity_sessions_device "
        "CHECK (device IS NULL OR length(device) BETWEEN 1 AND 64)"
    )


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("ALTER TABLE identity_sessions DROP CONSTRAINT ck_identity_sessions_device")
    op.execute("ALTER TABLE identity_sessions DROP COLUMN device")
