"""Prove a mobile money number by the name its network holds, read through ioTec Verify.

Revision ID: 0190_payout_name_lookups
Revises: 0182_transaction_monitoring

After the client enters the code texted to a mobile money number, the worker asks ioTec Verify
for the name the network holds for it and compares it with the name on the client's verified ID.
The same names, or one with a name left out, send the number to staff, who check and approve it
as two people. A different name, or no name at all, refuses it at once. No member of staff can
type a name in for a number. See docs/payouts.md#mobile-money-numbers.

`payout_name_lookups` holds each lookup: asked for by the client (in the transaction that
spends the code, or later to ask again), sent and answered by the worker. Its ID is the
`referenceId` ioTec is sent, and the destination's `proof_document_id` once it is decided.

What the database enforces:

* A client asks only for their own number while it waits for its name, at most three times.
  Only the worker records what ioTec said, and an answer is final.
* A number is never proved by the client. The client's proof steps are for bank accounts only.
* The worker moves a number on only from a lookup that answered for it: to staff with the
  name the network gave, matching exactly or partly, or refused for a different name or none.
* Staff check a number with the network's name as it was recorded. A check cannot change it.
* A refusal from the network's answer is final, like a refusal for suspected fraud.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0190_payout_name_lookups"
down_revision: str | None = "0182_transaction_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = [
    """
CREATE TABLE payout_name_lookups (
    id push_id NOT NULL,
    destination_id push_id NOT NULL,
    number smallint NOT NULL,
    status varchar(16) NOT NULL,
    request_id uuid,
    registered_name varchar(120),
    error_code varchar(64),
    created_at timestamptz NOT NULL,
    answered_at timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_payout_name_lookups PRIMARY KEY (id),
    CONSTRAINT fk_payout_name_lookups_destination FOREIGN KEY (destination_id)
        REFERENCES payout_destinations (id),
    CONSTRAINT uq_payout_name_lookups_number UNIQUE (destination_id, number),
    CONSTRAINT ck_payout_name_lookups_number CHECK (number BETWEEN 1 AND 3),
    CONSTRAINT ck_payout_name_lookups_status CHECK (
        status IN ('queued', 'sent', 'found', 'no_name', 'unanswered')),
    CONSTRAINT ck_payout_name_lookups_name CHECK (
        (status = 'found') = (registered_name IS NOT NULL)
        AND (registered_name IS NULL OR length(btrim(registered_name)) > 0)),
    CONSTRAINT ck_payout_name_lookups_request CHECK (
        status NOT IN ('sent', 'found', 'no_name') OR request_id IS NOT NULL),
    CONSTRAINT ck_payout_name_lookups_answered CHECK (
        (status IN ('found', 'no_name', 'unanswered')) = (answered_at IS NOT NULL)),
    CONSTRAINT ck_payout_name_lookups_error_code CHECK (error_code ~ '^[a-z0-9_]{1,64}$')
)
""",
    "CREATE INDEX ix_payout_name_lookups_destination "
    "ON payout_name_lookups (destination_id, number DESC)",
]

LOOKUP_GUARD = """
CREATE FUNCTION public.finvest_payout_name_lookups_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        step text;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'name lookups are never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the worker records a name lookup' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.destination_id, NEW.number, NEW.created_at)
               IS DISTINCT FROM (OLD.id, OLD.destination_id, OLD.number, OLD.created_at) THEN
            RAISE EXCEPTION 'a name lookup is for one number, once' USING ERRCODE = '42501';
        END IF;
        IF OLD.request_id IS NOT NULL AND NEW.request_id IS DISTINCT FROM OLD.request_id THEN
            RAISE EXCEPTION 'a name lookup keeps its request' USING ERRCODE = '42501';
        END IF;
        step := OLD.status || '>' || NEW.status;
        IF step NOT IN ('queued>sent', 'queued>unanswered', 'sent>found', 'sent>no_name',
                        'sent>unanswered') THEN
            RAISE EXCEPTION 'an answered name lookup is final' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

MINE = (
    "EXISTS (SELECT 1 FROM public.payout_destinations d WHERE d.id = destination_id "
    "AND d.client_id = public.finvest_actor_id())"
)

LOOKUP_RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_payout_name_lookups_guard() FROM PUBLIC",
    "CREATE TRIGGER payout_name_lookups_guard BEFORE UPDATE OR DELETE ON payout_name_lookups "
    "FOR EACH ROW EXECUTE FUNCTION public.finvest_payout_name_lookups_guard()",
    "CREATE TRIGGER payout_name_lookups_no_truncate BEFORE TRUNCATE ON payout_name_lookups "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_payout_name_lookups_guard()",
    "ALTER TABLE payout_name_lookups ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON payout_name_lookups FROM PUBLIC",
    f"GRANT SELECT, INSERT ON payout_name_lookups TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE ON payout_name_lookups TO {WORKER_ROLE}",
    # Staff see every lookup; a client sees the lookups of their own numbers.
    f"CREATE POLICY payout_name_lookups_app_read ON payout_name_lookups FOR SELECT TO {APP_ROLE} "
    "USING (public.finvest_actor_kind() = 'staff' "
    f"OR (public.finvest_actor_kind() = 'client' AND {MINE}))",
    # A client asks for a lookup of their own number while it waits for its name.
    f"CREATE POLICY payout_name_lookups_app_insert ON payout_name_lookups FOR INSERT "  # noqa: S608
    f"TO {APP_ROLE} WITH CHECK (public.finvest_actor_kind() = 'client' "
    "AND status = 'queued' AND request_id IS NULL AND registered_name IS NULL "
    "AND answered_at IS NULL AND error_code IS NULL "
    "AND EXISTS (SELECT 1 FROM public.payout_destinations d WHERE d.id = destination_id "
    "AND d.client_id = public.finvest_actor_id() AND d.kind = 'mobile_money' "
    "AND d.status = 'awaiting_proof'))",
    f"CREATE POLICY payout_name_lookups_worker_read ON payout_name_lookups FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY payout_name_lookups_worker_update ON payout_name_lookups FOR UPDATE "
    f"TO {WORKER_ROLE} USING (public.finvest_actor_kind() = 'system')",
]

CONSTRAINTS = [
    "ALTER TABLE payout_destinations DROP CONSTRAINT ck_payout_destinations_rejection_reason",
    "ALTER TABLE payout_destinations ADD CONSTRAINT ck_payout_destinations_rejection_reason "
    "CHECK (rejection_reason IN ('name_different', 'proof_unreadable', 'proof_too_old', "
    "'proof_wrong_account', 'proof_wrong_document', 'suspected_fraud', "
    "'network_name_unavailable'))",
    "ALTER TABLE payout_destinations DROP CONSTRAINT ck_payout_destinations_rejected",
    # A refusal from the network's answer is signed by nobody: the worker made it.
    "ALTER TABLE payout_destinations ADD CONSTRAINT ck_payout_destinations_rejected CHECK ("
    "status <> 'rejected' OR (rejection_reason IS NOT NULL AND (checked_by IS NOT NULL "
    "OR (kind = 'mobile_money' AND proof_kind = 'network_registered_name' "
    "AND rejection_reason IN ('name_different', 'network_name_unavailable')))))",
]

OLD_CONSTRAINTS = [
    "ALTER TABLE payout_destinations DROP CONSTRAINT ck_payout_destinations_rejected",
    "ALTER TABLE payout_destinations ADD CONSTRAINT ck_payout_destinations_rejected "
    "CHECK (status <> 'rejected' OR (rejection_reason IS NOT NULL AND checked_by IS NOT NULL))",
    "ALTER TABLE payout_destinations DROP CONSTRAINT ck_payout_destinations_rejection_reason",
    "ALTER TABLE payout_destinations ADD CONSTRAINT ck_payout_destinations_rejection_reason "
    "CHECK (rejection_reason IN ('name_different', 'proof_unreadable', 'proof_too_old', "
    "'proof_wrong_account', 'proof_wrong_document', 'suspected_fraud'))",
]

GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_payout_destinations_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
        step text := OLD.status || '>' || NEW.status;
        lookup_status text;
        lookup_name text;
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
        IF actor = 'client' AND NEW.kind = 'mobile_money'
           AND step IN ('awaiting_proof>in_review', 'rejected>in_review') THEN
            -- A number is proved by the name its network holds, never by the client.
            RAISE EXCEPTION 'a mobile money number is proved by its network'
                USING ERRCODE = '42501';
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
        ELSIF actor = 'system' AND NEW.kind = 'mobile_money'
              AND step IN ('awaiting_proof>in_review', 'awaiting_proof>rejected') THEN
            -- The worker moves a number on from the answer of one of its own lookups.
            SELECT status, registered_name INTO lookup_status, lookup_name
                FROM public.payout_name_lookups
                WHERE id = NEW.proof_document_id AND destination_id = NEW.id;
            IF NEW.proof_kind IS DISTINCT FROM 'network_registered_name'
               OR num_nonnulls(NEW.statement_date, NEW.checked_by, NEW.checked_at,
                               NEW.approved_at, NEW.approved_by) > 0 THEN
                RAISE EXCEPTION 'the network''s answer is the only evidence of a number'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT (
                (step = 'awaiting_proof>in_review' AND lookup_status = 'found'
                 AND NEW.proof_name = lookup_name AND NEW.name_match IN ('exact', 'partial')
                 AND NEW.rejection_reason IS NULL)
                OR (step = 'awaiting_proof>rejected' AND lookup_status = 'found'
                    AND NEW.proof_name = lookup_name AND NEW.name_match = 'different'
                    AND NEW.rejection_reason = 'name_different')
                OR (step = 'awaiting_proof>rejected' AND lookup_status = 'no_name'
                    AND NEW.proof_name IS NULL AND NEW.name_match IS NULL
                    AND NEW.rejection_reason = 'network_name_unavailable')
            ) THEN
                RAISE EXCEPTION 'a number moves on only as the network''s answer says'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step = 'in_review>in_review' THEN
            -- The first person records what they read on the proof. Nothing is approved yet.
            IF NEW.checked_by IS DISTINCT FROM public.finvest_actor_id() THEN
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
            IF OLD.proof_kind = 'network_registered_name'
               AND (NEW.proof_name, NEW.name_match) IS DISTINCT FROM (OLD.proof_name, OLD.name_match)
            THEN
                RAISE EXCEPTION 'a number is checked with the name its network gave'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step = 'in_review>approved' THEN
            -- A second person approves the check exactly as the first recorded it.
            IF OLD.checked_by IS NULL OR OLD.checked_at IS NULL THEN
                RAISE EXCEPTION 'a payout destination is checked before it is approved'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.approved_by IS DISTINCT FROM public.finvest_actor_id()
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
            IF NEW.checked_by IS DISTINCT FROM public.finvest_actor_id() THEN
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


def _previous_guard() -> str:
    """The guard as 0036 left it, restored on the way down. Read from 0036 itself, so the two
    can never differ."""
    path = Path(__file__).with_name("0036_payout_guard_search_path.py")
    spec = importlib.util.spec_from_file_location("finvest_migration_0036", path)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    guard: str = module.GUARD
    return guard


def upgrade() -> None:
    for statement in [*TABLE, LOOKUP_GUARD, *LOOKUP_RULES, *CONSTRAINTS, GUARD]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_name_lookups) OR EXISTS (
        SELECT FROM payout_destinations WHERE rejection_reason = 'network_name_unavailable'
            OR (status = 'rejected' AND checked_by IS NULL)
    ) THEN
        RAISE EXCEPTION 'refusing to drop the network name lookups of payout destinations';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(_previous_guard())
    for statement in OLD_CONSTRAINTS:
        op.execute(statement)
    op.execute("DROP TABLE payout_name_lookups")
    op.execute("DROP FUNCTION public.finvest_payout_name_lookups_guard()")
