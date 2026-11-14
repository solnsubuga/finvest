"""Note each client the worker could not carry over at the switch, and why.

Revision ID: 0075_migration_carry_problems
Revises: 0074_migration_switch

The worker carries each client over in a transaction of its own: their identity, their running
investments, their claim and the link. When one of those is refused, for example an identity
document that already belongs to a client of Finvest 2.0, nothing of that client is kept and the
refusal's code is noted here. Staff resolve it by hand, and the worker moves on to the next
client. A note holds the Finvest 1.0 reference and a code, nothing from the sheets, and is never
changed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0075_migration_carry_problems"
down_revision: str | None = "0074_migration_switch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "migration_carry_problems"

UPGRADE = [
    f"""
CREATE TABLE {TABLE} (
    switch_id push_id NOT NULL,
    legacy_client_ref varchar(64) NOT NULL,
    code varchar(64) NOT NULL,
    noted_at timestamptz NOT NULL,
    CONSTRAINT pk_{TABLE} PRIMARY KEY (switch_id, legacy_client_ref),
    CONSTRAINT fk_{TABLE}_switch_id FOREIGN KEY (switch_id) REFERENCES migration_switches (id),
    CONSTRAINT fk_{TABLE}_legacy_client_ref
        FOREIGN KEY (legacy_client_ref) REFERENCES migration_carried_clients (legacy_client_ref),
    CONSTRAINT ck_{TABLE}_code CHECK (code ~ '^[a-z_]{{1,64}}$')
)
""",
    f"CREATE TRIGGER {TABLE}_immutable BEFORE UPDATE OR DELETE ON {TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_immutable()",
    f"CREATE TRIGGER {TABLE}_no_truncate BEFORE TRUNCATE ON {TABLE} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_migration_immutable()",
    f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {TABLE} FROM PUBLIC",
    f"GRANT SELECT ON {TABLE} TO finvest_app",
    f"GRANT SELECT, INSERT ON {TABLE} TO finvest_worker",
    f"CREATE POLICY {TABLE}_app_read ON {TABLE} FOR SELECT TO finvest_app "
    "USING (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY {TABLE}_worker_read ON {TABLE} FOR SELECT TO finvest_worker USING (true)",
    f"CREATE POLICY {TABLE}_worker_insert ON {TABLE} FOR INSERT TO finvest_worker "
    "WITH CHECK (finvest_actor_kind() = 'system')",
]

EMPTY_GUARD = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM {TABLE}) THEN
        RAISE EXCEPTION 'refusing to drop the clients the switch could not carry over';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(f"DROP TABLE {TABLE}")
