"""Add the devices a client's push notifications go to.

Revision ID: 0091_push_devices
Revises: 0033_payout_send_attempts

One table, owned by the notifications module. See docs/notifications.md.

`push_devices` holds the Expo push token of each phone where a client is signed in to the app.
The app registers its token after sign-in and gives it up at sign-out. The worker reads the
tokens to send a push, and stops using one Expo reports is no longer registered.

A token can be registered by more than one client, when two people share a phone. Only the
latest client to register it is sent anything, so one person's alerts never show on a phone
someone else has since signed in to.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0091_push_devices"
down_revision: str | None = "0033_payout_send_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE push_devices (
    id text PRIMARY KEY,
    client_id text NOT NULL,
    token text NOT NULL,
    platform text NOT NULL,
    registered_at timestamptz NOT NULL,
    removed_at timestamptz,
    -- Why it is no longer used: the client signed out, or Expo said the app is gone.
    removed_reason text,
    CONSTRAINT uq_push_devices_client_token UNIQUE (client_id, token),
    CONSTRAINT ck_push_devices_token CHECK (token ~ '^Expo(nent)?PushToken\\[[A-Za-z0-9_-]{10,200}\\]$'),
    CONSTRAINT ck_push_devices_platform CHECK (platform IN ('ios', 'android')),
    CONSTRAINT ck_push_devices_removed CHECK (
        (removed_at IS NULL) = (removed_reason IS NULL)
        AND (removed_reason IS NULL OR removed_reason IN ('signed_out', 'not_registered'))
    )
);
CREATE INDEX ix_push_devices_token ON push_devices (token, registered_at DESC);
CREATE INDEX ix_push_devices_client ON push_devices (client_id) WHERE removed_at IS NULL
"""

RULES = [
    "REVOKE ALL ON push_devices FROM PUBLIC",
    # A sign-in registers the token again, which brings a removed device back.
    f"GRANT SELECT, INSERT, UPDATE (platform, registered_at, removed_at, removed_reason) "
    f"ON push_devices TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (removed_at, removed_reason) ON push_devices TO {WORKER_ROLE}",
    "ALTER TABLE push_devices ENABLE ROW LEVEL SECURITY",
    # A client sees and changes only their own devices. Staff have no reason to see tokens.
    f"CREATE POLICY push_devices_app ON push_devices TO {APP_ROLE} "
    "USING (public.finvest_actor_kind() = 'client' AND client_id = public.finvest_actor_id()) "
    "WITH CHECK (public.finvest_actor_kind() = 'client' AND client_id = public.finvest_actor_id())",
    f"CREATE POLICY push_devices_worker ON push_devices TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Tokens are not records anyone needs to keep: a phone registers again after an upgrade.
    op.execute("DROP TABLE push_devices")
