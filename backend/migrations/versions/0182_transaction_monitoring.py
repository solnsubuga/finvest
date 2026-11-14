"""Transaction monitoring: what money came in and went out, for the rules to read.

Revision ID: 0182_transaction_monitoring
Revises: 0181_payout_screening

* `monitoring_transactions` is screening's own copy of each confirmed deposit and each payout
  asked for: the client, the amount and when. The rules in `screening/domain/monitoring.py`
  read a client's earlier ones from it, so screening never reads another module's tables for
  history. Only the worker writes it, once per deposit or payout, and nothing changes or
  deletes a row.
* A rule that fires opens a `monitoring` case in `compliance_cases` (migration 0180), once per
  rule and dedupe key.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0182_transaction_monitoring"
down_revision: str | None = "0181_payout_screening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = "finvest_app"
WORKER = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"

TABLE = """
CREATE TABLE monitoring_transactions (
    id push_id NOT NULL,
    -- A client in the identity module: no foreign key, a module owns only its tables.
    client_id push_id NOT NULL,
    kind varchar(10) NOT NULL,
    -- The deposit or payout this records, in the payments or payouts module.
    source_id push_id NOT NULL,
    currency char(3) NOT NULL,
    amount_minor bigint NOT NULL,
    -- When the deposit was confirmed, or the payout asked for.
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL,
    CONSTRAINT pk_monitoring_transactions PRIMARY KEY (id),
    CONSTRAINT uq_monitoring_transactions_source UNIQUE (kind, source_id),
    CONSTRAINT ck_monitoring_transactions_kind CHECK (kind IN ('deposit', 'payout')),
    CONSTRAINT ck_monitoring_transactions_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_monitoring_transactions_amount CHECK (amount_minor > 0)
)
"""

GUARD = """
CREATE FUNCTION finvest_monitoring_transactions_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'INSERT' AND public.finvest_actor_kind() = 'system' THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'only the worker records a monitored transaction, and never changes it'
            USING ERRCODE = '42501';
    END;
    $$
"""

RULES = [
    "CREATE INDEX ix_monitoring_transactions_client ON monitoring_transactions "
    "(client_id, occurred_at)",
    GUARD,
    "REVOKE ALL ON FUNCTION finvest_monitoring_transactions_guard() FROM PUBLIC",
    "CREATE TRIGGER monitoring_transactions_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON monitoring_transactions FOR EACH ROW "
    "EXECUTE FUNCTION finvest_monitoring_transactions_guard()",
    "CREATE TRIGGER monitoring_transactions_no_truncate BEFORE TRUNCATE "
    "ON monitoring_transactions FOR EACH STATEMENT "
    "EXECUTE FUNCTION finvest_screening_immutable()",
    "REVOKE ALL ON monitoring_transactions FROM PUBLIC",
    "ALTER TABLE monitoring_transactions ENABLE ROW LEVEL SECURITY",
    f"GRANT SELECT, INSERT ON monitoring_transactions TO {WORKER}",
    "CREATE POLICY monitoring_transactions_worker_read ON monitoring_transactions "
    f"FOR SELECT TO {WORKER} USING (true)",
    "CREATE POLICY monitoring_transactions_worker_insert ON monitoring_transactions "
    f"FOR INSERT TO {WORKER} WITH CHECK (public.finvest_actor_kind() = 'system')",
    f"GRANT SELECT ON monitoring_transactions TO {APP}",
    "CREATE POLICY monitoring_transactions_app_read ON monitoring_transactions "
    f"FOR SELECT TO {APP} USING ({STAFF})",
]


def upgrade() -> None:
    op.execute(TABLE)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM monitoring_transactions) THEN
        RAISE EXCEPTION 'refusing to drop the monitoring record';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE monitoring_transactions")
    op.execute("DROP FUNCTION finvest_monitoring_transactions_guard()")
