"""Add deposits by bank transfer: the client's receipt and the staff member's check of it.

Revision ID: 0022_bank_transfer_deposits
Revises: 0062_investment_interest_runs

A bank transfer is a deposit like any other, in the same table, so the one live deposit per
investment rule from 0021 covers both ways to pay. It has its own short path, `in_review` to
`confirmed` or `failed`, and no payer number. What is enforced here:

* A bank transfer starts `in_review` and never enters a mobile money state. A mobile money
  deposit is never `in_review`. A bank transfer has no payer number and every other deposit has
  one.
* The receipt and the review are insert only, for every role. A deposit has one of each.
* A client adds a receipt only to their own bank transfer, while it is in review. Only staff add
  a review, in their own name, and only of a bank transfer that is in review.
* A statement line confirms one deposit at most.
* A match needs a second member of staff. The approval is insert only, one per deposit, and is
  refused from the person who made the match or for a review that did not match.
* The worker confirms a bank transfer only when its review matched it to a statement line and a
  second person approved the match, and fails one only with the reason its review gave.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_bank_transfer_deposits"
down_revision: str | None = "0062_investment_interest_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

OLD_STATUSES = "'created', 'sending', 'awaiting_approval', 'confirmed', 'failed', 'uncertain'"
STATUSES = f"{OLD_STATUSES}, 'in_review'"
OLD_CAUSES = "'request', 'send', 'status_read', 'sweep', 'reconciliation'"
CAUSES = f"{OLD_CAUSES}, 'staff_review'"
OLD_MOVES = (
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
# Kept equal to TRANSITIONS in app/modules/payments/domain/deposits.py. An integration test
# tries every pair against the trigger and compares.
ALLOWED_MOVES = (*OLD_MOVES, ("in_review", "confirmed"), ("in_review", "failed"))
REASONS = "'amount_differs', 'not_on_statement', 'unreadable', 'name_differs', 'other'"
CODE = "^[a-z][a-z0-9_]{0,63}$"

IS_STAFF = "finvest_actor_kind() = 'staff'"
IS_CLIENT = "finvest_actor_kind() = 'client'"
OWN = f"{IS_CLIENT} AND client_id = finvest_actor_id()"


def guard(moves: Sequence[tuple[str, str]]) -> str:
    """The deposit update guard from 0020, with the given moves allowed."""
    moves_sql = ", ".join(f"('{old}', '{new}')" for old, new in moves)
    return f"""
CREATE OR REPLACE FUNCTION finvest_payment_deposit_guard() RETURNS trigger
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
           AND (OLD.status, NEW.status) NOT IN ({moves_sql}) THEN
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
"""


SETTLE_GUARD = """
CREATE FUNCTION finvest_payment_bank_settle_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        review record;
    BEGIN
        IF NEW.method <> 'bank_transfer' OR NEW.status = OLD.status THEN
            RETURN NEW;
        END IF;
        SELECT outcome, reason INTO review FROM payment_bank_reviews
         WHERE deposit_id = NEW.id;
        IF NOT FOUND
           OR (NEW.status = 'confirmed' AND (review.outcome <> 'matched' OR NOT EXISTS (
               SELECT FROM payment_bank_approvals WHERE deposit_id = NEW.id)))
           OR (NEW.status = 'failed'
               AND (review.outcome <> 'not_matched' OR NEW.code IS DISTINCT FROM review.reason))
        THEN
            RAISE EXCEPTION 'a bank transfer is settled only as its review says'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

TABLES = f"""
CREATE TABLE payment_bank_receipts (
    deposit_id push_id NOT NULL,
    client_id push_id NOT NULL,
    storage_key varchar(128) NOT NULL,
    content_type varchar(32) NOT NULL,
    byte_size integer NOT NULL,
    sha256 char(64) NOT NULL,
    sent_on date NOT NULL,
    bank_name varchar(80) NOT NULL,
    transaction_number varchar(64) NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_bank_receipts PRIMARY KEY (deposit_id),
    CONSTRAINT fk_payment_bank_receipts_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT uq_payment_bank_receipts_storage_key UNIQUE (storage_key),
    CONSTRAINT ck_payment_bank_receipts_storage_key
        CHECK (storage_key ~ '^payments/receipts/[A-Za-z0-9_-]{{20}}$'),
    CONSTRAINT ck_payment_bank_receipts_content_type
        CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    CONSTRAINT ck_payment_bank_receipts_byte_size CHECK (byte_size BETWEEN 1 AND 10485760),
    CONSTRAINT ck_payment_bank_receipts_sha256 CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT ck_payment_bank_receipts_bank_name CHECK (length(bank_name) >= 2),
    CONSTRAINT ck_payment_bank_receipts_transaction_number
        CHECK (transaction_number ~ '^[A-Za-z0-9][A-Za-z0-9 ./-]{{0,63}}$')
);
CREATE INDEX ix_payment_bank_receipts_client_id ON payment_bank_receipts (client_id);

CREATE TABLE payment_bank_reviews (
    deposit_id push_id NOT NULL,
    outcome varchar(16) NOT NULL,
    reason varchar(32),
    statement_line_id push_id,
    account_reference varchar(64),
    reviewed_by push_id NOT NULL,
    reviewed_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_bank_reviews PRIMARY KEY (deposit_id),
    CONSTRAINT fk_payment_bank_reviews_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT uq_payment_bank_reviews_statement_line_id UNIQUE (statement_line_id),
    CONSTRAINT ck_payment_bank_reviews_outcome CHECK (outcome IN ('matched', 'not_matched')),
    CONSTRAINT ck_payment_bank_reviews_reason CHECK (reason IN ({REASONS})),
    CONSTRAINT ck_payment_bank_reviews_account_reference
        CHECK (account_reference ~ '^[A-Za-z0-9._:-]{{1,64}}$'),
    CONSTRAINT ck_payment_bank_reviews_shape CHECK (
        CASE outcome
            WHEN 'matched' THEN reason IS NULL AND statement_line_id IS NOT NULL
                AND account_reference IS NOT NULL
            ELSE reason IS NOT NULL AND statement_line_id IS NULL AND account_reference IS NULL
        END
    )
);

CREATE TABLE payment_bank_approvals (
    deposit_id push_id NOT NULL,
    approved_by push_id NOT NULL,
    approved_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_bank_approvals PRIMARY KEY (deposit_id),
    CONSTRAINT fk_payment_bank_approvals_deposit_id_payment_bank_reviews
        FOREIGN KEY (deposit_id) REFERENCES payment_bank_reviews (deposit_id)
)
"""

# Reads payment_deposits under the caller's own row policy.
BANK_DEPOSIT_IN_REVIEW = (
    "EXISTS (SELECT FROM payment_deposits AS deposit WHERE deposit.id = {table}.deposit_id "
    "AND deposit.method = 'bank_transfer' AND deposit.status = 'in_review')"
)
EVENT_DEPOSIT = (
    "EXISTS (SELECT FROM payment_deposits AS deposit "
    "WHERE deposit.id = payment_deposit_events.deposit_id)"
)

UPGRADE = [
    "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_method",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_method "
    "CHECK (method IN ('mtn', 'airtel', 'bank_transfer'))",
    "ALTER TABLE payment_deposits ALTER COLUMN payer_msisdn DROP NOT NULL",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_payer_when "
    "CHECK ((method = 'bank_transfer') = (payer_msisdn IS NULL))",
    "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_status",
    f"ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_status "
    f"CHECK (status IN ({STATUSES}))",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_path CHECK ("
    "CASE WHEN method = 'bank_transfer' THEN status IN ('in_review', 'confirmed', 'failed') "
    "ELSE status <> 'in_review' END)",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_bank_provider "
    "CHECK ((method = 'bank_transfer') = (provider = 'bank'))",
    *(
        statement
        for column in ("from_status", "to_status")
        for statement in (
            f"ALTER TABLE payment_deposit_events "
            f"DROP CONSTRAINT ck_payment_deposit_events_{column}",
            f"ALTER TABLE payment_deposit_events ADD CONSTRAINT "
            f"ck_payment_deposit_events_{column} CHECK ({column} IN ({STATUSES}))",
        )
    ),
    "ALTER TABLE payment_deposit_events DROP CONSTRAINT ck_payment_deposit_events_cause",
    f"ALTER TABLE payment_deposit_events ADD CONSTRAINT ck_payment_deposit_events_cause "
    f"CHECK (cause IN ({CAUSES}))",
    guard(ALLOWED_MOVES),
    *[s.strip() for s in TABLES.split(";\n") if s.strip()],
    SETTLE_GUARD,
    "REVOKE ALL ON FUNCTION finvest_payment_bank_settle_guard() FROM PUBLIC",
    "CREATE TRIGGER payment_deposits_bank_settle_guard BEFORE UPDATE ON payment_deposits "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payment_bank_settle_guard()",
    *(
        statement
        for table in ("payment_bank_receipts", "payment_bank_reviews", "payment_bank_approvals")
        for statement in (
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payments_immutable()",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {table} FROM PUBLIC",
            f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}",
            f"GRANT SELECT ON {table} TO {WORKER_ROLE}",
            f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER_ROLE} "
            "USING (true)",
        )
    ),
    # A client starts their own deposit: a mobile money one at the start of its path, a bank
    # transfer in review.
    "DROP POLICY payment_deposits_app_insert ON payment_deposits",
    f"CREATE POLICY payment_deposits_app_insert ON payment_deposits FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({OWN} AND provider_reference IS NULL AND status = CASE method "
    "WHEN 'bank_transfer' THEN 'in_review' ELSE 'created' END)",
    "DROP POLICY payment_deposit_events_app_insert ON payment_deposit_events",
    "CREATE POLICY payment_deposit_events_app_insert ON payment_deposit_events FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({IS_CLIENT} AND cause = 'request' AND from_status IS NULL "
    f"AND to_status IN ('created', 'in_review') AND {EVENT_DEPOSIT})",
    "CREATE POLICY payment_bank_receipts_app_read ON payment_bank_receipts FOR SELECT "
    f"TO {APP_ROLE} USING ({IS_STAFF} OR ({OWN}))",
    "CREATE POLICY payment_bank_receipts_app_insert ON payment_bank_receipts FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({OWN} AND "
    f"{BANK_DEPOSIT_IN_REVIEW.format(table='payment_bank_receipts')})",
    "CREATE POLICY payment_bank_reviews_app_read ON payment_bank_reviews FOR SELECT "
    f"TO {APP_ROLE} USING ({IS_STAFF})",
    "CREATE POLICY payment_bank_reviews_app_insert ON payment_bank_reviews FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({IS_STAFF} AND reviewed_by = finvest_actor_id() AND "
    f"{BANK_DEPOSIT_IN_REVIEW.format(table='payment_bank_reviews')})",
    "CREATE POLICY payment_bank_approvals_app_read ON payment_bank_approvals FOR SELECT "
    f"TO {APP_ROLE} USING ({IS_STAFF})",
    # A second person, approving a match that is still waiting.
    "CREATE POLICY payment_bank_approvals_app_insert ON payment_bank_approvals FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({IS_STAFF} AND approved_by = finvest_actor_id() AND "
    "EXISTS (SELECT FROM payment_bank_reviews AS review "
    "WHERE review.deposit_id = payment_bank_approvals.deposit_id "
    "AND review.outcome = 'matched' AND review.reviewed_by <> finvest_actor_id()) AND "
    f"{BANK_DEPOSIT_IN_REVIEW.format(table='payment_bank_approvals')})",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_deposits WHERE method = 'bank_transfer')
       OR EXISTS (SELECT FROM payment_bank_receipts)
       OR EXISTS (SELECT FROM payment_bank_reviews)
       OR EXISTS (SELECT FROM payment_bank_approvals) THEN
        RAISE EXCEPTION 'refusing to drop bank transfers that hold deposits';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Dropping these would erase the record of money received by bank transfer.
    op.execute(EMPTY_GUARD)
    statements = [
        "DROP POLICY payment_deposit_events_app_insert ON payment_deposit_events",
        "CREATE POLICY payment_deposit_events_app_insert ON payment_deposit_events FOR INSERT "
        f"TO {APP_ROLE} WITH CHECK ({IS_CLIENT} AND cause = 'request' AND from_status IS NULL "
        f"AND to_status = 'created' AND {EVENT_DEPOSIT})",
        "DROP POLICY payment_deposits_app_insert ON payment_deposits",
        f"CREATE POLICY payment_deposits_app_insert ON payment_deposits FOR INSERT TO {APP_ROLE} "
        f"WITH CHECK ({OWN} AND status = 'created' AND provider_reference IS NULL)",
        "DROP TRIGGER payment_deposits_bank_settle_guard ON payment_deposits",
        "DROP TABLE payment_bank_approvals",
        "DROP TABLE payment_bank_reviews",
        "DROP TABLE payment_bank_receipts",
        "DROP FUNCTION finvest_payment_bank_settle_guard()",
        guard(OLD_MOVES),
        "ALTER TABLE payment_deposit_events DROP CONSTRAINT ck_payment_deposit_events_cause",
        f"ALTER TABLE payment_deposit_events ADD CONSTRAINT ck_payment_deposit_events_cause "
        f"CHECK (cause IN ({OLD_CAUSES}))",
        *(
            statement
            for column in ("from_status", "to_status")
            for statement in (
                f"ALTER TABLE payment_deposit_events "
                f"DROP CONSTRAINT ck_payment_deposit_events_{column}",
                f"ALTER TABLE payment_deposit_events ADD CONSTRAINT "
                f"ck_payment_deposit_events_{column} CHECK ({column} IN ({OLD_STATUSES}))",
            )
        ),
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_bank_provider",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_path",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_status",
        f"ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_status "
        f"CHECK (status IN ({OLD_STATUSES}))",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_payer_when",
        "ALTER TABLE payment_deposits ALTER COLUMN payer_msisdn SET NOT NULL",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_method",
        "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_method "
        "CHECK (method IN ('mtn', 'airtel'))",
    ]
    for statement in statements:
        op.execute(statement)
