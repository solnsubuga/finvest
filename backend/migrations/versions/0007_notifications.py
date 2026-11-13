"""Create the queue of outgoing email and text messages. Let clients add an authenticator app."""

from alembic import op

revision = "0007_notifications"
down_revision = "0015_ledger_closed_day_guard"
branch_labels = None
depends_on = None

# The notifications module owns this table. A request queues a row and a job in its own
# transaction, and the worker sends it by email or SMS. `variables` can hold a one-time code, so it is emptied
# the moment the message is sent or given up on. The address or number is removed after 30 days.
TABLES = """
CREATE TABLE notification_messages (
    id push_id NOT NULL,
    idempotency_key text NOT NULL,
    channel text NOT NULL,
    recipient text,
    template text NOT NULL,
    variables jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    provider text,
    provider_message_id text,
    failure_code text,
    created_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_notification_messages PRIMARY KEY (id),
    CONSTRAINT uq_notification_messages_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT ck_notification_messages_idempotency_key
        CHECK (idempotency_key ~ '^[A-Za-z0-9._:@-]{1,200}$'),
    CONSTRAINT ck_notification_messages_channel CHECK (channel IN ('email', 'sms')),
    CONSTRAINT ck_notification_messages_recipient CHECK (length(recipient) <= 254),
    CONSTRAINT ck_notification_messages_template CHECK (template ~ '^[a-z_]{1,64}$'),
    CONSTRAINT ck_notification_messages_variables CHECK (jsonb_typeof(variables) = 'object'),
    CONSTRAINT ck_notification_messages_status CHECK (status IN ('queued', 'sent', 'failed')),
    CONSTRAINT ck_notification_messages_finished
        CHECK ((status = 'queued') = (finished_at IS NULL)),
    -- Once a message is finished its content is gone.
    CONSTRAINT ck_notification_messages_emptied
        CHECK (status = 'queued' OR variables = '{}'::jsonb)
);
CREATE INDEX ix_notification_messages_created_at ON notification_messages (created_at)
"""

# The API can queue a message and can never read one back. The worker reads and updates, and
# nobody deletes.
GRANTS = [
    "REVOKE ALL ON notification_messages FROM PUBLIC",
    "GRANT INSERT ON notification_messages TO finvest_app",
    "GRANT SELECT (idempotency_key) ON notification_messages TO finvest_app",
    "GRANT SELECT, INSERT, UPDATE ON notification_messages TO finvest_worker",
]


# The owner decided clients may use an authenticator app too. 0006 allowed it for staff only.
AUTHENTICATOR_KINDS = (
    "ALTER TABLE identity_authenticators DROP CONSTRAINT ck_identity_authenticators_actor_kind, "
    "ADD CONSTRAINT ck_identity_authenticators_actor_kind CHECK (actor_kind IN ({kinds}))"
)

# Queued messages are a record of what was sent to whom, and a client's authenticator app is
# their second step. A downgrade must not erase either.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM notification_messages) THEN
        RAISE EXCEPTION 'refusing to drop messages that were queued or sent';
    END IF;
    IF EXISTS (SELECT FROM identity_authenticators WHERE actor_kind = 'client') THEN
        RAISE EXCEPTION 'refusing to drop authenticator apps that clients use';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    for statement in GRANTS:
        op.execute(statement)
    op.execute(AUTHENTICATOR_KINDS.format(kinds="'client', 'staff'"))


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(AUTHENTICATOR_KINDS.format(kinds="'staff'"))
    op.execute("DROP TABLE notification_messages")
