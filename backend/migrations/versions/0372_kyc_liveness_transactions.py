"""Add liveness transactions: the Regula session a client finished for a liveness attempt.

Revision ID: 0372_kyc_liveness_transactions
Revises: 0088_dealing_auctions

Regula's liveness runs in the app, against Regula, under the attempt's tag. When the session
ends the app is given its transaction ID and sends it here. The worker then reads the result
from Regula itself and checks it carries the attempt's tag, so the app's word is never taken
for the result. One transaction per attempt, and a transaction is used once, by one attempt.

A live capture becomes the check's selfie, and a new selfie retires the guided selfie's pose
frames (migration 0371), so the worker may now retire them too.
See docs/identity-checks.md#regula-liveness.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0372_kyc_liveness_transactions"
down_revision: str | None = "0088_dealing_auctions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE kyc_liveness_transactions (
    attempt_id push_id NOT NULL,
    -- Regula's transaction ID, a UUID.
    transaction_id varchar(64) COLLATE "C" NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_kyc_liveness_transactions PRIMARY KEY (attempt_id),
    CONSTRAINT fk_kyc_liveness_transactions_attempt
        FOREIGN KEY (attempt_id) REFERENCES kyc_liveness (id),
    CONSTRAINT uq_kyc_liveness_transactions_transaction UNIQUE (transaction_id),
    CONSTRAINT ck_kyc_liveness_transactions_transaction
        CHECK (transaction_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
)
"""

# Only for an attempt that is open, the client's own (the policy below), and never changed.
GUARD = """
CREATE FUNCTION finvest_kyc_liveness_transactions_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        v_status text;
    BEGIN
        SELECT status INTO v_status FROM kyc_liveness WHERE id = NEW.attempt_id FOR SHARE;
        IF v_status IS DISTINCT FROM 'open' THEN
            RAISE EXCEPTION 'a liveness transaction is only for an open attempt'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_liveness_transactions_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_liveness_transactions_guard BEFORE INSERT ON kyc_liveness_transactions "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_liveness_transactions_guard()",
    "REVOKE ALL ON kyc_liveness_transactions FROM PUBLIC",
    # No UPDATE or DELETE for anyone.
    f"GRANT SELECT, INSERT ON kyc_liveness_transactions TO {APP_ROLE}",
    f"GRANT SELECT ON kyc_liveness_transactions TO {WORKER_ROLE}",
    "ALTER TABLE kyc_liveness_transactions ENABLE ROW LEVEL SECURITY",
    # The subquery runs under the kyc_liveness policies, so a client reaches their own attempts
    # and staff see all. Only the client sends a transaction.
    f"CREATE POLICY kyc_liveness_transactions_app_read ON kyc_liveness_transactions "  # noqa: S608 - constant role name
    f"FOR SELECT TO {APP_ROLE} USING (attempt_id IN (SELECT id FROM kyc_liveness))",
    f"CREATE POLICY kyc_liveness_transactions_app_insert ON kyc_liveness_transactions "  # noqa: S608 - constant role name
    f"FOR INSERT TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'client' "
    "AND attempt_id IN (SELECT id FROM kyc_liveness))",
    f"CREATE POLICY kyc_liveness_transactions_worker ON kyc_liveness_transactions "
    f"FOR SELECT TO {WORKER_ROLE} USING (true)",
    # A live capture becomes the selfie, which retires the pose frames taken with the old one.
    f"GRANT UPDATE (replaced_at) ON kyc_pose_frames TO {WORKER_ROLE}",
    f"CREATE POLICY kyc_pose_frames_worker_retire ON kyc_pose_frames FOR UPDATE TO {WORKER_ROLE} "
    "USING (finvest_actor_kind() = 'system') WITH CHECK (finvest_actor_kind() = 'system')",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_liveness_transactions) THEN
        RAISE EXCEPTION 'refusing to drop liveness transactions that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Identity records are kept for the regulator; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP POLICY kyc_pose_frames_worker_retire ON kyc_pose_frames")
    op.execute(f"REVOKE UPDATE (replaced_at) ON kyc_pose_frames FROM {WORKER_ROLE}")
    op.execute("DROP TABLE kyc_liveness_transactions")
    op.execute("DROP FUNCTION finvest_kyc_liveness_transactions_guard()")
