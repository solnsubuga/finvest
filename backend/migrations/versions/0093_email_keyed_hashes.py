"""Keyed hashes of email addresses, written beside the plain ones.

Revision ID: 0093_email_keyed_hashes
Revises: 0164_identity_session_devices

The first of three steps (docs/notifications.md#how-addresses-are-hashed). The suppression list,
its lifts and Resend's delivery events keep a plain sha256 of each address. This adds a column
for the keyed hash, HMAC-SHA256 under a server key over the plain hash, next to each one. New
rows fill both; every read still uses the plain one, so the release still running is unaffected.
The next step fills the keyed column for older rows and moves the reads to it; the last drops the
plain columns.

Each new column is nullable and holds the same shape as the column beside it. Nobody updates a
suppression or an event's recipients, and the lifts guard already refuses any change to a lift
but its decision, so the existing grants cover the new columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0093_email_keyed_hashes"
down_revision: str | None = "0164_identity_session_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
zero_downtime = True

HASH = "^[0-9a-f]{64}$"
HASHES = "^([0-9a-f]{64}(,[0-9a-f]{64})*)?$"

UPGRADE = [
    "ALTER TABLE notification_email_events ADD COLUMN recipients_keyed text[]",
    "ALTER TABLE notification_email_events ADD CONSTRAINT "
    "ck_notification_email_events_recipients_keyed CHECK (recipients_keyed IS NULL OR ("
    "cardinality(recipients_keyed) = cardinality(recipients) "
    f"AND array_to_string(recipients_keyed, ',') ~ '{HASHES}'))",
    "CREATE INDEX ix_notification_email_events_recipients_keyed "
    "ON notification_email_events USING gin (recipients_keyed)",
    'ALTER TABLE notification_email_suppressions ADD COLUMN address_key char(64) COLLATE "C"',
    "ALTER TABLE notification_email_suppressions ADD CONSTRAINT "
    f"ck_notification_email_suppressions_address_key CHECK (address_key ~ '{HASH}')",
    "CREATE UNIQUE INDEX uq_notification_email_suppressions_address_key "
    "ON notification_email_suppressions (address_key)",
    'ALTER TABLE notification_email_suppression_lifts ADD COLUMN address_key char(64) COLLATE "C"',
    "ALTER TABLE notification_email_suppression_lifts ADD CONSTRAINT "
    f"ck_notification_email_suppression_lifts_address_key CHECK (address_key ~ '{HASH}')",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # A keyed hash is worked out again from the plain one beside it, so nothing is lost.
    op.execute("ALTER TABLE notification_email_suppression_lifts DROP COLUMN address_key")
    op.execute("ALTER TABLE notification_email_suppressions DROP COLUMN address_key")
    op.execute("ALTER TABLE notification_email_events DROP COLUMN recipients_keyed")
