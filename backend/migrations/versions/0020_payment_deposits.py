"""Add deposits: payments in by mobile money, their history and provider callbacks.

Revision ID: 0020_payment_deposits
Revises: 0005_identity

A deposit is not a ledger row: its status changes as the provider answers. What must not change
is enforced here, not only in application code:

* What a deposit is for, who pays, how much and by which method never change after it is saved.
* A deposit moves only along the allowed path, and `confirmed` and `failed` are final.
* A deposit is `confirmed` exactly when it points at its ledger entry.
* The status history and the record of provider callbacks are insert-only for every role.
* A client transaction sees and creates only that client's deposits. Only the worker changes a
  deposit's status. The callback address can add a callback record and nothing else.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_payment_deposits"
down_revision: str | None = "0005_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

STATUSES = "'created', 'sending', 'awaiting_approval', 'confirmed', 'failed', 'uncertain'"
# Kept equal to TRANSITIONS in app/modules/payments/domain/deposits.py. An integration test
# tries every pair against this trigger and compares.
ALLOWED_MOVES = (
    ("created", "sending"),
    ("created", "failed"),
    ("sending", "created"),
    ("sending", "awaiting_approval"),
    ("sending", "failed"),
    ("sending", "uncertain"),
    ("awaiting_approval", "confirmed"),
    ("awaiting_approval", "failed"),
    ("awaiting_approval", "uncertain"),
    ("uncertain", "awaiting_approval"),
    ("uncertain", "confirmed"),
    ("uncertain", "failed"),
)
REFERENCE = "^[A-Za-z0-9._:-]{1,128}$"
CODE = "^[a-z][a-z0-9_]{0,63}$"

# `ledger_entry_id` has no foreign key on purpose. A module does not tie its tables to another
# module's, and the ledger entry already points back at its deposit (source_type and source_id).
# The entry is posted and the ID stored in one transaction, by one function.
TABLES = f"""
CREATE TABLE payment_deposits (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    purpose_type varchar(16) NOT NULL,
    purpose_id push_id NOT NULL,
    method varchar(16) NOT NULL,
    provider varchar(32) NOT NULL,
    currency varchar(3) NOT NULL,
    amount_minor bigint NOT NULL,
    payer_msisdn varchar(13) NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'created',
    code varchar(64),
    provider_reference varchar(128),
    ledger_entry_id push_id,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    sent_at timestamptz,
    settled_at timestamptz,
    CONSTRAINT pk_payment_deposits PRIMARY KEY (id),
    CONSTRAINT uq_payment_deposits_provider_reference UNIQUE (provider, provider_reference),
    CONSTRAINT uq_payment_deposits_ledger_entry_id UNIQUE (ledger_entry_id),
    CONSTRAINT ck_payment_deposits_purpose_type CHECK (purpose_type IN ('investment', 'goal')),
    CONSTRAINT ck_payment_deposits_method CHECK (method IN ('mtn', 'airtel')),
    CONSTRAINT ck_payment_deposits_provider CHECK (provider ~ '^[a-z][a-z0-9_]{{0,31}}$'),
    CONSTRAINT ck_payment_deposits_currency CHECK (currency = 'UGX'),
    CONSTRAINT ck_payment_deposits_amount_minor CHECK (amount_minor > 0),
    CONSTRAINT ck_payment_deposits_payer_msisdn CHECK (payer_msisdn ~ '^\\+256[0-9]{{9}}$'),
    CONSTRAINT ck_payment_deposits_status CHECK (status IN ({STATUSES})),
    CONSTRAINT ck_payment_deposits_code CHECK (code ~ '{CODE}'),
    CONSTRAINT ck_payment_deposits_code_when
        CHECK ((code IS NOT NULL) = (status IN ('failed', 'uncertain'))),
    CONSTRAINT ck_payment_deposits_provider_reference
        CHECK (provider_reference ~ '{REFERENCE}'),
    CONSTRAINT ck_payment_deposits_confirmed_has_entry
        CHECK ((status = 'confirmed') = (ledger_entry_id IS NOT NULL)),
    CONSTRAINT ck_payment_deposits_settled
        CHECK ((settled_at IS NOT NULL) = (status IN ('confirmed', 'failed')))
);
CREATE INDEX ix_payment_deposits_client_id ON payment_deposits (client_id, id DESC);
CREATE INDEX ix_payment_deposits_purpose ON payment_deposits (purpose_type, purpose_id);
CREATE INDEX ix_payment_deposits_open ON payment_deposits (status, updated_at)
    WHERE status NOT IN ('confirmed', 'failed');

CREATE TABLE payment_deposit_events (
    id push_id NOT NULL,
    deposit_id push_id NOT NULL,
    occurred_at timestamptz NOT NULL,
    from_status varchar(24),
    to_status varchar(24) NOT NULL,
    code varchar(64),
    cause varchar(24) NOT NULL,
    CONSTRAINT pk_payment_deposit_events PRIMARY KEY (id),
    CONSTRAINT fk_payment_deposit_events_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT ck_payment_deposit_events_from_status CHECK (from_status IN ({STATUSES})),
    CONSTRAINT ck_payment_deposit_events_to_status CHECK (to_status IN ({STATUSES})),
    CONSTRAINT ck_payment_deposit_events_code CHECK (code ~ '{CODE}'),
    CONSTRAINT ck_payment_deposit_events_cause
        CHECK (cause IN ('request', 'send', 'status_read', 'sweep', 'reconciliation'))
);
CREATE INDEX ix_payment_deposit_events_deposit_id ON payment_deposit_events (deposit_id, id);

CREATE TABLE payment_provider_notices (
    id push_id NOT NULL,
    provider varchar(32) NOT NULL,
    notice_key varchar(128) NOT NULL,
    request_id varchar(128),
    provider_reference varchar(128),
    received_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_provider_notices PRIMARY KEY (id),
    CONSTRAINT uq_payment_provider_notices_provider_notice_key UNIQUE (provider, notice_key),
    CONSTRAINT ck_payment_provider_notices_provider CHECK (provider ~ '^[a-z][a-z0-9_]{{0,31}}$'),
    CONSTRAINT ck_payment_provider_notices_notice_key CHECK (notice_key ~ '{REFERENCE}'),
    CONSTRAINT ck_payment_provider_notices_request_id CHECK (request_id ~ '{REFERENCE}'),
    CONSTRAINT ck_payment_provider_notices_provider_reference
        CHECK (provider_reference ~ '{REFERENCE}')
)
"""

MOVES_SQL = ", ".join(f"('{old}', '{new}')" for old, new in ALLOWED_MOVES)

FUNCTIONS = [
    f"""
CREATE FUNCTION finvest_payment_deposit_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF (NEW.id, NEW.client_id, NEW.purpose_type, NEW.purpose_id, NEW.method, NEW.provider,
            NEW.currency, NEW.amount_minor, NEW.payer_msisdn, NEW.created_at)
           IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.purpose_type, OLD.purpose_id, OLD.method, OLD.provider,
            OLD.currency, OLD.amount_minor, OLD.payer_msisdn, OLD.created_at) THEN
            RAISE EXCEPTION 'a deposit''s terms cannot change' USING ERRCODE = '42501';
        END IF;
        IF OLD.status IN ('confirmed', 'failed') THEN
            RAISE EXCEPTION 'a % deposit is final', OLD.status USING ERRCODE = '42501';
        END IF;
        IF NEW.status <> OLD.status
           AND (OLD.status, NEW.status) NOT IN ({MOVES_SQL}) THEN
            RAISE EXCEPTION 'a deposit cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        IF OLD.provider_reference IS NOT NULL
           AND NEW.provider_reference IS DISTINCT FROM OLD.provider_reference THEN
            RAISE EXCEPTION 'a provider reference cannot change' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    """
CREATE FUNCTION finvest_payments_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'payments table % does not allow this', TG_TABLE_NAME
            USING ERRCODE = '42501';
    END;
    $$
""",
]

IS_STAFF = "finvest_actor_kind() = 'staff'"
IS_CLIENT = "finvest_actor_kind() = 'client'"
OWN = f"{IS_CLIENT} AND client_id = finvest_actor_id()"
# Reads payment_deposits under the caller's own row policy.
VISIBLE_DEPOSIT = (
    "EXISTS (SELECT FROM payment_deposits AS deposit "
    "WHERE deposit.id = payment_deposit_events.deposit_id)"
)

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payment_deposit_guard(), finvest_payments_immutable() "
    "FROM PUBLIC",
    "CREATE TRIGGER payment_deposits_guard BEFORE UPDATE ON payment_deposits "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payment_deposit_guard()",
    "CREATE TRIGGER payment_deposits_no_delete BEFORE DELETE ON payment_deposits "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
    "CREATE TRIGGER payment_deposits_no_truncate BEFORE TRUNCATE ON payment_deposits "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payments_immutable()",
    *(
        statement
        for table in ("payment_deposit_events", "payment_provider_notices")
        for statement in (
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payments_immutable()",
        )
    ),
    *(
        statement
        for table in ("payment_deposits", "payment_deposit_events", "payment_provider_notices")
        for statement in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {table} FROM PUBLIC",
        )
    ),
    # The API reads deposits and saves new ones. It never changes one: every status change
    # follows a provider's answer, and only the worker talks to providers.
    f"GRANT SELECT, INSERT ON payment_deposits, payment_deposit_events TO {APP_ROLE}",
    f"GRANT INSERT ON payment_provider_notices TO {APP_ROLE}",
    f"GRANT SELECT ON payment_deposits, payment_provider_notices TO {WORKER_ROLE}",
    "GRANT UPDATE (status, code, provider_reference, ledger_entry_id, updated_at, sent_at, "
    f"settled_at) ON payment_deposits TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT ON payment_deposit_events TO {WORKER_ROLE}",
    f"CREATE POLICY payment_deposits_app_read ON payment_deposits FOR SELECT TO {APP_ROLE} "
    f"USING ({IS_STAFF} OR ({OWN}))",
    # A client starts only their own deposit, and only at the start of its path.
    f"CREATE POLICY payment_deposits_app_insert ON payment_deposits FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({OWN} AND status = 'created' AND provider_reference IS NULL)",
    f"CREATE POLICY payment_deposits_worker ON payment_deposits TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
    # The history of a deposit is visible to whoever may see the deposit itself.
    "CREATE POLICY payment_deposit_events_app_read ON payment_deposit_events FOR SELECT "
    f"TO {APP_ROLE} USING ({VISIBLE_DEPOSIT})",
    "CREATE POLICY payment_deposit_events_app_insert ON payment_deposit_events FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({IS_CLIENT} AND cause = 'request' AND from_status IS NULL "
    f"AND to_status = 'created' AND {VISIBLE_DEPOSIT})",
    f"CREATE POLICY payment_deposit_events_worker ON payment_deposit_events TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
    # A callback arrives with nobody signed in. The API can record it and cannot read it back.
    "CREATE POLICY payment_provider_notices_app_insert ON payment_provider_notices FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (true)",
    "CREATE POLICY payment_provider_notices_worker_read ON payment_provider_notices FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *FUNCTIONS, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_deposits) OR EXISTS (SELECT FROM payment_provider_notices) THEN
        RAISE EXCEPTION 'refusing to drop payments that hold deposits or callbacks';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Dropping the tables would erase the record of money received.
    op.execute(EMPTY_GUARD)
    for table in ("payment_provider_notices", "payment_deposit_events", "payment_deposits"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION finvest_payment_deposit_guard()")
    op.execute("DROP FUNCTION finvest_payments_immutable()")
