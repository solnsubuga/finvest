"""Record what Resend says happened to each email, and stop mailing addresses that refuse it.

Revision ID: 0092_email_delivery
Revises: 0041_reconciliation_day_close

Owned by the notifications module. See "Email delivery events" in docs/notifications.md.

* `notification_email_events` holds each signed webhook event once, keyed by its `svix-id`, so
  a retried delivery of the same event is recorded and handled only once. It keeps the email's
  id, what happened, and the recipients as sha256 hashes. Never an address, never the subject.
* `notification_email_suppressions` lists addresses, as the same hashes, that bounced for good
  or marked our mail as spam. The worker does not send to them.
* `notification_messages` gains the last delivery state Resend reported for each message.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0092_email_delivery"
down_revision: str | None = "0041_reconciliation_day_close"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

DELIVERIES = "'accepted', 'delayed', 'delivered', 'failed', 'bounced', 'complained'"
HASH = "^[0-9a-f]{64}$"

TABLES = f"""
CREATE TABLE notification_email_events (
    svix_id varchar(100) COLLATE "C" NOT NULL,
    delivery varchar(16) NOT NULL,
    email_id varchar(100) COLLATE "C" NOT NULL,
    recipients text[] NOT NULL,
    permanent boolean NOT NULL,
    occurred_at timestamptz,
    received_at timestamptz NOT NULL,
    processed_at timestamptz,
    outcome varchar(16),
    CONSTRAINT pk_notification_email_events PRIMARY KEY (svix_id),
    CONSTRAINT ck_notification_email_events_svix_id CHECK (svix_id ~ '^[A-Za-z0-9_-]{{1,100}}$'),
    CONSTRAINT ck_notification_email_events_delivery CHECK (delivery IN ({DELIVERIES})),
    CONSTRAINT ck_notification_email_events_email_id CHECK (email_id ~ '^[A-Za-z0-9-]{{1,100}}$'),
    CONSTRAINT ck_notification_email_events_recipients CHECK (
        cardinality(recipients) <= 50
        AND array_to_string(recipients, ',') ~ '^([0-9a-f]{{64}}(,[0-9a-f]{{64}})*)?$'),
    CONSTRAINT ck_notification_email_events_outcome
        CHECK ((processed_at IS NULL) = (outcome IS NULL)
            AND outcome IN ('applied', 'unchanged', 'unmatched'))
);
CREATE INDEX ix_notification_email_events_email_id ON notification_email_events (email_id);
CREATE TABLE notification_email_suppressions (
    address_hash char(64) COLLATE "C" NOT NULL,
    reason varchar(16) NOT NULL,
    svix_id varchar(100) COLLATE "C" NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_notification_email_suppressions PRIMARY KEY (address_hash),
    CONSTRAINT ck_notification_email_suppressions_address_hash CHECK (address_hash ~ '{HASH}'),
    CONSTRAINT ck_notification_email_suppressions_reason
        CHECK (reason IN ('bounced', 'complained')),
    CONSTRAINT fk_notification_email_suppressions_svix_id
        FOREIGN KEY (svix_id) REFERENCES notification_email_events (svix_id)
);
ALTER TABLE notification_messages
    ADD COLUMN delivery varchar(16),
    ADD COLUMN delivery_at timestamptz,
    ADD CONSTRAINT ck_notification_messages_delivery CHECK (delivery IN ({DELIVERIES})),
    ADD CONSTRAINT ck_notification_messages_delivery_at
        CHECK ((delivery IS NULL) = (delivery_at IS NULL));
CREATE INDEX ix_notification_messages_provider_message_id
    ON notification_messages (provider, provider_message_id)
    WHERE provider_message_id IS NOT NULL
"""

# A webhook arrives with nobody signed in. The API records an event and cannot read one back,
# so it learns of a repeat only from the insert doing nothing. The worker applies events and
# keeps the suppression list. Nobody updates a suppression or deletes anything.
RULES = [
    *(
        statement
        for table in ("notification_email_events", "notification_email_suppressions")
        for statement in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {table} FROM PUBLIC",
        )
    ),
    f"GRANT INSERT ON notification_email_events TO {APP_ROLE}",
    f"GRANT SELECT ON notification_email_events TO {WORKER_ROLE}",
    f"GRANT UPDATE (processed_at, outcome) ON notification_email_events TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT ON notification_email_suppressions TO {WORKER_ROLE}",
    "CREATE POLICY notification_email_events_app_insert ON notification_email_events "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK (processed_at IS NULL)",
    "CREATE POLICY notification_email_events_worker ON notification_email_events "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
    "CREATE POLICY notification_email_suppressions_worker ON notification_email_suppressions "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM notification_email_events)
        OR EXISTS (SELECT FROM notification_messages WHERE delivery IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop email delivery records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Which addresses refused our mail, and why, is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP INDEX ix_notification_messages_provider_message_id")
    op.execute(
        "ALTER TABLE notification_messages DROP CONSTRAINT ck_notification_messages_delivery_at, "
        "DROP CONSTRAINT ck_notification_messages_delivery, DROP COLUMN delivery_at, "
        "DROP COLUMN delivery"
    )
    op.execute("DROP TABLE notification_email_suppressions")
    op.execute("DROP TABLE notification_email_events")
