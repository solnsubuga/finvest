"""Keep the bank account clients pay into by transfer, set by two people on Settings.

Revision ID: 0260_payment_bank_accounts
Revises: 0222_identity_recovery_requests

Until now the account came from the `FINVEST_BANK_TRANSFER_ACCOUNTS` setting of each deploy.
Now staff set it on the console's Settings screen. `payment_bank_accounts` holds every proposed
change, one currency at a time. One member of staff proposes the details and a different one
approves or rejects them; only the proposer can withdraw. PostgreSQL enforces all of it: an
account changed by one person alone could send every client's transfer somewhere else. The
account in use for a currency is its last approved row. A decided row never changes and no
row is ever deleted. See docs/payments.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0260_payment_bank_accounts"
down_revision: str | None = "0222_identity_recovery_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE payment_bank_accounts (
    id push_id NOT NULL,
    currency char(3) NOT NULL,
    bank_name varchar(100) NOT NULL,
    account_name varchar(100) NOT NULL,
    account_number varchar(20) NOT NULL,
    branch varchar(100),
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_payment_bank_accounts PRIMARY KEY (id),
    CONSTRAINT ck_payment_bank_accounts_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_payment_bank_accounts_names
        CHECK (btrim(bank_name) <> '' AND btrim(account_name) <> ''),
    CONSTRAINT ck_payment_bank_accounts_number CHECK (account_number ~ '^[0-9]{6,20}$'),
    CONSTRAINT ck_payment_bank_accounts_branch CHECK (branch IS NULL OR btrim(branch) <> ''),
    CONSTRAINT ck_payment_bank_accounts_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payment_bank_accounts_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_payment_bank_accounts_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)),
    -- Two people. Only the proposer can withdraw, and never approve or reject, their own.
    CONSTRAINT ck_payment_bank_accounts_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
-- One change at a time per currency, so an approval is always of the details the approver read.
CREATE UNIQUE INDEX uq_payment_bank_accounts_open
    ON payment_bank_accounts (currency) WHERE status = 'proposed';
CREATE INDEX ix_payment_bank_accounts_in_use
    ON payment_bank_accounts (currency, decided_at DESC, id DESC) WHERE status = 'approved'
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001). The guard
# names the acting member of staff on every row, so the two-person check compares the people
# whose transactions wrote the proposal and the decision.
GUARD = """
CREATE FUNCTION finvest_payment_bank_accounts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff change the bank account' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a bank account starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided bank account never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
        THEN
            RAISE EXCEPTION 'a bank account is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payment_bank_accounts_guard() FROM PUBLIC",
    "CREATE TRIGGER payment_bank_accounts_guard BEFORE INSERT OR UPDATE ON payment_bank_accounts "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payment_bank_accounts_guard()",
    "REVOKE ALL ON payment_bank_accounts FROM PUBLIC",
    # No DELETE for anyone: who set the account, and when, is part of the record.
    "GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason) "
    f"ON payment_bank_accounts TO {APP_ROLE}",
    f"GRANT SELECT ON payment_bank_accounts TO {WORKER_ROLE}",
    "ALTER TABLE payment_bank_accounts ENABLE ROW LEVEL SECURITY",
    # A client paying by transfer reads the account in use. Proposals and history are for staff.
    "CREATE POLICY payment_bank_accounts_app_read ON payment_bank_accounts FOR SELECT "
    f"TO {APP_ROLE} USING (status = 'approved' OR finvest_actor_kind() = 'staff')",
    "CREATE POLICY payment_bank_accounts_app_insert ON payment_bank_accounts FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'staff')",
    "CREATE POLICY payment_bank_accounts_app_update ON payment_bank_accounts FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff')",
    "CREATE POLICY payment_bank_accounts_worker ON payment_bank_accounts FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_bank_accounts) THEN
        RAISE EXCEPTION 'refusing to drop bank accounts that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Who set the account clients pay into, and when, is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE payment_bank_accounts")
    op.execute("DROP FUNCTION finvest_payment_bank_accounts_guard()")
