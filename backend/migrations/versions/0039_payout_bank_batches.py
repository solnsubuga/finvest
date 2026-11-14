"""Pay bank payouts from the trust account in batches, and settle each one from the statement.

Revision ID: 0039_payout_bank_batches
Revises: 0073_migration_difference_places

A bank payout is not sent by the worker. Staff pay it from the trust bank account by uploading
a transfer file in the bank's portal. From the owner's standing rule that staff operations that
can be maker and checker must be, each money step takes two people:

* One member of staff prepares a batch of queued bank payouts in one currency. A different one
  approves it, and in the same transaction every payout in it moves from queued to sent. Only
  then can the transfer file be downloaded. The preparer may withdraw it, and anyone else may
  decline it; either way its payouts are free for another batch.
* A payout is in at most one batch that is prepared or approved. Items are written only in the
  transaction that prepares their batch, and never change.
* Once the money has left, one member of staff proposes how each payout ended, and a different
  one approves it. In the same transaction the payout becomes paid (settled from the trust
  account) or failed (parked in the client's "payout pending" account).
  * Paid names the trust statement line that took the money: going out, for the payout's amount
    and currency, on or after the day the batch was approved, and named by no other open or
    approved settlement.
  * Failed names a trust statement at least two days after the batch was approved. Every day
    from the approval to it must be imported, and none may hold a line going out for the
    payout's amount that no settlement accounts for, so a payout that did leave is never parked
    to be paid twice.
* The payouts guard admits a staff step from queued to sent only for a bank payout in a batch
  the same person approved, and from sent to paid or failed only with an approved resolution or
  an approved bank settlement by the same person. The worker takes no step for a bank payout.
* Nothing is deleted, and a decided batch or settlement never changes. Only staff see them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_payout_bank_batches"
down_revision: str | None = "0073_migration_difference_places"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
B = "payout_bank_batches"
I = "payout_bank_batch_items"  # noqa: E741 - the table's initial, as B and S
S = "payout_bank_settlements"

TABLES = """
CREATE TABLE payout_bank_batches (
    id push_id NOT NULL,
    currency char(3) NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(16) NOT NULL,
    prepared_by push_id NOT NULL,
    prepared_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_payout_bank_batches PRIMARY KEY (id),
    CONSTRAINT ck_payout_bank_batches_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_payout_bank_batches_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payout_bank_batches_status
        CHECK (status IN ('prepared', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_payout_bank_batches_decided CHECK (
        (status = 'prepared') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
    ),
    CONSTRAINT ck_payout_bank_batches_two_people CHECK (
        status NOT IN ('approved', 'declined') OR decided_by <> prepared_by
    ),
    CONSTRAINT ck_payout_bank_batches_withdrawn_by_preparer
        CHECK (status <> 'withdrawn' OR decided_by = prepared_by),
    CONSTRAINT ck_payout_bank_batches_decision_reason
        CHECK (status = 'prepared' OR btrim(COALESCE(decision_reason, '')) <> '')
);
CREATE INDEX ix_payout_bank_batches_status ON payout_bank_batches (status, prepared_at);
CREATE TABLE payout_bank_batch_items (
    batch_id push_id NOT NULL,
    payout_id push_id NOT NULL,
    CONSTRAINT pk_payout_bank_batch_items PRIMARY KEY (batch_id, payout_id),
    CONSTRAINT fk_payout_bank_batch_items_batch
        FOREIGN KEY (batch_id) REFERENCES payout_bank_batches (id),
    CONSTRAINT fk_payout_bank_batch_items_payout FOREIGN KEY (payout_id) REFERENCES payouts (id)
);
CREATE INDEX ix_payout_bank_batch_items_payout ON payout_bank_batch_items (payout_id);
CREATE TABLE payout_bank_settlements (
    id push_id NOT NULL,
    payout_id push_id NOT NULL,
    batch_id push_id NOT NULL,
    outcome varchar(8) NOT NULL,
    statement_line_id push_id,
    statement_id push_id,
    reason varchar(500) NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_payout_bank_settlements PRIMARY KEY (id),
    CONSTRAINT fk_payout_bank_settlements_item
        FOREIGN KEY (batch_id, payout_id) REFERENCES payout_bank_batch_items (batch_id, payout_id),
    CONSTRAINT fk_payout_bank_settlements_line
        FOREIGN KEY (statement_line_id) REFERENCES reconciliation_statement_lines (id),
    CONSTRAINT fk_payout_bank_settlements_statement
        FOREIGN KEY (statement_id) REFERENCES reconciliation_statements (id),
    CONSTRAINT ck_payout_bank_settlements_outcome CHECK (outcome IN ('paid', 'failed')),
    CONSTRAINT ck_payout_bank_settlements_evidence CHECK (
        (outcome = 'paid') = (statement_line_id IS NOT NULL)
        AND (outcome = 'failed') = (statement_id IS NOT NULL)
    ),
    CONSTRAINT ck_payout_bank_settlements_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payout_bank_settlements_status
        CHECK (status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_payout_bank_settlements_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
    ),
    CONSTRAINT ck_payout_bank_settlements_two_people CHECK (
        status NOT IN ('approved', 'declined') OR decided_by <> proposed_by
    ),
    CONSTRAINT ck_payout_bank_settlements_withdrawn_by_proposer
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payout_bank_settlements_decision_reason
        CHECK (status = 'proposed' OR btrim(COALESCE(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_payout_bank_settlements_open ON payout_bank_settlements (payout_id)
    WHERE status = 'proposed';
CREATE UNIQUE INDEX uq_payout_bank_settlements_approved ON payout_bank_settlements (payout_id)
    WHERE status = 'approved';
CREATE UNIQUE INDEX uq_payout_bank_settlements_line ON payout_bank_settlements (statement_line_id)
    WHERE status IN ('proposed', 'approved')
"""

BATCHES_GUARD = """
CREATE FUNCTION public.finvest_payout_bank_batches_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a bank payout batch is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff pay a bank payout' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'prepared' OR NEW.prepared_by IS DISTINCT FROM public.finvest_actor_id()
               OR abs(extract(epoch FROM NEW.prepared_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a batch starts prepared, by the person signed in, now'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'prepared' OR NEW.status = 'prepared' THEN
            RAISE EXCEPTION 'a prepared batch is decided once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.currency, NEW.reason, NEW.prepared_by, NEW.prepared_at)
           IS DISTINCT FROM (OLD.id, OLD.currency, OLD.reason, OLD.prepared_by, OLD.prepared_at)
        THEN
            RAISE EXCEPTION 'what was prepared does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
           OR abs(extract(epoch FROM NEW.decided_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'a batch is decided by the person signed in, now'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

ITEMS_GUARD = """
CREATE FUNCTION public.finvest_payout_bank_batch_items_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        batch public.payout_bank_batches;
        payout public.payouts;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a batch item never changes and is never deleted'
                USING ERRCODE = '42501';
        END IF;
        SELECT * INTO batch FROM public.payout_bank_batches WHERE id = NEW.batch_id;
        -- Only in the transaction that prepared the batch, so an approver approves what they saw.
        IF NOT FOUND OR batch.status <> 'prepared'
           OR batch.prepared_by IS DISTINCT FROM public.finvest_actor_id()
           OR (SELECT xmin FROM public.payout_bank_batches WHERE id = NEW.batch_id)::text
              IS DISTINCT FROM (pg_current_xact_id()::xid)::text THEN
            RAISE EXCEPTION 'items are added only as their batch is prepared'
                USING ERRCODE = '42501';
        END IF;
        -- Locked, so two batches cannot both take the same payout.
        SELECT * INTO payout FROM public.payouts WHERE id = NEW.payout_id FOR UPDATE;
        IF NOT FOUND OR payout.kind <> 'bank' OR payout.status <> 'queued'
           OR payout.currency <> batch.currency THEN
            RAISE EXCEPTION 'a batch holds queued bank payouts in its currency'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT FROM public.payout_bank_batch_items AS item
              JOIN public.payout_bank_batches AS other ON other.id = item.batch_id
             WHERE item.payout_id = NEW.payout_id AND other.status IN ('prepared', 'approved')
        ) THEN
            RAISE EXCEPTION 'a payout is in one batch at a time' USING ERRCODE = '23505';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# At commit, an approved batch's payouts have all left the queue.
BATCH_SENT_FUNCTION = """
CREATE FUNCTION public.finvest_payout_bank_batches_sent() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF EXISTS (
            SELECT FROM public.payout_bank_batch_items AS item
              JOIN public.payouts AS payout ON payout.id = item.payout_id
             WHERE item.batch_id = NEW.id AND payout.status = 'queued'
        ) THEN
            RAISE EXCEPTION 'an approved batch sends its payouts in the same transaction'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

# Whether the trust statement still shows what the settlement claims. Read at proposal and
# approval.
EVIDENCE_FUNCTION = """
CREATE FUNCTION public.finvest_payout_bank_settlement_evidence(
    settlement public.payout_bank_settlements
) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        payout public.payouts;
        approved_on date;
        cited public.reconciliation_statements;
    BEGIN
        SELECT * INTO payout FROM public.payouts WHERE id = settlement.payout_id;
        IF NOT FOUND OR payout.kind <> 'bank' OR payout.status <> 'sent' THEN
            RETURN false;
        END IF;
        SELECT (decided_at AT TIME ZONE 'Africa/Kampala')::date INTO approved_on
          FROM public.payout_bank_batches
         WHERE id = settlement.batch_id AND status = 'approved';
        IF approved_on IS NULL THEN
            RETURN false;
        END IF;
        IF settlement.outcome = 'paid' THEN
            RETURN EXISTS (
                SELECT FROM public.reconciliation_statement_lines AS line
                  JOIN public.reconciliation_statements AS statement
                    ON statement.id = line.statement_id
                 WHERE line.id = settlement.statement_line_id AND statement.source = 'bank'
                   AND statement.account_reference = 'trust'
                   AND statement.currency = payout.currency AND line.direction = 'out'
                   AND line.amount_minor = payout.amount_minor
                   AND line.value_date >= approved_on
            );
        END IF;
        SELECT * INTO cited FROM public.reconciliation_statements
         WHERE id = settlement.statement_id AND source = 'bank' AND account_reference = 'trust'
           AND currency = payout.currency AND business_date >= approved_on + 2;
        IF NOT FOUND THEN
            RETURN false;
        END IF;
        -- Every day from the approval to the cited one is imported, and no line on them going
        -- out for this amount is unaccounted for, so a payout that left cannot be parked.
        RETURN (
                SELECT count(DISTINCT business_date) FROM public.reconciliation_statements
                 WHERE source = 'bank' AND account_reference = 'trust'
                   AND currency = payout.currency
                   AND business_date BETWEEN approved_on AND cited.business_date
            ) = cited.business_date - approved_on + 1
            AND NOT EXISTS (
                SELECT FROM public.reconciliation_statement_lines AS line
                  JOIN public.reconciliation_statements AS statement
                    ON statement.id = line.statement_id
                 WHERE statement.source = 'bank' AND statement.account_reference = 'trust'
                   AND statement.currency = payout.currency
                   AND statement.business_date BETWEEN approved_on AND cited.business_date
                   AND line.direction = 'out' AND line.amount_minor = payout.amount_minor
                   AND NOT EXISTS (
                       SELECT FROM public.payout_bank_settlements AS other
                        WHERE other.statement_line_id = line.id
                          AND other.status = 'approved'
                   )
            );
    END;
    $$
"""

SETTLEMENTS_GUARD = """
CREATE FUNCTION public.finvest_payout_bank_settlements_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a bank payout settlement is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff settle a bank payout' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id()
               OR abs(extract(epoch FROM NEW.proposed_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a settlement starts proposed, by the person signed in, now'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT public.finvest_payout_bank_settlement_evidence(NEW) THEN
                RAISE EXCEPTION 'the statement does not show this outcome for this payout'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed' THEN
            RAISE EXCEPTION 'a proposed settlement is decided once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.payout_id, NEW.batch_id, NEW.outcome, NEW.statement_line_id,
            NEW.statement_id, NEW.reason, NEW.proposed_by, NEW.proposed_at)
           IS DISTINCT FROM
           (OLD.id, OLD.payout_id, OLD.batch_id, OLD.outcome, OLD.statement_line_id,
            OLD.statement_id, OLD.reason, OLD.proposed_by, OLD.proposed_at) THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
           OR abs(extract(epoch FROM NEW.decided_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'a settlement is decided by the person signed in, now'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' AND NOT public.finvest_payout_bank_settlement_evidence(NEW)
        THEN
            RAISE EXCEPTION 'the statement no longer shows this outcome for this payout'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# At commit, an approved settlement's payout must have reached that outcome.
SETTLED_FUNCTION = """
CREATE FUNCTION public.finvest_payout_bank_settlements_settled() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT FROM public.payouts WHERE id = NEW.payout_id AND status = NEW.outcome
        ) THEN
            RAISE EXCEPTION 'an approved settlement moves its payout in the same transaction'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

NEEDS_A_SEND_ATTEMPT = """
CREATE OR REPLACE FUNCTION public.finvest_payouts_need_a_send_attempt() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.kind = 'bank' THEN
            -- A bank payout leaves the queue through a batch a second person approved.
            IF NOT EXISTS (
                SELECT FROM public.payout_bank_batch_items AS item
                  JOIN public.payout_bank_batches AS batch ON batch.id = item.batch_id
                 WHERE item.payout_id = NEW.id AND batch.status = 'approved'
            ) THEN
                RAISE EXCEPTION 'a bank payout leaves the queue only in an approved batch'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NOT EXISTS (
            SELECT FROM public.payout_send_attempts
             WHERE payout_id = NEW.id AND (outcome IS NULL OR outcome <> 'deferred')
        ) THEN
            RAISE EXCEPTION 'a payout leaves the queue only through a send attempt'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

OLD_NEEDS_A_SEND_ATTEMPT = """
CREATE OR REPLACE FUNCTION public.finvest_payouts_need_a_send_attempt() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT FROM public.payout_send_attempts
             WHERE payout_id = NEW.id AND (outcome IS NULL OR outcome <> 'deferred')
        ) THEN
            RAISE EXCEPTION 'a payout leaves the queue only through a send attempt'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

_GUARD_HEAD = """
CREATE OR REPLACE FUNCTION public.finvest_payouts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
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
            IF NEW.reviewed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a review is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
            -- Released only by the second person, in the transaction where they approve
            -- someone else's proposal to release this very payout.
            IF step = 'held>queued' AND NOT EXISTS (
                SELECT FROM public.payout_releases
                 WHERE payout_id = NEW.id AND status = 'approved'
                   AND decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a held payout is released only with an approved release'
                    USING ERRCODE = '42501';
            END IF;
"""

_GUARD_BANK = """
        ELSIF actor = 'staff' AND step = 'queued>sent' AND NEW.kind = 'bank' THEN
            -- Sent from the trust account only by the second person, in the transaction where
            -- they approve someone else's batch holding this very payout.
            IF NOT EXISTS (
                SELECT FROM public.payout_bank_batch_items AS item
                  JOIN public.payout_bank_batches AS batch ON batch.id = item.batch_id
                 WHERE item.payout_id = NEW.id AND batch.status = 'approved'
                   AND batch.decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a bank payout is sent only in an approved batch'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'a batch signs the batch, not the review' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND NEW.kind = 'bank' AND step IN ('sent>paid', 'sent>failed') THEN
            -- Settled from the trust statement: only by the second person, in the transaction
            -- where they approve someone else's settlement for this very outcome.
            IF NOT EXISTS (
                SELECT FROM public.payout_bank_settlements
                 WHERE payout_id = NEW.id AND status = 'approved' AND outcome = NEW.status
                   AND decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a bank payout is settled only with an approved settlement'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'a settlement signs the settlement, not the review'
                    USING ERRCODE = '42501';
            END IF;
"""

_GUARD_TAIL = """
        ELSIF actor = 'staff' AND step IN (
            'sent>paid', 'sent>failed', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            -- Settled by hand from the statement: only by the second person, in the transaction
            -- where they approve someone else's proposal for this very outcome.
            IF NOT EXISTS (
                SELECT FROM public.payout_resolutions
                 WHERE payout_id = NEW.id AND status = 'approved' AND outcome = NEW.status
                   AND decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a payout is settled by hand only with an approved resolution'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'a resolution signs the resolution, not the review'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'system' AND {system_kind} step IN (
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

# The worker reports the ioTec provider; it never moves a bank payout, which staff pay.
PAYOUTS_GUARD = (
    _GUARD_HEAD + _GUARD_BANK + _GUARD_TAIL.replace("{system_kind}", "NEW.kind <> 'bank' AND")
)
# The guard as 0038 left it, restored on the way down.
OLD_PAYOUTS_GUARD = _GUARD_HEAD + _GUARD_TAIL.replace("{system_kind} ", "")

STAFF = "public.finvest_actor_kind() = 'staff'"


def _rules(table: str, guard: str) -> list[str]:
    return [
        f"CREATE TRIGGER {table}_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{guard}()",
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION public.{guard}()",
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"REVOKE ALL ON {table} FROM PUBLIC",
        f"GRANT SELECT ON {table} TO {WORKER_ROLE}",
        f"CREATE POLICY {table}_app_read ON {table} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
        f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER_ROLE} USING (true)",
    ]


FUNCTIONS = (
    "finvest_payout_bank_batches_guard()",
    "finvest_payout_bank_batch_items_guard()",
    "finvest_payout_bank_batches_sent()",
    "finvest_payout_bank_settlement_evidence(public.payout_bank_settlements)",
    "finvest_payout_bank_settlements_guard()",
    "finvest_payout_bank_settlements_settled()",
)

RULES = [
    *(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC" for function in FUNCTIONS),
    # The settlement routes ask it first, so a refusal can be named. It answers yes or no.
    "GRANT EXECUTE ON FUNCTION public.finvest_payout_bank_settlement_evidence"
    f"(public.payout_bank_settlements) TO {APP_ROLE}",
    *_rules(B, "finvest_payout_bank_batches_guard"),
    *_rules(I, "finvest_payout_bank_batch_items_guard"),
    *_rules(S, "finvest_payout_bank_settlements_guard"),
    f"CREATE CONSTRAINT TRIGGER {B}_sent AFTER UPDATE ON {B} DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN (NEW.status = 'approved') "
    "EXECUTE FUNCTION public.finvest_payout_bank_batches_sent()",
    f"CREATE CONSTRAINT TRIGGER {S}_settled AFTER UPDATE ON {S} DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN (NEW.status = 'approved') "
    "EXECUTE FUNCTION public.finvest_payout_bank_settlements_settled()",
    f"GRANT SELECT, INSERT ON {B} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason) ON {B} TO {APP_ROLE}",
    f"CREATE POLICY {B}_app_insert ON {B} FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({STAFF} AND prepared_by = public.finvest_actor_id())",
    f"CREATE POLICY {B}_app_update ON {B} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
    f"GRANT SELECT, INSERT ON {I} TO {APP_ROLE}",
    f"CREATE POLICY {I}_app_insert ON {I} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"GRANT SELECT, INSERT ON {S} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason) ON {S} TO {APP_ROLE}",
    f"CREATE POLICY {S}_app_insert ON {S} FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({STAFF} AND proposed_by = public.finvest_actor_id())",
    f"CREATE POLICY {S}_app_update ON {S} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [
        *split(TABLES),
        BATCHES_GUARD,
        ITEMS_GUARD,
        BATCH_SENT_FUNCTION,
        EVIDENCE_FUNCTION,
        SETTLEMENTS_GUARD,
        SETTLED_FUNCTION,
        *RULES,
        NEEDS_A_SEND_ATTEMPT,
        PAYOUTS_GUARD,
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_bank_batches) THEN
        RAISE EXCEPTION 'refusing to drop the batches of bank payouts';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(OLD_PAYOUTS_GUARD)
    op.execute(OLD_NEEDS_A_SEND_ATTEMPT)
    # The evidence function takes a settlement row, so it goes before its table.
    evidence = [function for function in FUNCTIONS if "evidence" in function]
    for function in evidence:
        op.execute(f"DROP FUNCTION public.{function}")
    for table in (S, I, B):
        op.execute(f"DROP TABLE {table}")
    for function in FUNCTIONS:
        if function not in evidence:
            op.execute(f"DROP FUNCTION public.{function}")
