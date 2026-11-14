"""Keep the Cloudflare Access list for the console in step with the team.

Revision ID: 0390_staff_access_sync
Revises: 0183_monitoring_transfers

`identity_staff_access` holds, for each member of staff, where the worker got to in bringing the
Cloudflare Zero Trust list behind the console's Access policy in line with the team: waiting,
on the list, removed, failed, or not set up. See docs/console-admin.md#cloudflare-access.

* The API marks a member `waiting` in the same transaction that approves a change to them, and
  may write nothing else: it never says that the list was changed.
* The worker reads each member's address and status (the only columns of identity_staff it may
  read) and records what Cloudflare's list says once it has changed it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0390_staff_access_sync"
down_revision: str | None = "0183_monitoring_transfers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STATES = "'waiting', 'in_access', 'removed', 'failed', 'not_set_up'"
STAFF = "finvest_actor_kind() = 'staff'"

TABLE = f"""
CREATE TABLE identity_staff_access (
    staff_id push_id NOT NULL,
    state text NOT NULL,
    -- When a change last asked for the list to be brought in line.
    requested_at timestamptz NOT NULL,
    -- When the worker last saw the list say what the team says.
    synced_at timestamptz,
    -- Why the last attempt did not finish. A code, never what Cloudflare sent back.
    error_code text,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_identity_staff_access PRIMARY KEY (staff_id),
    CONSTRAINT fk_identity_staff_access_staff_id_identity_staff
        FOREIGN KEY (staff_id) REFERENCES identity_staff (id),
    CONSTRAINT ck_identity_staff_access_state CHECK (state IN ({STATES})),
    CONSTRAINT ck_identity_staff_access_synced
        CHECK (state NOT IN ('in_access', 'removed') OR synced_at IS NOT NULL),
    CONSTRAINT ck_identity_staff_access_error_code
        CHECK (error_code IS NULL OR error_code ~ '^[a-z0-9_]{{1,64}}$')
)
"""

RULES = [
    "REVOKE ALL ON identity_staff_access FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (state, requested_at, error_code, updated_at) "
    f"ON identity_staff_access TO {APP_ROLE}",
    # The daily check adds a row for anyone added before this table, such as the first admin.
    f"GRANT SELECT, INSERT, UPDATE (state, requested_at, synced_at, error_code, updated_at) "
    f"ON identity_staff_access TO {WORKER_ROLE}",
    # The address the list holds and whether they belong on it. Nothing else about staff.
    f"GRANT SELECT (id, email, status) ON identity_staff TO {WORKER_ROLE}",
    "ALTER TABLE identity_staff_access ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY identity_staff_access_app_read ON identity_staff_access FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF})",
    # The API only ever asks: a row it writes is waiting, with no error carried over.
    f"CREATE POLICY identity_staff_access_app_insert ON identity_staff_access FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({STAFF} AND state = 'waiting' AND error_code IS NULL "
    "AND synced_at IS NULL)",
    f"CREATE POLICY identity_staff_access_app_update ON identity_staff_access FOR UPDATE "
    f"TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF} AND state = 'waiting' "
    "AND error_code IS NULL)",
    f"CREATE POLICY identity_staff_access_worker ON identity_staff_access "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in [TABLE, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Only a record of where the worker got to: Cloudflare's own list is the truth.
    op.execute("REVOKE SELECT (id, email, status) ON identity_staff FROM finvest_worker")
    op.execute("DROP TABLE identity_staff_access")
