"""Create operation receipts: one saved outcome per actor, operation and idempotency key."""

from alembic import op

revision = "0003_idempotency"
down_revision = "0002_jobs"
branch_labels = None
depends_on = None

TABLE = """
CREATE TABLE operation_receipts (
    actor_kind text NOT NULL,
    actor_id push_id NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_digest text NOT NULL,
    status_code integer NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_operation_receipts
        PRIMARY KEY (actor_kind, actor_id, operation, idempotency_key),
    CONSTRAINT ck_operation_receipts_actor_kind CHECK (actor_kind IN ('client', 'staff')),
    CONSTRAINT ck_operation_receipts_operation CHECK (operation ~ '^[a-z][a-z0-9_.]{0,99}$'),
    CONSTRAINT ck_operation_receipts_idempotency_key
        CHECK (idempotency_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
    CONSTRAINT ck_operation_receipts_request_digest CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_operation_receipts_status_code CHECK (status_code BETWEEN 200 AND 299),
    CONSTRAINT ck_operation_receipts_response
        CHECK (jsonb_typeof(response) = 'object' AND pg_column_size(response) <= 65536)
)
"""

# A receipt answers only the person who saved it, and is never changed or removed by the API.
OWN = "actor_kind = finvest_actor_kind() AND actor_id = finvest_actor_id()"
ACCESS = [
    "ALTER TABLE operation_receipts ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON operation_receipts FROM PUBLIC",
    "GRANT SELECT, INSERT ON operation_receipts TO finvest_app",
    f"CREATE POLICY operation_receipts_own ON operation_receipts TO finvest_app "
    f"USING ({OWN}) WITH CHECK ({OWN})",
]


def upgrade() -> None:
    op.execute(TABLE.strip())
    for statement in ACCESS:
        op.execute(statement)


# A receipt is the proof that a command already ran. Dropping one lets the command run again.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM operation_receipts) THEN
        RAISE EXCEPTION 'refusing to drop operation receipts that hold rows';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE operation_receipts")
