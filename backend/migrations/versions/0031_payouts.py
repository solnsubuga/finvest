"""Add withdrawal limits and payouts.

Revision ID: 0031_payouts
Revises: 0060_investment_products

* Limits are versions, insert only. Each has an effective date, a proposer and a different
  approver. The launch version is seeded here with no people, and is the only row that may be.
* A payout's amount, client and destination never change. Its status moves only along the
  table in `payouts/domain/payouts.py`, repeated here, and a finished payout never changes.
* A payout that may have reached the provider cannot go back to queued, so it cannot be sent
  again by any code path.
* Clients see only their own payouts and cannot change one except to cancel it while held.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031_payouts"
down_revision: str | None = "0060_investment_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE withdrawal_limits (
    id push_id NOT NULL,
    effective_from date NOT NULL,
    currency varchar(3) NOT NULL,
    mobile_money_per_payout bigint NOT NULL,
    mobile_money_per_day bigint,
    bank_per_payout bigint,
    bank_per_day bigint,
    review_above bigint,
    proposed_by push_id,
    approved_by push_id,
    reason varchar(500) NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_withdrawal_limits PRIMARY KEY (id),
    CONSTRAINT uq_withdrawal_limits_effective_from UNIQUE (effective_from),
    CONSTRAINT ck_withdrawal_limits_currency CHECK (currency = 'UGX'),
    CONSTRAINT ck_withdrawal_limits_mobile_money
        CHECK (mobile_money_per_payout BETWEEN 1 AND 5000000),
    CONSTRAINT ck_withdrawal_limits_positive CHECK (
        COALESCE(mobile_money_per_day, 1) > 0 AND COALESCE(bank_per_payout, 1) > 0
        AND COALESCE(bank_per_day, 1) > 0 AND COALESCE(review_above, 1) > 0
    ),
    CONSTRAINT ck_withdrawal_limits_daily CHECK (
        COALESCE(mobile_money_per_day, mobile_money_per_payout) >= mobile_money_per_payout
        AND (bank_per_day IS NULL OR bank_per_payout IS NULL OR bank_per_day >= bank_per_payout)
    ),
    CONSTRAINT ck_withdrawal_limits_two_people CHECK (
        (proposed_by IS NULL AND approved_by IS NULL AND effective_from = DATE '2026-01-01')
        OR (proposed_by IS NOT NULL AND approved_by IS NOT NULL AND proposed_by <> approved_by)
    ),
    CONSTRAINT ck_withdrawal_limits_reason CHECK (length(btrim(reason)) > 0)
);
CREATE TABLE payouts (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    destination_id push_id NOT NULL,
    kind varchar(16) NOT NULL,
    amount_minor bigint NOT NULL,
    currency varchar(3) NOT NULL,
    status varchar(16) NOT NULL,
    business_date date NOT NULL,
    source_account_id push_id NOT NULL,
    source_reference varchar(64) NOT NULL,
    in_flight_account_id push_id NOT NULL,
    request_key varchar(128) NOT NULL,
    provider_reference varchar(100),
    failure_code varchar(64),
    reviewed_by push_id,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_payouts PRIMARY KEY (id),
    CONSTRAINT fk_payouts_destination
        FOREIGN KEY (destination_id) REFERENCES payout_destinations (id),
    CONSTRAINT uq_payouts_request_key UNIQUE (client_id, request_key),
    CONSTRAINT ck_payouts_request_key CHECK (request_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
    CONSTRAINT ck_payouts_source_reference CHECK (source_reference ~ '^[A-Za-z0-9._:-]{1,64}$'),
    CONSTRAINT ck_payouts_accounts CHECK (source_account_id <> in_flight_account_id),
    CONSTRAINT ck_payouts_kind CHECK (kind IN ('mobile_money', 'bank')),
    CONSTRAINT ck_payouts_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_payouts_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_payouts_mobile_money CHECK (
        kind <> 'mobile_money' OR (currency = 'UGX' AND amount_minor <= 5000000)),
    CONSTRAINT ck_payouts_status CHECK (status IN (
        'held', 'queued', 'sent', 'uncertain', 'paid', 'failed', 'cancelled')),
    CONSTRAINT ck_payouts_failure_code CHECK (failure_code ~ '^[a-z][a-z0-9_.]{0,63}$')
);
CREATE INDEX ix_payouts_client_day ON payouts (client_id, business_date, kind);
CREATE INDEX ix_payouts_open ON payouts (status, updated_at)
    WHERE status IN ('held', 'queued', 'sent', 'uncertain');
CREATE INDEX ix_payouts_created_at ON payouts (created_at DESC, id DESC)
"""

SEED = """
INSERT INTO withdrawal_limits (id, effective_from, currency, mobile_money_per_payout,
    review_above, reason, created_at)
VALUES ('-Launch0limits000000', DATE '2026-01-01', 'UGX', 5000000, 10000000,
    'Launch limits. Seeded by migration 0031.', TIMESTAMPTZ '2026-01-01 00:00:00+00')
"""

IMMUTABLE_FUNCTION = """
CREATE FUNCTION finvest_withdrawal_limits_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'withdrawal limits are versions and are never changed'
            USING ERRCODE = '42501';
    END;
    $$
"""

GUARD_FUNCTION = """
CREATE FUNCTION finvest_payouts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        step text := OLD.status || '>' || NEW.status;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'payouts are never deleted' USING ERRCODE = '42501';
        END IF;
        IF OLD.status IN ('paid', 'failed', 'cancelled') THEN
            RAISE EXCEPTION 'a finished payout never changes' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.client_id, NEW.destination_id, NEW.kind, NEW.amount_minor, NEW.currency,
            NEW.business_date, NEW.source_account_id, NEW.in_flight_account_id, NEW.request_key,
            NEW.source_reference, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.destination_id, OLD.kind, OLD.amount_minor, OLD.currency,
            OLD.business_date, OLD.source_account_id, OLD.in_flight_account_id, OLD.request_key,
            OLD.source_reference, OLD.created_at)
        THEN
            RAISE EXCEPTION 'the terms of a payout never change' USING ERRCODE = '42501';
        END IF;

        IF actor = 'client' AND step = 'held>cancelled' THEN
            IF NEW.reviewed_by IS NOT NULL THEN
                RAISE EXCEPTION 'a client signs no review' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN ('held>queued', 'held>cancelled') THEN
            IF NEW.reviewed_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a review is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'system' AND step IN (
            'queued>queued', 'queued>sent', 'queued>paid', 'queued>failed', 'queued>uncertain',
            'sent>sent', 'sent>paid', 'sent>failed',
            'uncertain>uncertain', 'uncertain>sent', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'the worker signs nothing in a person''s name'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            RAISE EXCEPTION 'this step is not allowed for a payout' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

OWN = "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_withdrawal_limits_immutable() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_payouts_guard() FROM PUBLIC",
    "CREATE TRIGGER withdrawal_limits_immutable BEFORE UPDATE OR DELETE ON withdrawal_limits "
    "FOR EACH ROW EXECUTE FUNCTION finvest_withdrawal_limits_immutable()",
    "CREATE TRIGGER withdrawal_limits_no_truncate BEFORE TRUNCATE ON withdrawal_limits "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_withdrawal_limits_immutable()",
    "CREATE TRIGGER payouts_guard BEFORE UPDATE OR DELETE ON payouts "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payouts_guard()",
    "CREATE TRIGGER payouts_no_truncate BEFORE TRUNCATE ON payouts "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payouts_guard()",
    "ALTER TABLE withdrawal_limits ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE payouts ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON withdrawal_limits, payouts FROM PUBLIC",
    f"GRANT SELECT, INSERT ON withdrawal_limits TO {APP_ROLE}",
    f"GRANT SELECT ON withdrawal_limits TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON payouts TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON payouts TO {WORKER_ROLE}",
    # Limits are not secret from a signed-in person: the app has to say why it refused.
    f"CREATE POLICY withdrawal_limits_app_read ON withdrawal_limits FOR SELECT TO {APP_ROLE} "
    "USING (finvest_actor_kind() IN ('client', 'staff'))",
    f"CREATE POLICY withdrawal_limits_worker_read ON withdrawal_limits FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    # The approver inserts the version, in their own name, for a day that has not started.
    f"CREATE POLICY withdrawal_limits_app_insert ON withdrawal_limits FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'staff' AND approved_by = finvest_actor_id())",
    f"CREATE POLICY payouts_app_read ON payouts FOR SELECT TO {APP_ROLE} "
    f"USING (finvest_actor_kind() = 'staff' OR ({OWN}))",
    # A client asks for their own payout. It starts held or queued, never further along.
    f"CREATE POLICY payouts_app_insert ON payouts FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({OWN} AND status IN ('held', 'queued') AND provider_reference IS NULL "
    "AND reviewed_by IS NULL)",
    # The worker asks on a client's behalf when an investment matures. Same starting point.
    f"CREATE POLICY payouts_worker_insert ON payouts FOR INSERT TO {WORKER_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'system' AND status IN ('held', 'queued') "
    "AND provider_reference IS NULL AND reviewed_by IS NULL)",
    f"CREATE POLICY payouts_app_update ON payouts FOR UPDATE TO {APP_ROLE} "
    f"USING (finvest_actor_kind() = 'staff' OR ({OWN}))",
    f"CREATE POLICY payouts_worker_read ON payouts FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY payouts_worker_update ON payouts FOR UPDATE TO {WORKER_ROLE} "
    "USING (finvest_actor_kind() = 'system')",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), SEED, IMMUTABLE_FUNCTION, GUARD_FUNCTION, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payouts)
       OR EXISTS (SELECT FROM withdrawal_limits WHERE proposed_by IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop payouts or limits that hold rows';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE payouts")
    op.execute("ALTER TABLE withdrawal_limits DISABLE TRIGGER withdrawal_limits_no_truncate")
    op.execute("DROP TABLE withdrawal_limits")
    op.execute("DROP FUNCTION finvest_payouts_guard()")
    op.execute("DROP FUNCTION finvest_withdrawal_limits_immutable()")
