"""Stricter withdrawal limits for one client, set by two members of staff.

Revision ID: 0352_payout_client_limits
Revises: 0351_console_client_actions

`payout_client_limits` holds every version of one client's own limits. The newest version is in
force; one with every limit empty clears them. They only ever tighten the shared limits in
`withdrawal_limits`: a payout is checked against the lower of the two for each sort.

A version is written only by the transaction that approved a `withdrawal_limits` action in
`console_client_actions` (migration 0351), for that client, in that same transaction, so one
person can never change a client's limits alone. Nothing changes or deletes a version.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0352_payout_client_limits"
down_revision: str | None = "0351_console_client_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE payout_client_limits (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    currency varchar(3) NOT NULL,
    mobile_money_per_payout bigint,
    mobile_money_per_day bigint,
    bank_per_payout bigint,
    bank_per_day bigint,
    review_above bigint,
    -- The approved console action that set it.
    action_id push_id NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_payout_client_limits PRIMARY KEY (id),
    CONSTRAINT uq_payout_client_limits_action_id UNIQUE (action_id),
    CONSTRAINT fk_payout_client_limits_client_id_identity_clients
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT fk_payout_client_limits_action_id_console_client_actions
        FOREIGN KEY (action_id) REFERENCES console_client_actions (id),
    CONSTRAINT ck_payout_client_limits_currency CHECK (currency = 'UGX'),
    CONSTRAINT ck_payout_client_limits_positive CHECK (
        COALESCE(mobile_money_per_payout, 1) BETWEEN 1 AND 5000000
        AND COALESCE(mobile_money_per_day, 1) > 0 AND COALESCE(bank_per_payout, 1) > 0
        AND COALESCE(bank_per_day, 1) > 0 AND COALESCE(review_above, 1) > 0
    )
)
"""

_WIDEN = [
    "ALTER TABLE console_client_actions DROP CONSTRAINT ck_console_client_actions_kind",
    "ALTER TABLE console_client_actions ADD CONSTRAINT ck_console_client_actions_kind "
    "CHECK (kind IN ('pin_reset', 'withdrawal_limits'))",
]
_NARROW = [
    "ALTER TABLE console_client_actions DROP CONSTRAINT ck_console_client_actions_kind",
    "ALTER TABLE console_client_actions ADD CONSTRAINT ck_console_client_actions_kind "
    "CHECK (kind IN ('pin_reset'))",
]

# The actor settings are a fence against bugs, not a credential (see migration 0001).
RULES = [
    "CREATE INDEX ix_payout_client_limits_client "
    "ON payout_client_limits (client_id, created_at DESC, id DESC)",
    "REVOKE ALL ON payout_client_limits FROM PUBLIC",
    # No UPDATE or DELETE for anyone. The worker reads them when a payout is made for a client.
    f"GRANT SELECT, INSERT ON payout_client_limits TO {APP_ROLE}",
    f"GRANT SELECT ON payout_client_limits TO {WORKER_ROLE}",
    "ALTER TABLE payout_client_limits ENABLE ROW LEVEL SECURITY",
    # A client reads their own, as their app shows the limits that apply to them.
    f"CREATE POLICY payout_client_limits_read ON payout_client_limits FOR SELECT "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff' "
    "OR (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()))",
    # Only the worker may act as the system: the API role sets its own actor settings.
    f"CREATE POLICY payout_client_limits_worker ON payout_client_limits FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY payout_client_limits_insert ON payout_client_limits FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'staff' AND EXISTS ("
    "SELECT FROM console_client_actions a WHERE a.id = action_id AND a.client_id = "
    "payout_client_limits.client_id AND a.kind = 'withdrawal_limits' AND a.status = 'approved' "
    "AND a.decided_in = pg_current_xact_id()))",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_client_limits)
        OR EXISTS (SELECT FROM console_client_actions WHERE kind = 'withdrawal_limits') THEN
        RAISE EXCEPTION 'refusing to drop client withdrawal limits that hold records';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in _WIDEN:
        op.execute(statement)
    op.execute(TABLE)
    for statement in RULES:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE payout_client_limits")
    for statement in _NARROW:
        op.execute(statement)
