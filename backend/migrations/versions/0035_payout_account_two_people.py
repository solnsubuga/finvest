"""Approve a payout account with two people.

Revision ID: 0035_payout_account_two_people
Revises: 0153_email_suppression_lifts

From the owner's standing rule that a staff operation that can have a maker and a checker has
one. Refusing an account stays a one-person step, because it lets no money out.

* `checked_at` and `approved_by` are added. The first member of staff records a check: the name
  they read on the proof, how it compares with the ID name, the statement date, `checked_by` and
  `checked_at`. The account stays `in_review`.
* A different member of staff approves that check exactly as it was recorded, in their own name
  (`approved_by`). The guard refuses an approval with no check, by the person who checked, or
  that changes the check.
* New proof after a refusal starts a new check: the client's step must clear every field of
  the last one, so an old check can never be approved against a new document.
* Only the worker changes `first_paid_at`, now for every step.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035_payout_account_two_people"
down_revision: str | None = "0153_email_suppression_lifts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    "ALTER TABLE payout_destinations ADD COLUMN checked_at timestamptz",
    "ALTER TABLE payout_destinations ADD COLUMN approved_by push_id",
    "ALTER TABLE payout_destinations ADD CONSTRAINT ck_payout_destinations_two_people CHECK ("
    "approved_at IS NULL OR (approved_by IS NOT NULL AND checked_at IS NOT NULL "
    "AND approved_by <> checked_by))",
]

GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION finvest_payout_destinations_guard() RETURNS trigger
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
        IF NEW.first_paid_at IS DISTINCT FROM OLD.first_paid_at AND actor <> 'system' THEN
            RAISE EXCEPTION 'only the worker records a payout' USING ERRCODE = '42501';
        END IF;

        IF actor = 'client' AND step = 'rejected>in_review' THEN
            -- New proof starts a new check: nothing of the last one may carry over to it.
            IF num_nonnulls(NEW.proof_name, NEW.name_match, NEW.statement_date, NEW.checked_by,
                            NEW.checked_at, NEW.rejection_reason, NEW.approved_at,
                            NEW.approved_by) > 0 THEN
                RAISE EXCEPTION 'new proof is checked afresh' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'client' AND step IN ('awaiting_code>awaiting_proof', 'awaiting_proof>in_review')
              OR (actor IN ('client', 'staff') AND NEW.status = 'removed') THEN
            -- The client's other steps, and removal by either, touch none of the staff's evidence.
            IF (NEW.proof_name, NEW.name_match, NEW.statement_date, NEW.checked_by, NEW.checked_at,
                NEW.rejection_reason, NEW.approved_at, NEW.approved_by)
                   IS DISTINCT FROM
               (OLD.proof_name, OLD.name_match, OLD.statement_date, OLD.checked_by, OLD.checked_at,
                OLD.rejection_reason, OLD.approved_at, OLD.approved_by) THEN
                RAISE EXCEPTION 'only a staff check sets the evidence of a payout destination'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step = 'in_review>in_review' THEN
            -- The first person records what they read on the proof. Nothing is approved yet.
            IF NEW.checked_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a staff check is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.checked_at IS NULL OR NEW.proof_name IS NULL
               OR NEW.name_match NOT IN ('exact', 'partial')
               OR abs(extract(epoch FROM NEW.checked_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a check records a matching name, stamped with the current time'
                    USING ERRCODE = '42501';
            END IF;
            IF num_nonnulls(NEW.approved_at, NEW.approved_by, NEW.rejection_reason) > 0
               OR (NEW.proof_kind, NEW.proof_document_id)
                  IS DISTINCT FROM (OLD.proof_kind, OLD.proof_document_id) THEN
                RAISE EXCEPTION 'a check changes only the check' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step = 'in_review>approved' THEN
            -- A second person approves the check exactly as the first recorded it.
            IF OLD.checked_by IS NULL OR OLD.checked_at IS NULL THEN
                RAISE EXCEPTION 'a payout destination is checked before it is approved'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.approved_by IS DISTINCT FROM finvest_actor_id()
               OR NEW.approved_by = OLD.checked_by THEN
                RAISE EXCEPTION 'a different member of staff approves the check, in their own name'
                    USING ERRCODE = '42501';
            END IF;
            IF (NEW.checked_by, NEW.checked_at, NEW.proof_name, NEW.name_match,
                NEW.statement_date, NEW.proof_kind, NEW.proof_document_id, NEW.rejection_reason)
                   IS DISTINCT FROM
               (OLD.checked_by, OLD.checked_at, OLD.proof_name, OLD.name_match,
                OLD.statement_date, OLD.proof_kind, OLD.proof_document_id, OLD.rejection_reason)
            THEN
                RAISE EXCEPTION 'an approval does not change the check' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step = 'in_review>rejected' THEN
            -- One person may refuse.
            IF NEW.checked_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a staff check is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.approved_by IS NOT NULL THEN
                RAISE EXCEPTION 'a refusal is not an approval' USING ERRCODE = '42501';
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

# The guard as 0030 left it, restored on the way down.
ONE_PERSON_GUARD = """
CREATE OR REPLACE FUNCTION finvest_payout_destinations_guard() RETURNS trigger
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


def upgrade() -> None:
    for statement in COLUMNS:
        op.execute(statement)
    op.execute(GUARD_FUNCTION)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM payout_destinations WHERE checked_at IS NOT NULL OR approved_by IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'refusing to drop the checks and approvals of payout destinations';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(ONE_PERSON_GUARD)
    op.execute("ALTER TABLE payout_destinations DROP CONSTRAINT ck_payout_destinations_two_people")
    op.execute("ALTER TABLE payout_destinations DROP COLUMN approved_by")
    op.execute("ALTER TABLE payout_destinations DROP COLUMN checked_at")
