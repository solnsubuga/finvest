"""Record what Africa's Talking says happened to each text message.

Revision ID: 0440_sms_delivery_reports
Revises: 0433_document_spreadsheets

Owned by the notifications module. See "SMS delivery reports" in docs/notifications.md.

* `notification_sms_reports` holds each delivery report once per message and status, so a
  report Africa's Talking sends again is recorded and handled only once. It keeps the message
  id, the status and, for a failure, Africa's Talking's one-word reason. Never the phone number.
* `notification_messages` gains `delivery_reason`, the reason a text was not delivered. The
  `delivery` and `delivery_at` columns from 0092 now hold a text's delivery as well.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0440_sms_delivery_reports"
down_revision: str | None = "0433_document_spreadsheets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

DELIVERIES = "'accepted', 'delayed', 'delivered', 'failed'"
REASON = "^[A-Za-z]{1,40}$"

TABLES = f"""
CREATE TABLE notification_sms_reports (
    provider varchar(16) COLLATE "C" NOT NULL,
    provider_message_id varchar(100) COLLATE "C" NOT NULL,
    status varchar(16) COLLATE "C" NOT NULL,
    delivery varchar(16) NOT NULL,
    reason varchar(40) COLLATE "C",
    received_at timestamptz NOT NULL,
    processed_at timestamptz,
    outcome varchar(16),
    CONSTRAINT pk_notification_sms_reports
        PRIMARY KEY (provider, provider_message_id, status),
    CONSTRAINT ck_notification_sms_reports_provider CHECK (provider IN ('africastalking')),
    CONSTRAINT ck_notification_sms_reports_provider_message_id
        CHECK (provider_message_id ~ '^[A-Za-z0-9_-]{{1,100}}$'),
    CONSTRAINT ck_notification_sms_reports_status CHECK (status ~ '^[A-Za-z]{{1,16}}$'),
    CONSTRAINT ck_notification_sms_reports_delivery CHECK (delivery IN ({DELIVERIES})),
    CONSTRAINT ck_notification_sms_reports_reason
        CHECK (reason IS NULL OR (reason ~ '{REASON}' AND delivery = 'failed')),
    CONSTRAINT ck_notification_sms_reports_outcome
        CHECK ((processed_at IS NULL) = (outcome IS NULL)
            AND outcome IN ('applied', 'unchanged', 'unmatched'))
);
ALTER TABLE notification_messages
    ADD COLUMN delivery_reason varchar(40) COLLATE "C",
    ADD CONSTRAINT ck_notification_messages_delivery_reason
        CHECK (delivery_reason IS NULL OR (delivery_reason ~ '{REASON}' AND delivery = 'failed'))
"""

# A report arrives with nobody signed in. The API records a report and cannot read one back,
# so it learns of a repeat only from the insert doing nothing. The worker applies reports.
# Nobody deletes anything.
RULES = [
    "ALTER TABLE notification_sms_reports ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON notification_sms_reports FROM PUBLIC",
    f"GRANT INSERT ON notification_sms_reports TO {APP_ROLE}",
    f"GRANT SELECT ON notification_sms_reports TO {WORKER_ROLE}",
    f"GRANT UPDATE (processed_at, outcome) ON notification_sms_reports TO {WORKER_ROLE}",
    "CREATE POLICY notification_sms_reports_app_insert ON notification_sms_reports "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK (processed_at IS NULL)",
    "CREATE POLICY notification_sms_reports_worker ON notification_sms_reports "
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
    IF EXISTS (SELECT FROM notification_sms_reports)
        OR EXISTS (SELECT FROM notification_messages WHERE delivery_reason IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop SMS delivery reports';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Which texts reached the phone, and why one did not, is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute(
        "ALTER TABLE notification_messages "
        "DROP CONSTRAINT ck_notification_messages_delivery_reason, DROP COLUMN delivery_reason"
    )
    op.execute("DROP TABLE notification_sms_reports")
