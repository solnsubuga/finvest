"""Add payout destinations: a client's own mobile money numbers and bank accounts.

Revision ID: 0030_payout_destinations
Revises: 0050_kyc

The steps in docs/payouts.md are enforced here as well as in the application:

* Who may do what. A trigger allows a client transaction only the client's steps (confirm the
  code, submit proof, remove), a staff transaction only the staff steps (approve, reject,
  remove) and the worker only the note of the first payout. No one can approve their own
  destination from the client app, whatever the application code does.
* A different name is never approved. An approved row must carry an exact or partial name
  match, the staff member who checked it and the time of approval.
* The account never changes. Whose it is, its kind and its number are fixed at creation, so an
  approved destination cannot be pointed somewhere else.
* Removed is final, and rows are never deleted.
* Clients are fenced. Row policies show a client transaction only that client's destinations.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_payout_destinations"
down_revision: str | None = "0050_kyc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE payout_destinations (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    kind varchar(16) NOT NULL,
    network varchar(16),
    bank_code varchar(32),
    account_number varchar(20) NOT NULL,
    holder_name varchar(120) NOT NULL,
    status varchar(16) NOT NULL,
    proof_kind varchar(32),
    proof_document_id varchar(64),
    proof_name varchar(120),
    name_match varchar(16),
    statement_date date,
    checked_by push_id,
    approved_at timestamptz,
    rejection_reason varchar(32),
    first_paid_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_payout_destinations PRIMARY KEY (id),
    CONSTRAINT ck_payout_destinations_kind CHECK (
        (kind = 'mobile_money' AND network IN ('mtn', 'airtel') AND bank_code IS NULL
            AND account_number ~ '^256[0-9]{9}$')
        OR (kind = 'bank' AND network IS NULL AND bank_code ~ '^[a-z][a-z0-9_]{1,31}$'
            AND account_number ~ '^[0-9]{6,20}$')
    ),
    CONSTRAINT ck_payout_destinations_holder_name CHECK (length(btrim(holder_name)) > 0),
    CONSTRAINT ck_payout_destinations_status CHECK (status IN (
        'awaiting_code', 'awaiting_proof', 'in_review', 'approved', 'rejected', 'removed')),
    CONSTRAINT ck_payout_destinations_proof_kind CHECK (
        (kind = 'bank' AND proof_kind IN ('bank_statement', 'bank_letter', 'bank_card'))
        OR (kind = 'mobile_money' AND proof_kind = 'network_registered_name')
    ),
    CONSTRAINT ck_payout_destinations_proof_document_id
        CHECK (proof_document_id ~ '^[A-Za-z0-9._:-]{1,64}$'),
    CONSTRAINT ck_payout_destinations_name_match
        CHECK (name_match IN ('exact', 'partial', 'different')),
    CONSTRAINT ck_payout_destinations_rejection_reason CHECK (rejection_reason IN (
        'name_different', 'proof_unreadable', 'proof_too_old', 'proof_wrong_account',
        'proof_wrong_document', 'suspected_fraud')),
    CONSTRAINT ck_payout_destinations_in_review
        CHECK (status NOT IN ('in_review', 'approved') OR proof_kind IS NOT NULL),
    CONSTRAINT ck_payout_destinations_approved CHECK (
        (status = 'approved') = (approved_at IS NOT NULL)
        OR (status = 'removed' AND approved_at IS NOT NULL)
    ),
    CONSTRAINT ck_payout_destinations_approval_evidence CHECK (
        approved_at IS NULL
        OR (checked_by IS NOT NULL AND proof_name IS NOT NULL
            AND name_match IN ('exact', 'partial')
            AND (proof_kind <> 'bank_statement' OR statement_date IS NOT NULL))
    ),
    CONSTRAINT ck_payout_destinations_rejected
        CHECK (status <> 'rejected' OR (rejection_reason IS NOT NULL AND checked_by IS NOT NULL)),
    CONSTRAINT ck_payout_destinations_first_paid_at
        CHECK (first_paid_at IS NULL OR (approved_at IS NOT NULL AND first_paid_at >= approved_at))
);
CREATE UNIQUE INDEX uq_payout_destinations_live
    ON payout_destinations (client_id, kind, COALESCE(bank_code, ''), account_number)
    WHERE status <> 'removed';
CREATE INDEX ix_payout_destinations_client_id ON payout_destinations (client_id, created_at DESC);
CREATE INDEX ix_payout_destinations_review
    ON payout_destinations (updated_at, id) WHERE status = 'in_review'
"""

GUARD_FUNCTION = """
CREATE FUNCTION finvest_payout_destinations_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        step text := OLD.status || '>' || NEW.status;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'payout destinations are never deleted' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'removed' THEN
            RAISE EXCEPTION 'a removed payout destination is final' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.client_id, NEW.kind, NEW.account_number, NEW.holder_name, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.kind, OLD.account_number, OLD.holder_name, OLD.created_at)
           OR NEW.network IS DISTINCT FROM OLD.network
           OR NEW.bank_code IS DISTINCT FROM OLD.bank_code THEN
            RAISE EXCEPTION 'the account of a payout destination never changes'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.first_paid_at IS NOT NULL AND NEW.first_paid_at IS DISTINCT FROM OLD.first_paid_at THEN
            RAISE EXCEPTION 'the first payout is recorded once' USING ERRCODE = '42501';
        END IF;

        IF actor = 'client' AND step IN (
            'awaiting_code>awaiting_proof', 'awaiting_proof>in_review', 'rejected>in_review'
        ) OR (actor IN ('client', 'staff') AND NEW.status = 'removed') THEN
            -- The client's steps, and removal by either, touch none of the staff's evidence.
            IF (NEW.proof_name, NEW.name_match, NEW.statement_date, NEW.approved_at,
                NEW.first_paid_at)
                   IS DISTINCT FROM
               (OLD.proof_name, OLD.name_match, OLD.statement_date, OLD.approved_at,
                OLD.first_paid_at)
               OR (NEW.status <> 'in_review' AND (NEW.checked_by, NEW.rejection_reason)
                   IS DISTINCT FROM (OLD.checked_by, OLD.rejection_reason)) THEN
                RAISE EXCEPTION 'only a staff check sets the evidence of a payout destination'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN ('in_review>approved', 'in_review>rejected') THEN
            IF NEW.checked_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a staff check is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.first_paid_at IS DISTINCT FROM OLD.first_paid_at THEN
                RAISE EXCEPTION 'only the worker records a payout' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'system' AND step = 'approved>approved'
              AND OLD.first_paid_at IS NULL AND NEW.first_paid_at IS NOT NULL
              AND to_jsonb(NEW) - 'first_paid_at' - 'updated_at'
                  = to_jsonb(OLD) - 'first_paid_at' - 'updated_at' THEN
            NULL;
        ELSE
            RAISE EXCEPTION 'this step is not allowed for a payout destination'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payout_destinations_guard() FROM PUBLIC",
    "CREATE TRIGGER payout_destinations_guard BEFORE UPDATE OR DELETE ON payout_destinations "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payout_destinations_guard()",
    "CREATE TRIGGER payout_destinations_no_truncate BEFORE TRUNCATE ON payout_destinations "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payout_destinations_guard()",
    "ALTER TABLE payout_destinations ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON payout_destinations FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE ON payout_destinations TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE ON payout_destinations TO {WORKER_ROLE}",
    # A client sees and changes only their own destinations. Staff see all of them. Any other
    # API transaction sees nothing.
    f"CREATE POLICY payout_destinations_app_read ON payout_destinations FOR SELECT TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff' "
    "OR (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()))",
    # Only the client adds a destination, for themselves, and it starts at the first step.
    f"CREATE POLICY payout_destinations_app_insert ON payout_destinations FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
    "AND status = 'awaiting_code' AND approved_at IS NULL AND first_paid_at IS NULL "
    "AND checked_by IS NULL AND name_match IS NULL AND proof_kind IS NULL)",
    f"CREATE POLICY payout_destinations_app_update ON payout_destinations FOR UPDATE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff' "
    "OR (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()))",
    f"CREATE POLICY payout_destinations_worker_read ON payout_destinations FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY payout_destinations_worker_update ON payout_destinations FOR UPDATE "
    f"TO {WORKER_ROLE} USING (finvest_actor_kind() = 'system')",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GUARD_FUNCTION, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_destinations) THEN
        RAISE EXCEPTION 'refusing to drop payout destinations that hold rows';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE payout_destinations")
    op.execute("DROP FUNCTION finvest_payout_destinations_guard()")
